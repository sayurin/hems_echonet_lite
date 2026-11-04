"""Date platform for ECHONET Lite."""

from dataclasses import dataclass
from datetime import date
from typing import override

from pyhems import EntityDefinition

from homeassistant.components.date import DateEntity, DateEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    build_platform_descriptions,
    setup_common_platform,
)
from .prop import TemporalProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EchonetLiteDateEntityDescription(
    DateEntityDescription, EchonetLiteEntityDescription
):
    """A date setting with its typed EPC adapter."""

    prop: TemporalProp[date]

    @classmethod
    @override
    def build_from_entity_def(
        cls, class_code: int, entity_def: EntityDefinition
    ) -> EchonetLiteDateEntityDescription:
        """Build a writable scalar date description."""
        return cls(
            key=f"{entity_def.epc:02x}_0",
            prop=TemporalProp.from_entity_def(entity_def, date),
            **cls._common_kwargs(entity_def),
        )


_DESCRIPTIONS = build_platform_descriptions(
    Platform.DATE, EchonetLiteDateEntityDescription
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up scalar date settings."""
    setup_common_platform(
        entry, async_add_entities, Platform.DATE.value, _DESCRIPTIONS, EchonetLiteDate
    )


class EchonetLiteDate(
    EchonetLiteDescribedEntity[EchonetLiteDateEntityDescription], DateEntity
):
    """An ECHONET Lite local date setting."""

    @property
    @override
    def native_value(self) -> date | None:
        """Return the device's calendar date."""
        return self.description.prop.get(self._node)

    @override
    async def async_set_value(self, value: date) -> None:
        """Write an exactly representable date."""
        await self._send_prop(self.description.prop, value)
