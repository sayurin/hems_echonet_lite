"""Time platform for ECHONET Lite."""

from dataclasses import dataclass
from datetime import time
from typing import override

from pyhems import EntityDefinition

from homeassistant.components.time import TimeEntity, TimeEntityDescription
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
class EchonetLiteTimeEntityDescription(
    TimeEntityDescription, EchonetLiteEntityDescription
):
    """A writable clock value, never a duration."""

    prop: TemporalProp[time]

    @classmethod
    @override
    def build_from_entity_def(
        cls, class_code: int, entity_def: EntityDefinition
    ) -> EchonetLiteTimeEntityDescription:
        """Build a clock setting description."""
        return cls(
            key=f"{entity_def.epc:02x}_0",
            prop=TemporalProp.from_entity_def(entity_def, time),
            **cls._common_kwargs(entity_def),
        )


_DESCRIPTIONS = build_platform_descriptions(
    Platform.TIME, EchonetLiteTimeEntityDescription
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable scalar clock values."""
    setup_common_platform(
        entry, async_add_entities, Platform.TIME.value, _DESCRIPTIONS, EchonetLiteTime
    )


class EchonetLiteTime(
    EchonetLiteDescribedEntity[EchonetLiteTimeEntityDescription], TimeEntity
):
    """An ECHONET Lite clock or timer time setting."""

    @property
    @override
    def native_value(self) -> time | None:
        """Return the local clock value without timezone conversion."""
        return self.description.prop.get(self._node)

    @override
    async def async_set_value(self, value: time) -> None:
        """Write a clock value without rounding seconds."""
        await self._send_prop(self.description.prop, value)
