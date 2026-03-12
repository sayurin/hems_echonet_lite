"""Sensor platform for the HEMS integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pyhems import EntityDefinition, create_enum_decoder, create_numeric_decoder

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import camel_to_snake
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    setup_echonet_lite_platform,
)
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0

# Mapping from MRA unit to (device_class, ha_unit)
# device_class is inferred from the MRA unit
_MRA_UNIT_TO_HA: dict[str, tuple[SensorDeviceClass | None, str | None]] = {
    "W": (SensorDeviceClass.POWER, UnitOfPower.WATT),
    "kW": (SensorDeviceClass.POWER, UnitOfPower.KILO_WATT),
    "Wh": (SensorDeviceClass.ENERGY, UnitOfEnergy.WATT_HOUR),
    "kWh": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "Celsius": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
    "%": (None, PERCENTAGE),  # device_class determined by context
    "%RH": (SensorDeviceClass.HUMIDITY, PERCENTAGE),
    "A": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE),
    "mA": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.MILLIAMPERE),
    "V": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    "ppm": (SensorDeviceClass.CO2, "ppm"),
    "lux": (SensorDeviceClass.ILLUMINANCE, "lx"),
    "klux": (SensorDeviceClass.ILLUMINANCE, "lx"),  # Convert to lx
    "dB": (SensorDeviceClass.SOUND_PRESSURE, "dB"),
    "m/s": (SensorDeviceClass.WIND_SPEED, "m/s"),
}

# Context-specific device_class overrides for % unit
_PERCENTAGE_DEVICE_CLASS_KEYWORDS: dict[str, SensorDeviceClass] = {
    "humidity": SensorDeviceClass.HUMIDITY,
    "battery": SensorDeviceClass.BATTERY,
    "remaining": SensorDeviceClass.BATTERY,
    "soc": SensorDeviceClass.BATTERY,
}


def _infer_device_class(
    entity_def: EntityDefinition,
) -> SensorDeviceClass | None:
    """Infer device class from MRA unit and entity name.

    Args:
        entity_def: Entity definition with MRA data.

    Returns:
        SensorDeviceClass or None if cannot be inferred.
    """
    unit = entity_def.unit
    if not unit:
        return None

    unit_info = _MRA_UNIT_TO_HA.get(unit)
    if not unit_info:
        return None

    device_class, _ = unit_info

    # Context-specific inference for % unit
    if unit == "%" and device_class is None:
        name_lower = entity_def.name_en.lower()
        for keyword, dc in _PERCENTAGE_DEVICE_CLASS_KEYWORDS.items():
            if keyword in name_lower:
                return dc

    return device_class


def _infer_ha_unit(entity_def: EntityDefinition) -> str | None:
    """Infer HA unit from MRA unit.

    Args:
        entity_def: Entity definition with MRA unit.

    Returns:
        HA unit string or None.
    """
    unit = entity_def.unit
    if not unit:
        return None

    unit_info = _MRA_UNIT_TO_HA.get(unit)
    if unit_info:
        return unit_info[1]

    # Return MRA unit as-is for units not in mapping
    return unit


def _infer_state_class(entity_def: EntityDefinition) -> SensorStateClass:
    """Infer state class from entity name.

    Args:
        entity_def: Entity definition with name.

    Returns:
        SensorStateClass (measurement or total_increasing).
    """
    name_lower = entity_def.name_en.lower()
    if "cumulative" in name_lower:
        return SensorStateClass.TOTAL_INCREASING
    return SensorStateClass.MEASUREMENT


@dataclass(frozen=True, kw_only=True)
class EchonetLiteSensorEntityDescription(
    SensorEntityDescription, EchonetLiteEntityDescription
):
    """Entity description with EPC metadata."""

    decoder: Callable[[bytes], float | int | str | None]


def _create_sensor_description(
    class_code: int,
    entity_def: EntityDefinition,
) -> EchonetLiteSensorEntityDescription:
    """Create a sensor entity description from an EntityDefinition."""
    # Read-only multi-value enum → ENUM sensor
    if entity_def.enum_values:
        value_map = {ev.edt: camel_to_snake(ev.key) for ev in entity_def.enum_values}
        options = list(value_map.values())
        raw_decoder = create_enum_decoder()

        def _enum_sensor_decoder(
            state: bytes,
            *,
            _raw: Callable[[bytes], int | None] = raw_decoder,
            _map: dict[int, str] = value_map,
        ) -> str | None:
            if (val := _raw(state)) is None:
                return None
            return _map.get(val)

        return EchonetLiteSensorEntityDescription(
            key=f"{entity_def.epc:02x}",
            translation_key=entity_def.id,
            class_code=class_code,
            epc=entity_def.epc,
            device_class=SensorDeviceClass.ENUM,
            options=options,
            decoder=_enum_sensor_decoder,
            manufacturer_code=entity_def.manufacturer_code,
            fallback_name=entity_def.name_en or None,
        )

    # Numeric sensor
    assert entity_def.format is not None
    return EchonetLiteSensorEntityDescription(
        key=f"{entity_def.epc:02x}_{entity_def.byte_offset}",
        translation_key=entity_def.id,
        class_code=class_code,
        epc=entity_def.epc,
        device_class=_infer_device_class(entity_def),
        native_unit_of_measurement=_infer_ha_unit(entity_def),
        state_class=_infer_state_class(entity_def),
        decoder=create_numeric_decoder(
            mra_format=entity_def.format,
            minimum=entity_def.minimum,
            maximum=entity_def.maximum,
            scale=entity_def.multiple_of,
            byte_offset=entity_def.byte_offset,
        ),
        manufacturer_code=entity_def.manufacturer_code,
        fallback_name=entity_def.name_en or None,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite sensors from a config entry."""
    setup_echonet_lite_platform(
        entry,
        async_add_entities,
        "sensor",
        _create_sensor_description,
        EchonetLiteSensor,
        "sensor",
    )


class EchonetLiteSensor(
    EchonetLiteDescribedEntity[EchonetLiteSensorEntityDescription], SensorEntity
):
    """Representation of an ECHONET Lite sensor property."""

    @property
    def native_value(self) -> float | int | str | None:
        """Return the state of the sensor."""
        state = self._node.properties.get(self._epc)
        return self.description.decoder(state) if state is not None else None


__all__ = ["EchonetLiteSensor", "EchonetLiteSensorEntityDescription"]
