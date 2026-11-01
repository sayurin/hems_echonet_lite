"""Binary sensor platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import override

from pyhems import EntityDefinition, Indication

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    build_platform_descriptions,
    setup_common_platform,
)
from .prop import BinaryProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0


# pyhems Indication -> binary sensor device class. pyhems derives the
# indication of each boolean property from its name.
_INDICATION_TO_DEVICE_CLASS: dict[Indication, BinarySensorDeviceClass] = {
    Indication.DOOR: BinarySensorDeviceClass.DOOR,
    Indication.GAS: BinarySensorDeviceClass.GAS,
    Indication.HEAT: BinarySensorDeviceClass.HEAT,
    Indication.LIGHT: BinarySensorDeviceClass.LIGHT,
    Indication.MOISTURE: BinarySensorDeviceClass.MOISTURE,
    Indication.MOTION: BinarySensorDeviceClass.MOTION,
    Indication.OCCUPANCY: BinarySensorDeviceClass.OCCUPANCY,
    Indication.OPENING: BinarySensorDeviceClass.OPENING,
    Indication.PROBLEM: BinarySensorDeviceClass.PROBLEM,
    Indication.RUNNING: BinarySensorDeviceClass.RUNNING,
    Indication.SMOKE: BinarySensorDeviceClass.SMOKE,
    Indication.WINDOW: BinarySensorDeviceClass.WINDOW,
}


@dataclass(frozen=True, kw_only=True)
class EchonetLiteBinarySensorEntityDescription(
    BinarySensorEntityDescription, EchonetLiteEntityDescription
):
    """Entity description that tracks EPC metadata."""

    prop: BinaryProp

    @classmethod
    @override
    def build_from_entity_def(
        cls,
        class_code: int,
        entity_def: EntityDefinition,
    ) -> EchonetLiteBinarySensorEntityDescription:
        """Construct a binary sensor description from an EntityDefinition."""
        return cls(
            key=f"{entity_def.epc:02x}",
            device_class=(
                _INDICATION_TO_DEVICE_CLASS[entity_def.indication]
                if entity_def.indication is not None
                else None
            ),
            prop=BinaryProp.from_entity_def(entity_def),
            **cls._common_kwargs(entity_def),
        )


_DESCRIPTIONS: dict[int, list[EchonetLiteBinarySensorEntityDescription]] = (
    build_platform_descriptions(
        Platform.BINARY_SENSOR, EchonetLiteBinarySensorEntityDescription
    )
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite binary sensors from a config entry."""
    setup_common_platform(
        entry,
        async_add_entities,
        Platform.BINARY_SENSOR.value,
        _DESCRIPTIONS,
        EchonetLiteBinarySensor,
    )


class EchonetLiteBinarySensor(
    EchonetLiteDescribedEntity[EchonetLiteBinarySensorEntityDescription],
    BinarySensorEntity,
):
    """Representation of a boolean ECHONET Lite property."""

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        return self.description.prop.get(self._node)
