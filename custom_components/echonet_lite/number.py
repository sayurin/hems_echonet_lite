"""Number platform for the HEMS Echonet Lite integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pyhems import (
    EntityDefinition,
    NodeState,
    create_numeric_decoder,
    create_numeric_encoder,
)

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    infer_device_classes,
    infer_entity_category,
    infer_entity_registry_enabled_default,
    infer_ha_unit,
)
from .coordinator import EchonetLiteCoordinator
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    setup_echonet_lite_platform,
)
from .types import EchonetLiteConfigEntry

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

    decoder: Callable[[bytes], float | int | None]
    encoder: Callable[[float | int], bytes]
    mra_format: str
    scale: float


def _create_number_description(
    class_code: int,
    entity_def: EntityDefinition,
) -> EchonetLiteNumberEntityDescription:
    """Create a number entity description from an EntityDefinition."""
    if entity_def.format is None:
        raise ValueError(
            f"Number entity EPC 0x{entity_def.epc:02X} for class 0x{class_code:04X} "
            "has no format defined"
        )
    return EchonetLiteNumberEntityDescription(
        key=f"{entity_def.epc:02x}_{entity_def.byte_offset}",
        translation_key=entity_def.id,
        class_code=class_code,
        epc=entity_def.epc,
        device_class=_infer_device_class(entity_def),
        entity_category=infer_entity_category(entity_def),
        entity_registry_enabled_default=infer_entity_registry_enabled_default(
            entity_def
        ),
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
        native_step=entity_def.multiple_of if entity_def.multiple_of != 1.0 else None,
        decoder=create_numeric_decoder(
            mra_format=entity_def.format,
            minimum=entity_def.minimum,
            maximum=entity_def.maximum,
            scale=entity_def.multiple_of,
            byte_offset=entity_def.byte_offset,
        ),
        encoder=create_numeric_encoder(
            mra_format=entity_def.format,
            scale=entity_def.multiple_of,
        ),
        mra_format=entity_def.format,
        scale=entity_def.multiple_of,
        manufacturer_code=entity_def.manufacturer_code,
        fallback_name=entity_def.name_en or None,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite number entities from a config entry."""
    setup_echonet_lite_platform(
        entry,
        async_add_entities,
        "number",
        _create_number_description,
        EchonetLiteNumber,
        "number",
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
    def native_value(self) -> float | int | None:
        """Return the current value."""
        state = self._node.properties.get(self._epc)
        return self.description.decoder(state) if state is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the value by sending an ECHONET Lite command."""
        encoded = self.description.encoder(value)
        await self._async_send_property(self._epc, encoded)


__all__ = ["EchonetLiteNumber", "EchonetLiteNumberEntityDescription"]
