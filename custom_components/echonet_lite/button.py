"""Button platform for the HEMS Echonet Lite integration."""

from __future__ import annotations

from dataclasses import dataclass

from pyhems import EntityDefinition

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import infer_entity_category, infer_entity_registry_enabled_default
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    setup_echonet_lite_platform,
)
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EchonetLiteButtonEntityDescription(
    ButtonEntityDescription, EchonetLiteEntityDescription
):
    """Entity description that also stores EPC metadata."""

    press_value: bytes  # Byte value to send when button is pressed


def _create_button_description(
    class_code: int,
    entity_def: EntityDefinition,
) -> EchonetLiteButtonEntityDescription:
    """Create a button entity description from an EntityDefinition.

    For write-only properties, the button press sends the first enum value's EDT.

    Args:
        class_code: ECHONET Lite class code
        entity_def: Entity definition from pyhems

    Returns:
        Button entity description

    Raises:
        ValueError: If entity has no enum values (unsupported for button)
    """
    if not entity_def.enum_values:
        raise ValueError(
            f"Button entity requires enum values, but {entity_def.id} has none"
        )

    # Use the first enum value's EDT as the press value
    press_edt = entity_def.enum_values[0].edt
    press_value = press_edt.to_bytes(1, "big")

    return EchonetLiteButtonEntityDescription(
        key=f"{entity_def.epc:02x}",
        translation_key=entity_def.id,
        class_code=class_code,
        epc=entity_def.epc,
        device_class=None,
        entity_category=infer_entity_category(entity_def),
        entity_registry_enabled_default=infer_entity_registry_enabled_default(
            entity_def
        ),
        press_value=press_value,
        manufacturer_code=entity_def.manufacturer_code,
        fallback_name=entity_def.name_en or None,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite buttons from a config entry."""
    setup_echonet_lite_platform(
        entry,
        async_add_entities,
        "button",
        _create_button_description,
        EchonetLiteButton,
        "button",
    )


class EchonetLiteButton(
    EchonetLiteDescribedEntity[EchonetLiteButtonEntityDescription], ButtonEntity
):
    """Representation of a write-only ECHONET Lite property as a button.

    Button entities are used for write-only properties that represent one-shot
    commands (e.g., reset operations). The button press sends a predefined EDT
    value via ECHONET Lite SetC command.
    """

    async def async_press(self) -> None:
        """Send the button press command via the pyhems runtime client."""
        await self._async_send_property(self._epc, self.description.press_value)


__all__ = ["EchonetLiteButton"]
