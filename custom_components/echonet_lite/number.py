"""Number platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import override

from pyhems import EntityDefinition, NodeState

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import infer_device_classes, infer_ha_unit
from .coordinator import EchonetLiteCoordinator
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    build_platform_descriptions,
    setup_common_platform,
)
from .prop import NumericProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1


def _infer_device_class(
    entity_def: EntityDefinition,
) -> NumberDeviceClass | None:
    """Infer the number device class from MRA unit and entity name."""
    return infer_device_classes(entity_def)[1]


@dataclass(frozen=True, kw_only=True)
class EchonetLiteNumberEntityDescription(
    NumberEntityDescription, EchonetLiteEntityDescription
):
    """Entity description with EPC metadata for number entities."""

    prop: NumericProp

    @classmethod
    @override
    def build_from_entity_def(
        cls, entity_def: EntityDefinition
    ) -> EchonetLiteNumberEntityDescription:
        """Construct a number description from an EntityDefinition."""
        return cls(
            key=f"{entity_def.epc:02x}_{entity_def.byte_offset}",
            device_class=_infer_device_class(entity_def),
            native_unit_of_measurement=infer_ha_unit(entity_def),
            native_min_value=(
                entity_def.minimum * entity_def.multiple_of
                if entity_def.minimum is not None
                else None
            ),
            native_max_value=(
                entity_def.maximum * entity_def.multiple_of
                if entity_def.maximum is not None
                else None
            ),
            native_step=entity_def.multiple_of
            if entity_def.multiple_of != 1.0
            else None,
            prop=NumericProp.from_entity_def(entity_def),
            **cls._common_kwargs(entity_def),
        )


_DESCRIPTIONS: dict[int, list[EchonetLiteNumberEntityDescription]] = (
    build_platform_descriptions(Platform.NUMBER, EchonetLiteNumberEntityDescription)
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite number entities from a config entry."""
    setup_common_platform(
        entry,
        async_add_entities,
        Platform.NUMBER.value,
        _DESCRIPTIONS,
        EchonetLiteNumber,
    )


class EchonetLiteNumber(
    EchonetLiteDescribedEntity[EchonetLiteNumberEntityDescription], NumberEntity
):
    """Representation of a writable ECHONET Lite numeric property."""

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteNumberEntityDescription,
    ) -> None:
        """Initialize the ECHONET Lite number entity."""
        super().__init__(coordinator, node, description)

    @property
    @override
    def native_value(self) -> float | int | None:
        """Return the current value."""
        return self.description.prop.get(self._node)

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set the value by sending an ECHONET Lite command."""
        await self._async_send_prop(self.description.prop, value)
