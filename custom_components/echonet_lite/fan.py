"""Fan platform for the HEMS Echonet Lite integration."""

from __future__ import annotations

import logging
from typing import Any

from pyhems import (
    CLASS_CODE_AIR_CLEANER,
    CLASS_CODE_AIR_CONDITIONER_VENTILATION_FAN,
    CLASS_CODE_VENTILATION_FAN,
    NodeState,
    Property,
)

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)
from homeassistant.util.scaling import int_states_in_range

from .const import DOMAIN
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_echonet_lite_device_platform
from .types import EchonetLiteConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# Fan class codes (local to this platform)
FAN_CLASS_CODES: frozenset[int] = frozenset(
    {
        CLASS_CODE_VENTILATION_FAN,
        CLASS_CODE_AIR_CONDITIONER_VENTILATION_FAN,
        CLASS_CODE_AIR_CLEANER,
    }
)

# Fan-specific EPCs (local to this platform)
EPC_OPERATION_STATUS = 0x80
EPC_AIR_FLOW_LEVEL = 0xA0

# Air flow rate setting values (0x31-0x38 in ECHONET Lite protocol)
_SPEED_RANGE = (0x31, 0x38)

# Auto mode EDT value
_EDT_AUTO = 0x41

# Preset modes
PRESET_MODE_AUTO = "auto"
PRESET_MODE_MANUAL = "manual"


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite fan entities from a config entry."""

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        if node.eoj.class_code not in FAN_CLASS_CODES:
            return []
        return [EchonetLiteFan(coordinator, node)]

    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        entity_factory=_entity_factory,
    )


class EchonetLiteFan(EchonetLiteEntity, FanEntity):
    """Representation of an ECHONET Lite fan device.

    Supports air cleaners (0x0135), ventilation fans (0x0133),
    and air conditioner ventilation fans (0x0134).
    """

    _attr_has_entity_name = True
    _attr_preset_modes = [PRESET_MODE_AUTO, PRESET_MODE_MANUAL]
    _attr_speed_count = int_states_in_range(_SPEED_RANGE)

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
    ) -> None:
        """Initialize an ECHONET Lite fan entity."""
        super().__init__(coordinator, node)
        self._attr_unique_id = f"{node.device_key}-fan"
        self._attr_translation_key = (
            "air_cleaner" if node.eoj.class_code == CLASS_CODE_AIR_CLEANER else "fan"
        )
        features = FanEntityFeature(0)

        if EPC_OPERATION_STATUS in node.set_epcs:
            features |= FanEntityFeature.TURN_ON
            features |= FanEntityFeature.TURN_OFF

        if EPC_AIR_FLOW_LEVEL in node.set_epcs:
            features |= FanEntityFeature.SET_SPEED
            features |= FanEntityFeature.PRESET_MODE

        self._attr_supported_features = features

    @property
    def is_on(self) -> bool | None:
        """Return true if the fan is on."""
        if edt := self._node.properties.get(EPC_OPERATION_STATUS):
            return edt == b"\x30"  # 0x30 = ON
        return None

    @property
    def percentage(self) -> int | None:
        """Return the current speed percentage.

        Returns None when in preset mode (auto) or when air flow level is unavailable.
        """
        if edt := self._node.properties.get(EPC_AIR_FLOW_LEVEL):
            if len(edt) != 1:
                return None
            value = edt[0]
            # Auto mode (0x41) - return None to indicate preset mode is active
            if value == _EDT_AUTO:
                return None
            # EDT values 0x31-0x38
            if 0x31 <= value <= 0x38:
                return ranged_value_to_percentage(_SPEED_RANGE, value)
        return None

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        if edt := self._node.properties.get(EPC_AIR_FLOW_LEVEL):
            if len(edt) != 1:
                return None
            if edt[0] == _EDT_AUTO:
                return PRESET_MODE_AUTO
            if 0x31 <= edt[0] <= 0x38:
                return PRESET_MODE_MANUAL
        return None

    def _percentage_to_edt(self, percentage: int) -> int:
        """Convert a percentage to an EDT value (0x31-0x38)."""
        return int(round(percentage_to_ranged_value(_SPEED_RANGE, percentage)))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan.

        Service ↔ EPC mapping:

        * bare: ``OP_STATUS=ON`` only. ``EPC_AIR_FLOW_LEVEL`` is not
          retransmitted; the device retains its previous level, which
          notably preserves an ``auto`` preset across ON→OFF→ON.
        * ``percentage=0``: ``OP_STATUS=OFF`` (HA convention).
        * ``percentage=p>0``: ``OP_STATUS=ON`` + ``AIR_FLOW_LEVEL=f(p)``.
        * ``preset_mode="auto"``: ``OP_STATUS=ON`` + ``AIR_FLOW_LEVEL=0x41``.
        * ``preset_mode="manual"``: ``OP_STATUS=ON`` only. ``manual`` is
          not a distinct ECHONET level, so no ``AIR_FLOW_LEVEL`` is sent.
        """
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_status_not_writable",
            )

        # Turn off the fan if percentage is explicitly set to 0
        if percentage == 0:
            await self._async_send_property(EPC_OPERATION_STATUS, b"\x31")
            return

        properties: list[Property] = [Property(epc=EPC_OPERATION_STATUS, edt=b"\x30")]

        if EPC_AIR_FLOW_LEVEL in self._node.set_epcs:
            if preset_mode == PRESET_MODE_AUTO:
                properties.append(
                    Property(epc=EPC_AIR_FLOW_LEVEL, edt=bytes([_EDT_AUTO]))
                )
            elif percentage is not None:
                properties.append(
                    Property(
                        epc=EPC_AIR_FLOW_LEVEL,
                        edt=bytes([self._percentage_to_edt(percentage)]),
                    )
                )

        await self._async_send_properties(properties)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        await self._async_send_property(EPC_OPERATION_STATUS, b"\x31")

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan.

        Sends only ``AIR_FLOW_LEVEL`` when the fan is already on so the
        current preset/level is replaced atomically. When the fan is off
        (and ``percentage>0``), ``OP_STATUS=ON`` is bundled in the same
        frame so the service call lands on a running fan. ``percentage=0``
        is treated as OFF per HA convention.
        """
        if percentage == 0:
            await self._async_send_property(EPC_OPERATION_STATUS, b"\x31")
            return
        if EPC_AIR_FLOW_LEVEL not in self._node.set_epcs:
            return
        edt = bytes([self._percentage_to_edt(percentage)])
        if self.is_on:
            await self._async_send_property(EPC_AIR_FLOW_LEVEL, edt)
            return
        await self._async_send_properties(
            [
                Property(epc=EPC_OPERATION_STATUS, edt=b"\x30"),
                Property(epc=EPC_AIR_FLOW_LEVEL, edt=edt),
            ]
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan.

        ``auto`` maps to ``AIR_FLOW_LEVEL=0x41``. When the fan is off, a
        combined ``OP_STATUS=ON`` + ``AIR_FLOW_LEVEL`` frame is sent;
        when already on, only the level change is transmitted.

        ``manual`` has no direct ECHONET counterpart: any non-auto level
        already represents manual operation. When the fan is off it is
        simply turned on (the device retains its stored level); when
        already on, the call is a no-op to avoid clobbering the current
        level with an arbitrary value.
        """
        if preset_mode == PRESET_MODE_AUTO:
            if EPC_AIR_FLOW_LEVEL not in self._node.set_epcs:
                return
            edt = bytes([_EDT_AUTO])
            if self.is_on:
                await self._async_send_property(EPC_AIR_FLOW_LEVEL, edt)
                return
            await self._async_send_properties(
                [
                    Property(epc=EPC_OPERATION_STATUS, edt=b"\x30"),
                    Property(epc=EPC_AIR_FLOW_LEVEL, edt=edt),
                ]
            )
            return

        # preset_mode == PRESET_MODE_MANUAL
        if self.is_on:
            return
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_status_not_writable",
            )
        await self._async_send_property(EPC_OPERATION_STATUS, b"\x30")


__all__ = ["EchonetLiteFan"]
