"""Constants for the HEMS Echonet Lite integration."""

from __future__ import annotations

from datetime import timedelta
import re

from pyhems import (
    CLASS_CODE_AIR_CLEANER,
    CLASS_CODE_AIR_CONDITIONER_VENTILATION_FAN,
    CLASS_CODE_HOME_AIR_CONDITIONER,
    CLASS_CODE_VENTILATION_FAN,
    EntityDefinition,
)

from homeassistant.components.number import NumberDeviceClass as NumberDC
from homeassistant.components.sensor import SensorDeviceClass as SensorDC
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    DEGREE,
    LIGHT_LUX,
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfSoundPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)

DOMAIN = "echonet_lite"
ATTR_EPC = "epc"
CONF_INTERFACE = "interface"
CONF_ENABLE_EXPERIMENTAL = "enable_experimental"
DEFAULT_INTERFACE = "0.0.0.0"
DEFAULT_POLL_INTERVAL = 60
ISSUE_RUNTIME_CLIENT_ERROR = "runtime_client_error"
ISSUE_RUNTIME_INACTIVE = "runtime_inactive"
RUNTIME_MONITOR_INTERVAL = timedelta(minutes=1)
RUNTIME_MONITOR_MAX_SILENCE = timedelta(minutes=5)
DISCOVERY_INTERVAL = 60.0 * 60.0  # 1 hour

# Stable (non-experimental) device class codes
# These device classes have been verified with real hardware.
# Other device classes are considered experimental.
STABLE_CLASS_CODES: frozenset[int] = frozenset(
    {
        0x0130,  # Home air conditioner
        0x0135,  # Air cleaner
        0x0279,  # Fuel cell (residential solar power generation)
        0x027D,  # In-house power generation (storage battery)
        0x05FD,  # Switch (supporting JEM-A/HA terminals)
        0x05FF,  # Controller
    }
)

# EPCs managed by dedicated platform entities (climate, fan)
# - Excluded from other platforms (sensor/binary_sensor/select/switch) to avoid duplicates
# - Used for polling/notification to keep entity state up-to-date
DEDICATED_PLATFORM_EPCS: dict[int, frozenset[int]] = {
    CLASS_CODE_HOME_AIR_CONDITIONER: frozenset(
        {
            0x80,  # Operation status (on/off)
            0xA0,  # Fan speed
            0xA3,  # Swing mode
            0xAA,  # Special state (defrosting/preheating/heat removal)
            0xB0,  # HVAC mode
            0xB3,  # Target temperature
        }
    ),
    CLASS_CODE_VENTILATION_FAN: frozenset(
        {
            0x80,  # Operation status (on/off)
            0xA0,  # Air flow rate setting
        }
    ),
    CLASS_CODE_AIR_CONDITIONER_VENTILATION_FAN: frozenset(
        {
            0x80,  # Operation status (on/off)
            0xA0,  # Air flow rate setting
        }
    ),
    CLASS_CODE_AIR_CLEANER: frozenset(
        {
            0x80,  # Operation status (on/off)
            0xA0,  # Air flow rate setting
        }
    ),
}


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case.

    MRA enum names use camelCase (e.g., 'automaticAirFlowDirection').
    HA uses snake_case for state keys (e.g., 'automatic_air_flow_direction').
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# ============================================================================
# MRA unit -> Home Assistant unit / device class tables
# ============================================================================
# These three tables drive how pyhems' MRA (machine readable appendix) units
# map to Home Assistant's runtime unit strings and device classes for the
# sensor and number platforms. They live here, side-by-side, so adding a new
# MRA unit is a single-file change.

# MRA unit -> HA unit string. A value of ``None`` means HA has no matching
# constant; the MRA string is used verbatim so the unit still appears in the
# UI. Every unit produced by pyhems must appear here; the
# ``test_all_pyhems_units_are_handled`` test enforces this.
MRA_UNIT_TO_HA_UNIT: dict[str, str | None] = {
    "W": UnitOfPower.WATT,
    "kW": UnitOfPower.KILO_WATT,
    "Wh": UnitOfEnergy.WATT_HOUR,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "MJ": UnitOfEnergy.MEGA_JOULE,
    "Celsius": UnitOfTemperature.CELSIUS,
    "%": PERCENTAGE,
    "%RH": PERCENTAGE,
    "A": UnitOfElectricCurrent.AMPERE,
    "mA": UnitOfElectricCurrent.MILLIAMPERE,
    "V": UnitOfElectricPotential.VOLT,
    "ppm": CONCENTRATION_PARTS_PER_MILLION,
    "lux": LIGHT_LUX,
    "dB": UnitOfSoundPressure.DECIBEL,
    "m/s": UnitOfSpeed.METERS_PER_SECOND,
    "L": UnitOfVolume.LITERS,
    "m3": UnitOfVolume.CUBIC_METERS,
    "m3/h": UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    "second": UnitOfTime.SECONDS,
    "days": UnitOfTime.DAYS,
    "ms": UnitOfTime.MILLISECONDS,
    "degree": DEGREE,
    "r/min": REVOLUTIONS_PER_MINUTE,
    "µg/m³": CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    # No HA equivalent — MRA string is used as-is.
    "mHz": None,  # Will added in HA 2026.5
    "Ah": None,
    "digit": None,
    "klux": None,
}


def infer_ha_unit(entity_def: EntityDefinition) -> str | None:
    """Return the HA unit string for ``entity_def`` or ``None``.

    Units with a ``None`` mapping (no HA constant) fall through to the MRA
    string unchanged. Units not listed in :data:`MRA_UNIT_TO_HA_UNIT` also
    fall through, but the coverage test prevents that in practice.
    """
    unit = entity_def.unit
    if not unit:
        return None
    return MRA_UNIT_TO_HA_UNIT.get(unit, unit) or unit


# Unit -> device class rules, shared between the sensor and number platforms.
#
# Each entry is ``((units, ...), ((keyword, sensor_dc, number_dc), ...))``:
# for each unit, rules are tried in order and the first keyword matched in
# the entity's English name wins. An empty keyword ``""`` matches
# unconditionally and acts as a catch-all default. If no rule matches the
# entity's unit or name, ``(None, None)`` is returned (no device class).
#
# Either column may be ``None`` for units that only make sense as a sensor
# (e.g. ``ppm``/``lux``/``µg/m³``) or have no matching NumberDC.
UNIT_DEVICE_CLASS_RULES: tuple[
    tuple[
        tuple[str, ...],
        tuple[tuple[str, SensorDC | None, NumberDC | None], ...],
    ],
    ...,
] = (
    (("W", "kW"), (("", SensorDC.POWER, NumberDC.POWER),)),
    (("Celsius",), (("", SensorDC.TEMPERATURE, NumberDC.TEMPERATURE),)),
    (("%RH",), (("", SensorDC.HUMIDITY, NumberDC.HUMIDITY),)),
    (("A", "mA"), (("", SensorDC.CURRENT, NumberDC.CURRENT),)),
    (("V",), (("", SensorDC.VOLTAGE, NumberDC.VOLTAGE),)),
    (("ppm",), (("", SensorDC.CO2, None),)),
    (("lux",), (("", SensorDC.ILLUMINANCE, None),)),
    (("dB",), (("", SensorDC.SOUND_PRESSURE, None),)),
    (("m/s",), (("", SensorDC.WIND_SPEED, None),)),
    (("m3/h",), (("", SensorDC.VOLUME_FLOW_RATE, NumberDC.VOLUME_FLOW_RATE),)),
    (("second", "days"), (("", SensorDC.DURATION, NumberDC.DURATION),)),
    (
        ("%",),
        (
            ("humidity", SensorDC.HUMIDITY, NumberDC.HUMIDITY),
            ("battery", SensorDC.BATTERY, NumberDC.BATTERY),
            # Number entities never reach these sensor-only keywords, but
            # keeping both columns in the same row keeps the table flat.
            ("remaining", SensorDC.BATTERY, None),
            ("soc", SensorDC.BATTERY, None),
            ("moisture", SensorDC.MOISTURE, NumberDC.MOISTURE),
        ),
    ),
    (
        ("Wh", "kWh", "MJ"),
        (
            # Static ratings (e.g. "AC chargeable capacity") don't fit
            # measurement device classes.
            ("capacity", None, None),
            ("stored", SensorDC.ENERGY_STORAGE, NumberDC.ENERGY_STORAGE),
            ("", SensorDC.ENERGY, NumberDC.ENERGY),
        ),
    ),
    (
        ("L",),
        (
            # Static tank capacity is not a variable measurement.
            ("capacity", None, None),
            ("tank", SensorDC.VOLUME_STORAGE, NumberDC.VOLUME_STORAGE),
            ("remaining", SensorDC.VOLUME_STORAGE, NumberDC.VOLUME_STORAGE),
            ("", SensorDC.WATER, NumberDC.WATER),
        ),
    ),
    (
        ("m3",),
        (
            ("gas", SensorDC.GAS, NumberDC.GAS),
            ("water", SensorDC.WATER, NumberDC.WATER),
            ("", SensorDC.VOLUME, NumberDC.VOLUME),
        ),
    ),
    (
        ("µg/m³",),
        (
            ("pm2.5", SensorDC.PM25, None),
            ("pm25", SensorDC.PM25, None),
        ),
    ),
)


def infer_device_classes(
    entity_def: EntityDefinition,
) -> tuple[SensorDC | None, NumberDC | None]:
    """Return the ``(sensor_dc, number_dc)`` tuple for ``entity_def``.

    See :data:`UNIT_DEVICE_CLASS_RULES` for the rule format. Returns
    ``(None, None)`` when the unit is unknown or no rule matches the entity's
    English name.
    """
    unit = entity_def.unit
    if not unit:
        return None, None
    name_lower = entity_def.name_en.lower()
    for units, rules in UNIT_DEVICE_CLASS_RULES:
        if unit not in units:
            continue
        for keyword, sensor_dc, number_dc in rules:
            if keyword == "" or keyword in name_lower:
                return sensor_dc, number_dc
        return None, None
    return None, None


# ============================================================================
# EntityCategory inference
# ============================================================================
# Home Assistant distinguishes three tiers:
# - DIAGNOSTIC: fault / error / cumulative counters / identification, etc.
# - CONFIG: writable settings (thresholds, schedules, reservations, ...)
# - None: primary user-facing entities (e.g. temperature, power reading)
#
# Only the standardized common EPCs (0x80-0x9F) are classified here via the
# explicit ``ENTITY_CATEGORY_BY_EPC`` map. Device-specific EPCs (0xA0-0xEF)
# are intentionally left uncategorized because their meaning varies per
# device class and keyword-based inference is too error-prone.

# EPC -> EntityCategory for the standardized common EPCs (0x80-0x9F).
ENTITY_CATEGORY_BY_EPC: dict[int, EntityCategory] = {
    # DIAGNOSTIC: fault / identification
    0x86: EntityCategory.DIAGNOSTIC,  # Manufacturer fault code
    0x88: EntityCategory.DIAGNOSTIC,  # Fault status
    0x89: EntityCategory.DIAGNOSTIC,  # Fault description
    0x9A: EntityCategory.DIAGNOSTIC,  # Cumulative operating time
    # CONFIG: installation / settings
    0x81: EntityCategory.CONFIG,  # Installation location
    0x87: EntityCategory.CONFIG,  # Current limit setting
    0x8F: EntityCategory.CONFIG,  # Power saving operation setting
    0x93: EntityCategory.CONFIG,  # Remote control setting
    0x97: EntityCategory.CONFIG,  # Current time setting
    0x98: EntityCategory.CONFIG,  # Current date setting
    0x99: EntityCategory.CONFIG,  # Power limit setting
}


def infer_entity_category(
    entity_def: EntityDefinition,
) -> EntityCategory | None:
    """Return the :class:`EntityCategory` for ``entity_def`` or ``None``.

    Classification is driven solely by :data:`ENTITY_CATEGORY_BY_EPC`, which
    covers the standardized common EPCs (0x80-0x9F). Any other EPC returns
    ``None`` (primary user-facing entity).
    """
    return ENTITY_CATEGORY_BY_EPC.get(entity_def.epc)


def infer_entity_registry_enabled_default(
    entity_def: EntityDefinition,
) -> bool:
    """Return the default enabled state for ``entity_def`` in the registry.

    Diagnostic entities (fault codes, fault status, cumulative operating time,
    ...) are disabled by default so they do not clutter the UI and do not
    grow the recorder database. Users can opt in via the entity registry when
    the value is needed. This mirrors the convention used by other Home
    Assistant integrations for diagnostic entities.
    """
    return infer_entity_category(entity_def) is not EntityCategory.DIAGNOSTIC


__all__ = [
    "CONF_ENABLE_EXPERIMENTAL",
    "CONF_INTERFACE",
    "DEDICATED_PLATFORM_EPCS",
    "DEFAULT_INTERFACE",
    "DEFAULT_POLL_INTERVAL",
    "DISCOVERY_INTERVAL",
    "DOMAIN",
    "ENTITY_CATEGORY_BY_EPC",
    "ISSUE_RUNTIME_CLIENT_ERROR",
    "ISSUE_RUNTIME_INACTIVE",
    "MRA_UNIT_TO_HA_UNIT",
    "RUNTIME_MONITOR_INTERVAL",
    "RUNTIME_MONITOR_MAX_SILENCE",
    "STABLE_CLASS_CODES",
    "UNIT_DEVICE_CLASS_RULES",
    "camel_to_snake",
    "infer_device_classes",
    "infer_entity_category",
    "infer_entity_registry_enabled_default",
    "infer_ha_unit",
]
