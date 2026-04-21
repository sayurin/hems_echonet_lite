"""Select platform for the HEMS Echonet Lite integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pyhems import EntityDefinition, NodeState, create_enum_decoder

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, camel_to_snake, infer_entity_category
from .coordinator import EchonetLiteCoordinator
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    setup_echonet_lite_platform,
)
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EchonetLiteSelectEntityDescription(
    SelectEntityDescription, EchonetLiteEntityDescription
):
    """Entity description that stores EPC metadata and value mapping."""

    decoder: Callable[[bytes], int | None]
    value_to_option: dict[int, str]
    option_to_value: dict[str, int]


def _create_select_description(
    class_code: int,
    entity_def: EntityDefinition,
) -> EchonetLiteSelectEntityDescription:
    """Create a select entity description from an EntityDefinition.

    All select entities in definitions.json are validated to have enum_values,
    so this function always returns a valid description.
    """
    value_to_option: dict[int, str] = {}
    option_to_value: dict[str, int] = {}

    # enum_values is tuple[EnumValue, ...] with edt, key, name_en, name_ja
    for enum_val in entity_def.enum_values:
        option_key = camel_to_snake(enum_val.key)
        value_to_option[enum_val.edt] = option_key
        option_to_value[option_key] = enum_val.edt

    if not option_to_value:
        raise ValueError(
            f"Select entity EPC 0x{entity_def.epc:02X} for class 0x{class_code:04X} "
            "has no valid enum values - this should be caught during generation"
        )

    return EchonetLiteSelectEntityDescription(
        key=f"{entity_def.epc:02x}",
        translation_key=entity_def.id,
        class_code=class_code,
        epc=entity_def.epc,
        entity_category=infer_entity_category(entity_def),
        decoder=create_enum_decoder(),
        value_to_option=value_to_option,
        option_to_value=option_to_value,
        manufacturer_code=entity_def.manufacturer_code,
        fallback_name=entity_def.name_en or None,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite select entities from a config entry."""
    setup_echonet_lite_platform(
        entry,
        async_add_entities,
        "select",
        _create_select_description,
        EchonetLiteSelect,
        "select",
    )


class EchonetLiteSelect(
    EchonetLiteDescribedEntity[EchonetLiteSelectEntityDescription], SelectEntity
):
    """Representation of a writable ECHONET Lite select property."""

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteSelectEntityDescription,
    ) -> None:
        """Initialize the ECHONET Lite select entity."""
        super().__init__(coordinator, node, description)
        self._attr_options = list(description.option_to_value)

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option or None if unset.

        The raw property value is decoded and mapped to the option name.
        """
        if (state := self._node.properties.get(self._epc)) is None:
            return None
        if (value := self.description.decoder(state)) is None:
            return None
        return self.description.value_to_option.get(value)

    async def async_select_option(self, option: str) -> None:
        """Select the given option by sending the corresponding payload."""
        if (value := self.description.option_to_value.get(option)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_option",
                translation_placeholders={"option": option},
            )
        await self._async_send_property(self._epc, bytes([value]))
