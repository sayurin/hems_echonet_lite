"""Lock platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import Any

from pyhems import NodeState

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CLASS_CODE_ELECTRIC_LOCK,
    EPC_LOCK_ALARM_STATUS,
    EPC_LOCK_SETTING_1,
    EPC_LOCK_SETTING_2,
)
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_echonet_lite_device_platform
from .prop import BinaryProp, EnumProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class EchonetLiteLockEntityDescription(LockEntityDescription):
    """Description for an ECHONET Lite lock entity."""

    lock_prop: BinaryProp
    sub_lock_prop: BinaryProp
    alarm_prop: EnumProp


def _create_lock_description() -> EchonetLiteLockEntityDescription:
    """Build a lock description from pyhems definitions."""
    return EchonetLiteLockEntityDescription(
        key="lock",
        lock_prop=BinaryProp.from_registry(
            CLASS_CODE_ELECTRIC_LOCK, EPC_LOCK_SETTING_1
        ),
        sub_lock_prop=BinaryProp.from_registry(
            CLASS_CODE_ELECTRIC_LOCK, EPC_LOCK_SETTING_2
        ),
        alarm_prop=EnumProp.from_registry(
            CLASS_CODE_ELECTRIC_LOCK, EPC_LOCK_ALARM_STATUS
        ),
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite lock entities from a config entry."""
    description = _create_lock_description()

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        if node.eoj.class_code != CLASS_CODE_ELECTRIC_LOCK:
            return []
        return [EchonetLiteLock(coordinator, node, description)]

    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        entity_factory=_entity_factory,
    )


class EchonetLiteLock(EchonetLiteEntity, LockEntity):
    """Representation of an ECHONET Lite electric lock."""

    _attr_name = None
    entity_description: EchonetLiteLockEntityDescription

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteLockEntityDescription,
    ) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator, node)
        self.entity_description = description
        self._attr_unique_id = f"{node.device_key}-{description.key}"
        self._has_sub_lock = EPC_LOCK_SETTING_2 in node.get_epcs
        self._has_alarm = EPC_LOCK_ALARM_STATUS in node.get_epcs

    @property
    def is_locked(self) -> bool | None:
        """Return True only when every advertised lock is in the locked state.

        ECHONET Lite electric locks may expose a primary lock (EPC 0xE0,
        required) and an optional sub-lock (EPC 0xE1). We treat the device
        as "locked" only when every lock the device advertises reports
        ``locked``; any partial state (e.g. main=locked, sub=unlocked) is
        reported as ``unlocked`` so that automations and the UI surface a
        not-fully-secured door.
        """
        if (main := self.entity_description.lock_prop.get(self._node)) is None:
            return None
        if not self._has_sub_lock:
            return main
        sub = self.entity_description.sub_lock_prop.get(self._node)
        return None if sub is None else main and sub

    @property
    def is_jammed(self) -> bool | None:
        """Return True when the device reports an alarm condition.

        The MRA defines 0x40 as the "normal" indicator and 0x41-0x44 as
        various alarm conditions (break-open, tampered, key-related etc.).
        We collapse all alarm values to ``is_jammed = True`` so HA users
        get a single actionable signal.
        """
        if not self._has_alarm:
            return None
        key = self.entity_description.alarm_prop.get(self._node)
        return None if key is None else key != "normal"

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the device by writing the primary lock EPC only.

        We deliberately do not touch the sub-lock (EPC 0xE1) here because
        it is optional and writing it on a device that only supports the
        primary lock would trigger a SetC failure. Devices that expose a
        sub-lock typically engage it themselves via Auto-Lock or via the
        physical mechanism.
        """
        await self._async_send_prop(self.entity_description.lock_prop, True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the device by writing the primary lock EPC only."""
        await self._async_send_prop(self.entity_description.lock_prop, False)
