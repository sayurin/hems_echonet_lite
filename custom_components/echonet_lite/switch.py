"""Switch platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import Any

from pyhems import EntityDefinition

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import infer_entity_category, infer_entity_registry_enabled_default
from .entity import (
    BinaryProp,
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    setup_echonet_lite_platform,
)
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class EchonetLiteSwitchEntityDescription(
    SwitchEntityDescription, EchonetLiteEntityDescription
):
    """Entity description that also stores EPC metadata."""

    prop: BinaryProp


def _create_switch_description(
    class_code: int,
    entity_def: EntityDefinition,
) -> EchonetLiteSwitchEntityDescription:
    """Create a switch entity description from an EntityDefinition."""
    return EchonetLiteSwitchEntityDescription(
        key=f"{entity_def.epc:02x}",
        translation_key=entity_def.id,
        class_code=class_code,
        epc=entity_def.epc,
        entity_category=infer_entity_category(entity_def),
        entity_registry_enabled_default=infer_entity_registry_enabled_default(
            entity_def
        ),
        prop=BinaryProp.from_entity_def(entity_def),
        manufacturer_code=entity_def.manufacturer_code,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite switches from a config entry."""
    setup_echonet_lite_platform(
        entry,
        async_add_entities,
        "switch",
        _create_switch_description,
        EchonetLiteSwitch,
        "switch",
    )


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


__all__ = ["EchonetLiteSwitch"]
