"""Button platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import override

from pyhems import EntityDefinition

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    build_platform_descriptions,
    setup_common_platform,
)
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class EchonetLiteButtonEntityDescription(
    ButtonEntityDescription, EchonetLiteEntityDescription
):
    """Entity description that also stores EPC metadata."""

    press_value: bytes  # Byte value to send when button is pressed

    @classmethod
    @override
    def build_from_entity_def(
        cls, entity_def: EntityDefinition
    ) -> EchonetLiteButtonEntityDescription:
        """Construct a button description from an EntityDefinition.

        Raises:
            ValueError: If entity has no enum values (unsupported for button)
        """
        if not entity_def.enum_values:
            raise ValueError(
                f"Button entity requires enum values, but {entity_def.id} has none"
            )
        # Use the first enum value's EDT as the press value
        press_value = entity_def.enum_values[0].edt.to_bytes(1, "big")
        return cls(
            key=f"{entity_def.epc:02x}",
            press_value=press_value,
            **cls._common_kwargs(entity_def),
        )


_DESCRIPTIONS: dict[int, list[EchonetLiteButtonEntityDescription]] = (
    build_platform_descriptions(Platform.BUTTON, EchonetLiteButtonEntityDescription)
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite buttons from a config entry."""
    setup_common_platform(
        entry,
        async_add_entities,
        Platform.BUTTON.value,
        _DESCRIPTIONS,
        EchonetLiteButton,
    )


class EchonetLiteButton(
    EchonetLiteDescribedEntity[EchonetLiteButtonEntityDescription], ButtonEntity
):
    """Representation of a write-only ECHONET Lite property as a button.

    Button entities are used for write-only properties that represent one-shot
    commands (e.g., reset operations). The button press sends a predefined EDT
    value via ECHONET Lite SetC command.
    """

    @override
    async def async_press(self) -> None:
        """Send the button press command via the pyhems runtime client."""
        await self._async_send_property(self._epc, self.description.press_value)
