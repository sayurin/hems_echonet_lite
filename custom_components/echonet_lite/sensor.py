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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import camel_to_snake, infer_device_classes, infer_ha_unit
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    setup_echonet_lite_platform,
)
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0


def _infer_device_class(
    entity_def: EntityDefinition,
) -> SensorDeviceClass | None:
    """Infer the sensor device class from MRA unit and entity name."""
    return infer_device_classes(entity_def)[0]


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
        value_map = {
            enum_val.edt: camel_to_snake(enum_val.key)
            for enum_val in entity_def.enum_values
        }
        options = list(value_map.values())
        raw_decoder = create_enum_decoder()

        def _enum_sensor_decoder(
            state: bytes,
            *,
            _raw_decoder: Callable[[bytes], int | None] = raw_decoder,
            _value_map: dict[int, str] = value_map,
        ) -> str | None:
            if (raw_value := _raw_decoder(state)) is None:
                return None
            return _value_map.get(raw_value)

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
    if entity_def.format is None:
        raise ValueError(
            f"Numeric sensor EPC 0x{entity_def.epc:02X} for class 0x{class_code:04X} "
            "has no format defined"
        )
    return EchonetLiteSensorEntityDescription(
        key=f"{entity_def.epc:02x}_{entity_def.byte_offset}",
        translation_key=entity_def.id,
        class_code=class_code,
        epc=entity_def.epc,
        device_class=_infer_device_class(entity_def),
        native_unit_of_measurement=infer_ha_unit(entity_def),
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
