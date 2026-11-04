"""Datetime platform for ECHONET Lite."""

from dataclasses import dataclass
from datetime import datetime
from typing import override

from pyhems import EntityDefinition

from homeassistant.components.datetime import DateTimeEntity, DateTimeEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    build_platform_descriptions,
    setup_common_platform,
)
from .prop import DatetimeProp, TemporalProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EchonetLiteDatetimeEntityDescription(
    DateTimeEntityDescription, EchonetLiteEntityDescription
):
    """A local datetime setting interpreted in HA's timezone."""

    prop: DatetimeProp

    @classmethod
    @override
    def build_from_entity_def(
        cls, class_code: int, entity_def: EntityDefinition
    ) -> EchonetLiteDatetimeEntityDescription:
        """Build a writable datetime description."""
        return cls(
            key=f"{entity_def.epc:02x}_0",
            prop=DatetimeProp(TemporalProp.from_entity_def(entity_def, datetime)),
            **cls._common_kwargs(entity_def),
        )


_DESCRIPTIONS = build_platform_descriptions(
    Platform.DATETIME, EchonetLiteDatetimeEntityDescription
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up scalar datetime settings."""
    setup_common_platform(
        entry,
        async_add_entities,
        Platform.DATETIME.value,
        _DESCRIPTIONS,
        EchonetLiteDatetime,
    )


class EchonetLiteDatetime(
    EchonetLiteDescribedEntity[EchonetLiteDatetimeEntityDescription], DateTimeEntity
):
    """An ECHONET Lite local datetime setting."""

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to timezone changes for local datetime interpretation."""
        await super().async_added_to_hass()
        self._async_listen_timezone_updates()

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the device datetime in HA's timezone."""
        return self.description.prop.get(self._node)

    @override
    async def async_set_value(self, value: datetime) -> None:
        """Write the datetime converted to HA's timezone."""
        await self._send_prop(self.description.prop, value)
