"""Lock platform for the HEMS Echonet Lite integration."""

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
from .prop import BinaryProp, EnumProp
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1


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
        definitions = coordinator.config_entry.runtime_data.definitions
        class_code = node.eoj.class_code
        self._lock_prop = BinaryProp.from_registry(
            definitions, class_code, EPC_LOCK_SETTING_1
        )
        self._sub_lock_prop: BinaryProp | None = (
            BinaryProp.from_registry(definitions, class_code, EPC_LOCK_SETTING_2)
            if EPC_LOCK_SETTING_2 in node.get_epcs
            else None
        )
        self._alarm_prop: EnumProp | None = (
            EnumProp.from_registry(definitions, class_code, EPC_LOCK_ALARM_STATUS)
            if EPC_LOCK_ALARM_STATUS in node.get_epcs
            else None
        )

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
        main = self._lock_prop.get(self._node)
        if main is None:
            return None
        if self._sub_lock_prop is None:
            return main
        sub = self._sub_lock_prop.get(self._node)
        return None if sub is None else main and sub

    @property
    def is_jammed(self) -> bool | None:
        """Return True when the device reports an alarm condition.

        The MRA defines 0x40 as the "normal" indicator and 0x41-0x44 as
        various alarm conditions (break-open, tampered, key-related etc.).
        We collapse all alarm values to ``is_jammed = True`` so HA users
        get a single actionable signal.
        """
        if self._alarm_prop is None:
            return None
        key = self._alarm_prop.get(self._node)
        return None if key is None else key != "normal"

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the device by writing the primary lock EPC only.

        We deliberately do not touch the sub-lock (EPC 0xE1) here because
        it is optional and writing it on a device that only supports the
        primary lock would trigger a SetC failure. Devices that expose a
        sub-lock typically engage it themselves via Auto-Lock or via the
        physical mechanism.
        """
        await self._async_send_prop(self._lock_prop, True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the device by writing the primary lock EPC only."""
        await self._async_send_prop(self._lock_prop, False)
