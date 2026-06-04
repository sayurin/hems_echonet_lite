"""Fan platform for the HEMS Echonet Lite integration."""

import logging
from typing import Any

from pyhems import NodeState, Property

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .const import (
    CLASS_CODE_AIR_CLEANER,
    CLASS_CODE_AIR_CONDITIONER_VENTILATION_FAN,
    CLASS_CODE_VENTILATION_FAN,
    DOMAIN,
    EPC_AIR_FLOW_LEVEL,
    EPC_OPERATION_STATUS,
)
from .coordinator import EchonetLiteCoordinator
from .entity import (
    BinaryProp,
    EchonetLiteEntity,
    EnumProp,
    setup_echonet_lite_device_platform,
)
from .types import EchonetLiteConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# Class codes handled by this platform
FAN_CLASS_CODES: frozenset[int] = frozenset(
    {
        CLASS_CODE_VENTILATION_FAN,
        CLASS_CODE_AIR_CONDITIONER_VENTILATION_FAN,
        CLASS_CODE_AIR_CLEANER,
    }
)

# Ordered list of pyhems speed level keys (level_1 = slowest, level_8 = fastest)
_SPEED_LEVELS = [
    "level_1",
    "level_2",
    "level_3",
    "level_4",
    "level_5",
    "level_6",
    "level_7",
    "level_8",
]

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

    _attr_name = None
    _attr_preset_modes = [PRESET_MODE_AUTO, PRESET_MODE_MANUAL]
    _attr_speed_count = len(_SPEED_LEVELS)

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
        # bool ⇔ EDT codec for the common operation status EPC.
        definitions = coordinator.config_entry.runtime_data.definitions
        class_code = node.eoj.class_code
        self._op_status = BinaryProp.from_registry(
            definitions, class_code, EPC_OPERATION_STATUS
        )
        self._air_flow_prop = EnumProp.from_registry(
            definitions, class_code, EPC_AIR_FLOW_LEVEL
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
        return self._op_status.get(self._node)

    @property
    def percentage(self) -> int | None:
        """Return the current speed percentage.

        Returns None when in preset mode (auto) or when air flow level is unavailable.
        """
        key = self._air_flow_prop.get(self._node)
        if key is None or key == PRESET_MODE_AUTO:
            return None
        if key not in _SPEED_LEVELS:
            return None
        return ordered_list_item_to_percentage(_SPEED_LEVELS, key)

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        key = self._air_flow_prop.get(self._node)
        if key is None:
            return None
        if key == PRESET_MODE_AUTO:
            return PRESET_MODE_AUTO
        if key in _SPEED_LEVELS:
            return PRESET_MODE_MANUAL
        return None

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
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )

        # Turn off the fan if percentage is explicitly set to 0
        if percentage == 0:
            await self._async_send_prop(self._op_status, False)
            return

        properties: list[Property] = [self._op_status.make_property(True)]

        if EPC_AIR_FLOW_LEVEL in self._node.set_epcs:
            if preset_mode == PRESET_MODE_AUTO:
                properties.append(self._air_flow_prop.make_property(PRESET_MODE_AUTO))
            elif percentage is not None:
                properties.append(
                    self._air_flow_prop.make_property(
                        percentage_to_ordered_list_item(_SPEED_LEVELS, percentage)
                    )
                )

        await self._async_send_properties(properties)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        await self._async_send_prop(self._op_status, False)

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan.

        Sends only ``AIR_FLOW_LEVEL`` when the fan is already on so the
        current preset/level is replaced atomically. When the fan is off
        (and ``percentage>0``), ``OP_STATUS=ON`` is bundled in the same
        frame so the service call lands on a running fan. ``percentage=0``
        is treated as OFF per HA convention.
        """
        if percentage == 0:
            await self._async_send_prop(self._op_status, False)
            return
        if EPC_AIR_FLOW_LEVEL not in self._node.set_epcs:
            return
        key = percentage_to_ordered_list_item(_SPEED_LEVELS, percentage)
        if self.is_on:
            await self._async_send_prop(self._air_flow_prop, key)
            return
        await self._async_send_properties(
            [
                self._op_status.make_property(True),
                self._air_flow_prop.make_property(key),
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
            if self.is_on:
                await self._async_send_prop(self._air_flow_prop, PRESET_MODE_AUTO)
                return
            await self._async_send_properties(
                [
                    self._op_status.make_property(True),
                    self._air_flow_prop.make_property(PRESET_MODE_AUTO),
                ]
            )
            return

        # preset_mode == PRESET_MODE_MANUAL
        if self.is_on:
            return
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        await self._async_send_prop(self._op_status, True)


__all__ = ["EchonetLiteFan"]
