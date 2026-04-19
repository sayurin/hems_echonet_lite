"""Binary sensor platform for the HEMS integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pyhems import EntityDefinition, create_binary_decoder

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import infer_entity_category
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    setup_echonet_lite_platform,
)
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0


# Device class inference based on name keywords.
# Order matters: first match wins. PROBLEM-related phrases come first to avoid
# being shadowed by more generic keywords.
_BINARY_DEVICE_CLASS_KEYWORDS: tuple[tuple[str, BinarySensorDeviceClass], ...] = (
    # PROBLEM variants (includes "Fault status" at EPC 0x88)
    ("fault", BinarySensorDeviceClass.PROBLEM),
    ("abnormal", BinarySensorDeviceClass.PROBLEM),
    ("emergency", BinarySensorDeviceClass.PROBLEM),
    ("exceptional", BinarySensorDeviceClass.PROBLEM),
    ("maintenance", BinarySensorDeviceClass.PROBLEM),
    ("filter change", BinarySensorDeviceClass.PROBLEM),
    # RUNNING (includes "Operation status" at EPC 0x80)
    ("operation status", BinarySensorDeviceClass.RUNNING),
    # HEAT
    ("heating", BinarySensorDeviceClass.HEAT),
    ("heater", BinarySensorDeviceClass.HEAT),
    # OPENING
    ("cover", BinarySensorDeviceClass.OPENING),
    ("removal", BinarySensorDeviceClass.OPENING),
    # OCCUPANCY
    ("occupancy", BinarySensorDeviceClass.OCCUPANCY),
    ("occupant", BinarySensorDeviceClass.OCCUPANCY),
    ("human", BinarySensorDeviceClass.OCCUPANCY),
    # LIGHT
    ("sunlight", BinarySensorDeviceClass.LIGHT),
    # Common HA device_classes matched directly by name
    ("door", BinarySensorDeviceClass.DOOR),
    ("gas", BinarySensorDeviceClass.GAS),
    ("moisture", BinarySensorDeviceClass.MOISTURE),
    ("motion", BinarySensorDeviceClass.MOTION),
    ("smoke", BinarySensorDeviceClass.SMOKE),
    ("window", BinarySensorDeviceClass.WINDOW),
)


def _infer_binary_device_class(
    entity_def: EntityDefinition,
) -> BinarySensorDeviceClass | None:
    """Infer device class for binary sensor from name keywords.

    Args:
        entity_def: Entity definition.

    Returns:
        BinarySensorDeviceClass or None.
    """
    name_lower = entity_def.name_en.lower()
    for keyword, device_class in _BINARY_DEVICE_CLASS_KEYWORDS:
        if keyword in name_lower:
            return device_class

    return None


@dataclass(frozen=True, kw_only=True)
class EchonetLiteBinarySensorEntityDescription(
    BinarySensorEntityDescription, EchonetLiteEntityDescription
):
    """Entity description that tracks EPC metadata."""

    decoder: Callable[[bytes], bool | None]


def _create_binary_sensor_description(
    class_code: int,
    entity_def: EntityDefinition,
) -> EchonetLiteBinarySensorEntityDescription:
    """Create a binary sensor entity description from an EntityDefinition."""
    on_value, _ = entity_def.get_binary_values()

    return EchonetLiteBinarySensorEntityDescription(
        key=f"{entity_def.epc:02x}",
        translation_key=entity_def.id,
        class_code=class_code,
        epc=entity_def.epc,
        device_class=_infer_binary_device_class(entity_def),
        entity_category=infer_entity_category(entity_def),
        decoder=create_binary_decoder(on_value),
        manufacturer_code=entity_def.manufacturer_code,
        fallback_name=entity_def.name_en or None,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite binary sensors from a config entry."""
    setup_echonet_lite_platform(
        entry,
        async_add_entities,
        "binary_sensor",
        _create_binary_sensor_description,
        EchonetLiteBinarySensor,
        "binary_sensor",
    )


class EchonetLiteBinarySensor(
    EchonetLiteDescribedEntity[EchonetLiteBinarySensorEntityDescription],
    BinarySensorEntity,
):
    """Representation of a boolean ECHONET Lite property."""

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        state = self._node.properties.get(self._epc)
        return self.description.decoder(state) if state else None


__all__ = ["EchonetLiteBinarySensor", "EchonetLiteBinarySensorEntityDescription"]
