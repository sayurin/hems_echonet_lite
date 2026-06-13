"""Switch platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import Any

from pyhems import EntityDefinition

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
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

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class EchonetLiteSwitchEntityDescription(
    SwitchEntityDescription, EchonetLiteEntityDescription
):
    """Entity description that also stores EPC metadata."""

    prop: BinaryProp

    @classmethod
    def build_from_entity_def(
        cls, entity_def: EntityDefinition
    ) -> EchonetLiteSwitchEntityDescription:
        """Construct a switch description from an EntityDefinition."""
        return cls(
            key=f"{entity_def.epc:02x}",
            prop=BinaryProp.from_entity_def(entity_def),
            **cls._common_kwargs(entity_def),
        )


_DESCRIPTIONS: dict[int, list[EchonetLiteSwitchEntityDescription]] = (
    build_platform_descriptions(Platform.SWITCH, EchonetLiteSwitchEntityDescription)
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite switches from a config entry."""
    setup_common_platform(entry, async_add_entities, _DESCRIPTIONS, EchonetLiteSwitch)


class EchonetLiteSwitch(
    EchonetLiteDescribedEntity[EchonetLiteSwitchEntityDescription], SwitchEntity
):
    """Representation of a writable ECHONET Lite property."""

    @property
    def is_on(self) -> bool | None:
        """Return the decoded boolean value stored in the coordinator."""
        return self.description.prop.get(self._node)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send the On command via the pyhems runtime client."""
        await self._async_send_prop(self.description.prop, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send the Off command via the pyhems runtime client."""
        await self._async_send_prop(self.description.prop, False)
