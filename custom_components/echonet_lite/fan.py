"""Fan platform for the HEMS integration."""

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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)
from homeassistant.util.scaling import int_states_in_range

from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity
from .types import EchonetLiteConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

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
    assert entry.runtime_data is not None
    coordinator = entry.runtime_data.coordinator

    @callback
    def _async_add_entities_for_devices(device_keys: set[str]) -> None:
        """Create fan entities for the given device keys."""
        new_entities: list[EchonetLiteFan] = []
        for device_key in device_keys:
            node = coordinator.data.get(device_key)
            if not node:
                continue

            if node.eoj.class_code not in FAN_CLASS_CODES:
                continue

            new_entities.append(EchonetLiteFan(coordinator, node))
        if new_entities:
            async_add_entities(new_entities)

    @callback
    def _async_process_coordinator_update() -> None:
        """Handle coordinator update - process only new devices."""
        if coordinator.new_device_keys:
            _async_add_entities_for_devices(coordinator.new_device_keys)

    entry.async_on_unload(
        coordinator.async_add_listener(_async_process_coordinator_update)
    )
    # Initial setup: process all existing devices
    _async_add_entities_for_devices(set(coordinator.data.keys()))


class EchonetLiteFan(EchonetLiteEntity, FanEntity):
    """Representation of an ECHONET Lite fan device.

    Supports air cleaners (0x0135), ventilation fans (0x0133),
    and air conditioner ventilation fans (0x0134).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "fan"
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
        """Turn on the fan."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError("Operation status is not writable")

        # Turn off the fan if percentage is explicitly set to 0
        if percentage == 0:
            await self._async_send_property(EPC_OPERATION_STATUS, b"\x31")
            return

        properties: list[Property] = [Property(epc=EPC_OPERATION_STATUS, edt=b"\x30")]

        # If percentage or preset_mode is specified, also set the air flow level
        if EPC_AIR_FLOW_LEVEL in self._node.set_epcs:
            if preset_mode is None and percentage is None:
                preset_mode = self.preset_mode
                percentage = self.percentage
            if preset_mode == PRESET_MODE_AUTO:
                properties.append(
                    Property(epc=EPC_AIR_FLOW_LEVEL, edt=bytes([_EDT_AUTO]))
                )
            elif percentage is not None:
                edt_value = self._percentage_to_edt(percentage)
                properties.append(
                    Property(epc=EPC_AIR_FLOW_LEVEL, edt=bytes([edt_value]))
                )

        await self._async_send_properties(properties)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        await self.async_turn_on(percentage=0)

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan."""
        await self.async_turn_on(percentage=percentage)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        await self.async_turn_on(preset_mode=preset_mode)


__all__ = ["EchonetLiteFan"]
