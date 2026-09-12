"""Constants for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from datetime import timedelta
import re

from pyhems import DeviceClass, EntityDefinition, PropertyRole

from homeassistant.components.number import NumberDeviceClass as NumberDC
from homeassistant.components.sensor import (
    SensorDeviceClass as SensorDC,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    LIGHT_LUX,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfDensity,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfRatio,
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
DEFAULT_INTERFACE = "0.0.0.0"
DEFAULT_POLL_INTERVAL = 60
# Fixed cadence for the high-frequency polling tier (instantaneous values
# such as power consumption). Not user-configurable: HA integrations should
# not expose polling intervals as a setting (see developer docs on polling).
# Kept well below DEFAULT_POLL_INTERVAL so it offers a meaningful
# improvement, but not so low that it floods slow devices; PropertyPoller
# separately folds this back into the normal cadence for devices confirmed
# to be slow (see pyhems.poller).
DEFAULT_FAST_POLL_INTERVAL = 10
ISSUE_RUNTIME_CLIENT_ERROR = "runtime_client_error"
ISSUE_RUNTIME_INACTIVE = "runtime_inactive"
RUNTIME_MONITOR_INTERVAL = timedelta(minutes=1)
# Five minutes corresponds to roughly five missed polling cycles
# (``DEFAULT_POLL_INTERVAL`` = 60s). If either constant changes, revisit this
# ratio so the inactivity issue still trips after several missed polls rather
# than firing on a single transient gap.
RUNTIME_MONITOR_MAX_SILENCE = timedelta(minutes=5)

# ============================================================================
# ECHONET Lite property codes (EPCs) used by this integration
# ============================================================================
# Common (super class, 0x80-0x9F) EPCs.
EPC_OPERATION_STATUS = 0x80
EPC_INSTALLATION_LOCATION = 0x81

# Class-specific EPCs used by the climate / fan platforms.
# 0xA0 has different semantic names per class (fan speed for HOME AC,
# air flow level for ventilation fan / air cleaner). Both aliases are
# defined to keep call sites self-documenting.
EPC_AIR_FLOW_LEVEL = 0xA0
EPC_FAN_SPEED = 0xA0
EPC_SWING_AIR_FLOW = 0xA3
EPC_SPECIAL_STATE = 0xAA
EPC_OPERATION_MODE = 0xB0
EPC_TARGET_TEMPERATURE = 0xB3
EPC_ROOM_HUMIDITY = 0xBA
EPC_ROOM_TEMPERATURE = 0xBB
EPC_MEASURED_WATER_TEMPERATURE = 0xC1

# Class-specific EPCs used by the light platform.
EPC_LIGHT_LEVEL = 0xB0
EPC_LIGHT_COLOR = 0xB1
EPC_LIGHTING_MODE = 0xB6

# Class-specific EPCs used by the cover platform.
EPC_COVER_OPEN_CLOSE = 0xE0
EPC_COVER_POSITION = 0xE1
EPC_COVER_ANGLE = 0xE2
EPC_COVER_OPEN_CLOSED_STATUS = 0xEA

# Class-specific EPCs used by the lock platform.
EPC_LOCK_SETTING_1 = 0xE0
EPC_LOCK_SETTING_2 = 0xE1
EPC_LOCK_ALARM_STATUS = 0xE5

# Class-specific EPCs used by the power distribution board metering
# (branch circuit) collection sensor projections. 0xD0-0xEF (legacy fixed
# 32-channel layout) are intentionally excluded (see
# EXCLUDED_EPCS_BY_CLASS) in favor of the modern array EPCs below.
EPC_SIMPLEX_CUMULATIVE_ENERGY_LIST = 0xB3
EPC_SIMPLEX_INSTANTANEOUS_POWER_LIST = 0xB7
EPC_DUPLEX_CUMULATIVE_ENERGY_LIST = 0xBA
EPC_DUPLEX_INSTANTANEOUS_POWER_LIST = 0xBE
EPC_UNIT_FOR_CUMULATIVE_ELECTRIC_ENERGY = 0xC2

# EPCs owned by dedicated platform entities and excluded from generic platforms
# (sensor/binary_sensor/select/switch) to avoid duplicate entities.
DEDICATED_PLATFORM_EPCS: dict[int, frozenset[int]] = {
    DeviceClass.HOME_AIR_CONDITIONER: frozenset(
        {
            EPC_OPERATION_STATUS,
            EPC_FAN_SPEED,
            EPC_SWING_AIR_FLOW,
            EPC_SPECIAL_STATE,
            EPC_OPERATION_MODE,
            EPC_TARGET_TEMPERATURE,
        }
    ),
    DeviceClass.VENTILATION_FAN: frozenset(
        {
            EPC_OPERATION_STATUS,
            EPC_AIR_FLOW_LEVEL,
        }
    ),
    DeviceClass.AIR_CONDITIONER_VENTILATION_FAN: frozenset(
        {
            EPC_OPERATION_STATUS,
            EPC_AIR_FLOW_LEVEL,
        }
    ),
    DeviceClass.AIR_CLEANER: frozenset(
        {
            EPC_OPERATION_STATUS,
            EPC_AIR_FLOW_LEVEL,
        }
    ),
    DeviceClass.ELECTRIC_WATER_HEATER: frozenset(
        {
            EPC_OPERATION_STATUS,
            EPC_OPERATION_MODE,
            EPC_TARGET_TEMPERATURE,
        }
    ),
    DeviceClass.ELECTRIC_LOCK: frozenset(
        {
            EPC_LOCK_SETTING_1,
            EPC_LOCK_SETTING_2,
            EPC_LOCK_ALARM_STATUS,
        }
    ),
    DeviceClass.ELECTRIC_BLIND_SHADE: frozenset(
        {
            EPC_COVER_OPEN_CLOSE,
            EPC_COVER_POSITION,
            EPC_COVER_ANGLE,
            EPC_COVER_OPEN_CLOSED_STATUS,
        }
    ),
    DeviceClass.ELECTRIC_RAIN_DOOR: frozenset(
        {
            EPC_COVER_OPEN_CLOSE,
            EPC_COVER_POSITION,
            EPC_COVER_ANGLE,
            EPC_COVER_OPEN_CLOSED_STATUS,
        }
    ),
    DeviceClass.MONO_FUNCTIONAL_LIGHTING: frozenset(
        {
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
        }
    ),
    DeviceClass.GENERAL_LIGHTING: frozenset(
        {
            EPC_OPERATION_STATUS,
            EPC_LIGHT_LEVEL,
            EPC_LIGHT_COLOR,
            EPC_LIGHTING_MODE,
        }
    ),
}

# EPCs required by dedicated platform entities for state updates. This is
# separate from DEDICATED_PLATFORM_EPCS because some measured values are also
# intentionally exposed as independent generic sensor entities.
DEDICATED_PLATFORM_REQUIRED_EPCS: dict[int, frozenset[int]] = (
    DEDICATED_PLATFORM_EPCS
    | {
        DeviceClass.HOME_AIR_CONDITIONER: DEDICATED_PLATFORM_EPCS[
            DeviceClass.HOME_AIR_CONDITIONER
        ]
        | frozenset({EPC_ROOM_HUMIDITY, EPC_ROOM_TEMPERATURE}),
        DeviceClass.ELECTRIC_WATER_HEATER: DEDICATED_PLATFORM_EPCS[
            DeviceClass.ELECTRIC_WATER_HEATER
        ]
        | frozenset({EPC_MEASURED_WATER_TEMPERATURE}),
    }
)

# EPCs permanently excluded from monitored/fast-poll EPC sets and from
# scalar entity generation, per device class code. Unlike
# DEDICATED_PLATFORM_EPCS (owned by another platform), these EPCs are not
# used by this integration at all.
#
# Class 0x0287 (power distribution board metering) EPCs 0xD0-0xEF are the
# legacy fixed 32-channel measurement layout, superseded by the modern
# array EPCs (0xB1/0xB3/0xB7/0xB8/0xBA/0xBE, see
# COLLECTION_SENSOR_PROJECTIONS below). Including both would create up to
# 96 redundant entities and risk oversized Get requests being split across
# frames; see docs/ha-0287-epc-be-implementation-report-v2.md section 6.2.
EXCLUDED_EPCS_BY_CLASS: dict[int, frozenset[int]] = {
    DeviceClass.POWER_DISTRIBUTION_BOARD_METERING: frozenset(range(0xD0, 0xF0)),
}


# ============================================================================
# Collection (paged list) sensor projections
# ============================================================================
# Data-driven HA sensor projections for MRA properties that describe a
# variable-length list rather than a single scalar value (see
# pyhems.definitions.CollectionBinding / PropertyValueDefinition). Unlike
# DEDICATED_PLATFORM_EPCS, entity creation for these EPCs is dynamic
# (one entity per channel, per node) — see sensor.py's collection entity
# factory — since the channel count is only known once the sibling "count"
# EPC (e.g. 0xB1/0xB8) has been read from the device.
#
# Adding a projection here is the *only* class-specific step for a new
# collection EPC: the decode path (pyhems.decode_collection_page) and the HA
# entity factory (sensor.py) are both fully generic.


@dataclass(frozen=True, kw_only=True)
class CollectionFieldProjection:
    """HA projection metadata for one field decoded from a collection item.

    Attributes:
        item_field: The item's ObjectField key (see pyhems
          ``CollectionBinding.items_path``), or ``None`` when items decode to
          a bare scalar rather than an object (e.g. instantaneous power).
        translation_key: Shared translation key (see ``strings.json``
          ``common`` section) used by every channel entity for this field;
          the channel number is passed via ``translation_placeholders``.
        device_class: HA sensor device class.
        state_class: HA sensor state class.
        unit: Native unit of measurement.
        unique_id_suffix: Suffix appended after the channel number in the
          entity's unique_id (e.g. ``"instantaneous_power"``).
    """

    item_field: str | None
    translation_key: str
    device_class: SensorDC
    state_class: SensorStateClass
    unit: str
    unique_id_suffix: str


@dataclass(frozen=True, kw_only=True)
class CollectionSensorProjection:
    """Data-driven HA sensor projection for one collection (list) property.

    Attributes:
        class_code: ECHONET Lite class code.
        result_epc: EPC of the paged list result. Must match a curated
          ``pyhems.CollectionBinding.result_epc`` for the same class_code.
        max_exposed_items: Maximum number of channel entities to create,
          independent of the MRA per-page item limit or the device's actual
          channel count (see
          docs/ha-0287-epc-be-implementation-report-v2.md section 3).
        unique_id_prefix: Prefix for each channel entity's unique_id, before
          the channel number (e.g. ``"simplex_channel"``).
        fields: One or more fields decoded from each item (more than one for
          object items, e.g. forward/reverse cumulative energy).
        coefficient_epcs: Sibling EPCs (MRA ``coefficient``) item values in
          this list depend on (e.g. 0xC2 for cumulative energy).
        fast_poll: Whether ``result_epc`` should be polled at the
          high-frequency cadence (instantaneous power lists), matching
          ``PropertyRole.INSTANTANEOUS`` for scalar entities.
    """

    class_code: int
    result_epc: int
    max_exposed_items: int
    unique_id_prefix: str
    fields: tuple[CollectionFieldProjection, ...]
    coefficient_epcs: tuple[int, ...] = ()
    fast_poll: bool = False


COLLECTION_SENSOR_PROJECTIONS: tuple[CollectionSensorProjection, ...] = (
    CollectionSensorProjection(
        class_code=DeviceClass.POWER_DISTRIBUTION_BOARD_METERING,
        result_epc=EPC_SIMPLEX_INSTANTANEOUS_POWER_LIST,
        max_exposed_items=60,
        unique_id_prefix="simplex_channel",
        fast_poll=True,
        fields=(
            CollectionFieldProjection(
                item_field=None,
                translation_key="simplex_instantaneous_power",
                device_class=SensorDC.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                unit=UnitOfPower.WATT,
                unique_id_suffix="instantaneous_power",
            ),
        ),
    ),
    CollectionSensorProjection(
        class_code=DeviceClass.POWER_DISTRIBUTION_BOARD_METERING,
        result_epc=EPC_DUPLEX_INSTANTANEOUS_POWER_LIST,
        max_exposed_items=60,
        unique_id_prefix="duplex_channel",
        fast_poll=True,
        fields=(
            CollectionFieldProjection(
                item_field=None,
                translation_key="duplex_instantaneous_power",
                device_class=SensorDC.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                unit=UnitOfPower.WATT,
                unique_id_suffix="instantaneous_power",
            ),
        ),
    ),
    CollectionSensorProjection(
        class_code=DeviceClass.POWER_DISTRIBUTION_BOARD_METERING,
        result_epc=EPC_SIMPLEX_CUMULATIVE_ENERGY_LIST,
        max_exposed_items=60,
        unique_id_prefix="simplex_channel",
        coefficient_epcs=(EPC_UNIT_FOR_CUMULATIVE_ELECTRIC_ENERGY,),
        fields=(
            CollectionFieldProjection(
                item_field=None,
                translation_key="cumulative_energy",
                device_class=SensorDC.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                unit=UnitOfEnergy.KILO_WATT_HOUR,
                unique_id_suffix="cumulative_energy",
            ),
        ),
    ),
    CollectionSensorProjection(
        class_code=DeviceClass.POWER_DISTRIBUTION_BOARD_METERING,
        result_epc=EPC_DUPLEX_CUMULATIVE_ENERGY_LIST,
        max_exposed_items=30,
        unique_id_prefix="duplex_channel",
        coefficient_epcs=(EPC_UNIT_FOR_CUMULATIVE_ELECTRIC_ENERGY,),
        fields=(
            CollectionFieldProjection(
                item_field="normalDirectionElectricEnergy",
                translation_key="cumulative_energy_forward",
                device_class=SensorDC.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                unit=UnitOfEnergy.KILO_WATT_HOUR,
                unique_id_suffix="cumulative_energy_forward",
            ),
            CollectionFieldProjection(
                item_field="reverseDirectionElectricEnergy",
                translation_key="cumulative_energy_reverse",
                device_class=SensorDC.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                unit=UnitOfEnergy.KILO_WATT_HOUR,
                unique_id_suffix="cumulative_energy_reverse",
            ),
        ),
    ),
)


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case.

    MRA enum names use camelCase (e.g., 'automaticAirFlowDirection').
    HA uses snake_case for state keys (e.g., 'automatic_air_flow_direction').
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# ============================================================================
# Installation location (EPC 0x81) — integration-side option keys
# ============================================================================
# The 1-byte format and the standard LLLL labels live in
# :mod:`pyhems.installation_location` and are reused by the select platform,
# the device-info helper, and the strings generator. The constants below are
# integration-only additions that surface alongside the spec values.

# Option key written to EPC 0x81 as 0x00 (LLLL=0, NNN=0).
INSTALLATION_LOCATION_UNSET = "unset"

# NNN (bits 2-0) option keys; "0" is displayed as "Unset".
INSTALLATION_LOCATION_NUMBER_OPTIONS: tuple[str, ...] = tuple(str(n) for n in range(8))


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
    "%": UnitOfRatio.PERCENTAGE,
    "%RH": UnitOfRatio.PERCENTAGE,
    "A": UnitOfElectricCurrent.AMPERE,
    "mA": UnitOfElectricCurrent.MILLIAMPERE,
    "V": UnitOfElectricPotential.VOLT,
    "ppm": UnitOfRatio.PARTS_PER_MILLION,
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
    "µg/m³": UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
    "mHz": UnitOfFrequency.MILLIHERTZ,
    # No HA equivalent — MRA string is used as-is.
    "Ah": None,
    "digit": None,
    "klux": None,
    "W/mHz": None,
    "W/sec": None,
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
    return (
        MRA_UNIT_TO_HA_UNIT.get(unit, unit) or unit
    )  # pragma: no cover - safety net for unmapped MRA units


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
    return None, None  # pragma: no cover - safety net for unmapped MRA units


# ============================================================================
# EntityCategory inference
# ============================================================================
# pyhems curates PropertyRole per (class_code, epc) in
# scripts/property_roles.xlsx (MRA properties) and scripts/custom_definitions.
# yaml (custom entries); this integration only maps that role to the
# corresponding HA EntityCategory. INSTANTANEOUS is still a primary,
# user-facing value (just fast-changing), so it maps to ``None`` like
# PRIMARY; it is used for fast-poll selection in ``__init__.py`` instead.
_ROLE_TO_ENTITY_CATEGORY: dict[PropertyRole, EntityCategory | None] = {
    PropertyRole.PRIMARY: None,
    PropertyRole.INSTANTANEOUS: None,
    PropertyRole.SETTING: EntityCategory.CONFIG,
    PropertyRole.STATUS: EntityCategory.DIAGNOSTIC,
    PropertyRole.SPECIFICATION: EntityCategory.DIAGNOSTIC,
}


def get_entity_category(entity_def: EntityDefinition) -> EntityCategory | None:
    """Return the HA entity category for ``entity_def``'s curated role."""
    return _ROLE_TO_ENTITY_CATEGORY[entity_def.role]
