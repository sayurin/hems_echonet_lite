"""Lock platform for the HEMS Echonet Lite integration.

Supports the ECHONET Lite electric lock class (0x026F).

Like the climate / water_heater platforms, this platform exposes a high-level
``LockEntity`` that aggregates the main lock (EPC 0xE0), the optional sub-lock
(EPC 0xE1) and the alarm status (EPC 0xE5) into a single entity. EPCs that
are aggregated by this entity are listed in :data:`DEDICATED_PLATFORM_EPCS`
so that the generic switch / binary_sensor platforms do not produce duplicate
entities for them.

The other lock-related EPCs -- door guard 0xE2, door open/close 0xE3,
occupancy 0xE4, auto-lock setting 0xE6, battery level 0xE7 -- are intentionally
left to the generic binary_sensor / switch platforms because they expose
information that does not fit Home Assistant's ``LockEntity`` contract.
"""

from __future__ import annotations

from typing import Any

from pyhems import NodeState

from homeassistant.components.lock import LockEntity
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
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1

# EPC 0xE0 / 0xE1 raw values
_LOCK_LOCKED = 0x41
_LOCK_UNLOCKED = 0x42

# EPC 0xE5 (Alarm status) raw values. 0x40 is the "normal" indicator;
# any other value means the device reports some abnormal condition.
_ALARM_NORMAL = 0x40


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite lock entities from a config entry."""

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        if node.eoj.class_code != CLASS_CODE_ELECTRIC_LOCK:
            return []
        return [EchonetLiteLock(coordinator, node)]

    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        entity_factory=_entity_factory,
    )


class EchonetLiteLock(EchonetLiteEntity, LockEntity):
    """Representation of an ECHONET Lite electric lock."""

    _attr_name = None

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
    ) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator, node)
        self._attr_unique_id = f"{node.device_key}-lock"
        # Sub-lock is reported only when the device advertises EPC 0xE1.
        self._has_sub_lock = EPC_LOCK_SETTING_2 in node.get_epcs

    def _raw(self, epc: int) -> int | None:
        """Return the single-byte raw value for ``epc`` if known."""
        if edt := self._node.properties.get(epc):
            return edt[0]
        return None

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
        main = self._raw(EPC_LOCK_SETTING_1)
        if main is None:
            return None
        if main == _LOCK_LOCKED:
            if self._has_sub_lock:
                sub = self._raw(EPC_LOCK_SETTING_2)
                if sub != _LOCK_LOCKED:
                    return False
            return True
        if main == _LOCK_UNLOCKED:
            return False
        return None

    @property
    def is_jammed(self) -> bool | None:
        """Return True when the device reports an alarm condition.

        The MRA defines 0x40 as the "normal" indicator and 0x41-0x44 as
        various alarm conditions (break-open, tampered, key-related etc.).
        We collapse all alarm values to ``is_jammed = True`` so HA users
        get a single actionable signal.
        """
        alarm = self._raw(EPC_LOCK_ALARM_STATUS)
        if alarm is None:
            return None
        return alarm != _ALARM_NORMAL

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the device by writing the primary lock EPC only.

        We deliberately do not touch the sub-lock (EPC 0xE1) here because
        it is optional and writing it on a device that only supports the
        primary lock would trigger a SetC failure. Devices that expose a
        sub-lock typically engage it themselves via Auto-Lock or via the
        physical mechanism.
        """
        await self._async_send_property(EPC_LOCK_SETTING_1, bytes([_LOCK_LOCKED]))

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the device by writing the primary lock EPC only."""
        await self._async_send_property(EPC_LOCK_SETTING_1, bytes([_LOCK_UNLOCKED]))


__all__ = ["EchonetLiteLock"]
