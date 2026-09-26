"""Fan platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
import logging
from typing import Any, override

from pyhems import DeviceClass, NodeState, Property

from homeassistant.components.fan import (
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    FanEntity,
    FanEntityDescription,
    FanEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .ceiling_fan import (
    CEILING_FAN_COMPANION_EPCS,
    build_ceiling_fan_properties,
)
from .const import (
    DEDICATED_PLATFORM_REQUIRED_EPCS,
    DOMAIN,
    EPC_AIR_FLOW_LEVEL,
    EPC_CEILING_FAN_AIR_FLOW_DIRECTION,
    EPC_CEILING_FAN_AIR_FLOW_RATE,
    EPC_CEILING_FAN_NATURAL_WIND,
    EPC_OPERATION_STATUS,
)
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_dedicated_platform
from .prop import BinaryProp, EnumProp
from .runtime import EchonetLiteConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

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

# Ceiling fan (0x013A) has ten discrete speed steps at 10% … 100%.
_CEILING_FAN_SPEED_LEVELS = [
    "level_1",
    "level_2",
    "level_3",
    "level_4",
    "level_5",
    "level_6",
    "level_7",
    "level_8",
    "level_9",
    "level_10",
]

# Preset modes (ventilation fan / air cleaner)
PRESET_MODE_AUTO = "auto"
PRESET_MODE_MANUAL = "manual"

# Ceiling fan natural-wind presets. Natural wind varies the breeze; it does
# not swing the head, so it is not FanEntityFeature.OSCILLATE.
PRESET_MODE_NORMAL = "normal"
PRESET_MODE_NATURAL = "natural"

# ECHONET down (air blows down) → HA forward; up → reverse.
_CEILING_FAN_DIRECTION_TO_HA: dict[str, str] = {
    "down": DIRECTION_FORWARD,
    "up": DIRECTION_REVERSE,
}
_HA_DIRECTION_TO_CEILING_FAN: dict[str, str] = {
    DIRECTION_FORWARD: "down",
    DIRECTION_REVERSE: "up",
}


@dataclass(frozen=True, kw_only=True)
class EchonetLiteFanEntityDescription(FanEntityDescription):
    """Description for an ECHONET Lite fan entity."""

    op_status: BinaryProp
    air_flow_prop: EnumProp


@dataclass(frozen=True, kw_only=True)
class EchonetLiteCeilingFanEntityDescription(FanEntityDescription):
    """Description for a ceiling-fan (0x013A) entity."""

    op_status: BinaryProp
    air_flow_prop: EnumProp
    direction_prop: EnumProp
    natural_wind_prop: BinaryProp


def _create_fan_description(
    class_code: int,
    translation_key: str = "fan",
) -> EchonetLiteFanEntityDescription:
    """Build a fan description from pyhems definitions."""
    return EchonetLiteFanEntityDescription(
        key="fan",
        translation_key=translation_key,
        op_status=BinaryProp.from_registry(class_code, EPC_OPERATION_STATUS),
        air_flow_prop=EnumProp.from_registry(class_code, EPC_AIR_FLOW_LEVEL),
    )


def _create_ceiling_fan_description() -> EchonetLiteCeilingFanEntityDescription:
    """Build a ceiling-fan description from pyhems definitions."""
    class_code = DeviceClass.CEILING_FAN
    return EchonetLiteCeilingFanEntityDescription(
        key="fan",
        translation_key="ceiling_fan",
        op_status=BinaryProp.from_registry(class_code, EPC_OPERATION_STATUS),
        air_flow_prop=EnumProp.from_registry(
            class_code, EPC_CEILING_FAN_AIR_FLOW_RATE
        ),
        direction_prop=EnumProp.from_registry(
            class_code, EPC_CEILING_FAN_AIR_FLOW_DIRECTION
        ),
        natural_wind_prop=BinaryProp.from_registry(
            class_code, EPC_CEILING_FAN_NATURAL_WIND
        ),
    )


_DESCRIPTIONS: dict[int, EchonetLiteFanEntityDescription] = {
    DeviceClass.VENTILATION_FAN: _create_fan_description(DeviceClass.VENTILATION_FAN),
    DeviceClass.AIR_CONDITIONER_VENTILATION_FAN: _create_fan_description(
        DeviceClass.AIR_CONDITIONER_VENTILATION_FAN
    ),
    DeviceClass.AIR_CLEANER: _create_fan_description(
        DeviceClass.AIR_CLEANER, "air_cleaner"
    ),
}

_CEILING_FAN_DESCRIPTIONS: dict[int, EchonetLiteCeilingFanEntityDescription] = {
    DeviceClass.CEILING_FAN: _create_ceiling_fan_description(),
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite fan entities from a config entry."""
    setup_dedicated_platform(
        entry,
        async_add_entities,
        Platform.FAN.value,
        _DESCRIPTIONS,
        EchonetLiteFan,
    )
    setup_dedicated_platform(
        entry,
        async_add_entities,
        Platform.FAN.value,
        _CEILING_FAN_DESCRIPTIONS,
        EchonetLiteCeilingFan,
    )


class EchonetLiteFan(EchonetLiteEntity, FanEntity):
    """Representation of an ECHONET Lite fan device.

    Supports air cleaners (0x0135), ventilation fans (0x0133),
    and air conditioner ventilation fans (0x0134).
    """

    _attr_name = None
    _attr_preset_modes = [PRESET_MODE_AUTO, PRESET_MODE_MANUAL]
    _attr_speed_count = len(_SPEED_LEVELS)
    entity_description: EchonetLiteFanEntityDescription

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteFanEntityDescription,
    ) -> None:
        """Initialize an ECHONET Lite fan entity."""
        super().__init__(coordinator, node)
        self.entity_description = description
        self._attr_unique_id = f"{node.device_key}-{description.key}"
        self._subscribed_epcs = DEDICATED_PLATFORM_REQUIRED_EPCS.get(
            node.eoj.class_code, frozenset()
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
    @override
    def is_on(self) -> bool | None:
        """Return true if the fan is on."""
        return self.entity_description.op_status.get(self._node)

    @property
    @override
    def percentage(self) -> int | None:
        """Return the current speed percentage.

        Returns None when in preset mode (auto) or when air flow level is unavailable.
        """
        key = self.entity_description.air_flow_prop.get(self._node)
        if key is None or key == PRESET_MODE_AUTO or key not in _SPEED_LEVELS:
            return None
        return ordered_list_item_to_percentage(_SPEED_LEVELS, key)

    @property
    @override
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        if (key := self.entity_description.air_flow_prop.get(self._node)) is None:
            return None
        if key == PRESET_MODE_AUTO:
            return PRESET_MODE_AUTO
        return PRESET_MODE_MANUAL if key in _SPEED_LEVELS else None

    @override
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
            self._send_prop(self.entity_description.op_status, False)
            return

        properties: list[Property] = [
            self.entity_description.op_status.make_property(True)
        ]

        if EPC_AIR_FLOW_LEVEL in self._node.set_epcs:
            if preset_mode == PRESET_MODE_AUTO:
                properties.append(
                    self.entity_description.air_flow_prop.make_property(
                        PRESET_MODE_AUTO
                    )
                )
            elif percentage is not None:
                properties.append(
                    self.entity_description.air_flow_prop.make_property(
                        percentage_to_ordered_list_item(_SPEED_LEVELS, percentage)
                    )
                )

        self._send_properties(properties)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        self._send_prop(self.entity_description.op_status, False)

    @override
    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan.

        Sends only ``AIR_FLOW_LEVEL`` when the fan is already on so the
        current preset/level is replaced atomically. When the fan is off
        (and ``percentage>0``), ``OP_STATUS=ON`` is bundled in the same
        frame so the service call lands on a running fan. ``percentage=0``
        is treated as OFF per HA convention.
        """
        if percentage == 0:
            self._send_prop(self.entity_description.op_status, False)
            return
        if EPC_AIR_FLOW_LEVEL not in self._node.set_epcs:
            return
        properties: list[Property] = []
        if not self.is_on:
            properties.append(self.entity_description.op_status.make_property(True))
        key = percentage_to_ordered_list_item(_SPEED_LEVELS, percentage)
        properties.append(self.entity_description.air_flow_prop.make_property(key))
        self._send_properties(properties)

    @override
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
            properties: list[Property] = []
            if not self.is_on:
                properties.append(self.entity_description.op_status.make_property(True))
            properties.append(
                self.entity_description.air_flow_prop.make_property(PRESET_MODE_AUTO)
            )
            self._send_properties(properties)
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
        self._send_prop(self.entity_description.op_status, True)


class EchonetLiteCeilingFan(EchonetLiteEntity, FanEntity):
    """Representation of an ECHONET Lite ceiling fan (class 0x013A).

    Writes go through ``ceiling_fan_set_properties`` as a single SetC so the
    buzzer bit travels with every command. The lamp is a separate light entity.
    """

    _attr_name = None
    _attr_speed_count = len(_CEILING_FAN_SPEED_LEVELS)
    entity_description: EchonetLiteCeilingFanEntityDescription

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteCeilingFanEntityDescription,
    ) -> None:
        """Initialize a ceiling-fan entity from the node's advertised EPCs."""
        super().__init__(coordinator, node)
        self.entity_description = description
        self._attr_unique_id = f"{node.device_key}-{description.key}"

        # Subscribe only to fan EPCs the node actually advertises for GET.
        fan_epcs = frozenset(
            {
                EPC_OPERATION_STATUS,
                EPC_CEILING_FAN_AIR_FLOW_RATE,
                EPC_CEILING_FAN_AIR_FLOW_DIRECTION,
                EPC_CEILING_FAN_NATURAL_WIND,
            }
        )
        self._subscribed_epcs = fan_epcs & node.get_epcs

        features = FanEntityFeature(0)
        if EPC_OPERATION_STATUS in node.set_epcs:
            features |= FanEntityFeature.TURN_ON
            features |= FanEntityFeature.TURN_OFF
        if EPC_CEILING_FAN_AIR_FLOW_RATE in node.set_epcs:
            features |= FanEntityFeature.SET_SPEED
        if EPC_CEILING_FAN_AIR_FLOW_DIRECTION in node.set_epcs:
            features |= FanEntityFeature.DIRECTION
        if EPC_CEILING_FAN_NATURAL_WIND in node.set_epcs:
            features |= FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = [PRESET_MODE_NORMAL, PRESET_MODE_NATURAL]
        self._attr_supported_features = features

    def _send_ceiling_fan(self, **kwargs: Any) -> None:
        """Build and send one silent ceiling-fan SetC."""
        self._send_properties(
            build_ceiling_fan_properties(**kwargs),
            allow_unadvertised_epcs=CEILING_FAN_COMPANION_EPCS,
        )

    @property
    @override
    def is_on(self) -> bool | None:
        """Return true if the fan is on."""
        return self.entity_description.op_status.get(self._node)

    @property
    @override
    def percentage(self) -> int | None:
        """Return the current speed as 10% … 100%."""
        key = self.entity_description.air_flow_prop.get(self._node)
        if key is None or key not in _CEILING_FAN_SPEED_LEVELS:
            return None
        return ordered_list_item_to_percentage(_CEILING_FAN_SPEED_LEVELS, key)

    @property
    @override
    def current_direction(self) -> str | None:
        """Return forward (down) or reverse (up)."""
        key = self.entity_description.direction_prop.get(self._node)
        if key is None:
            return None
        return _CEILING_FAN_DIRECTION_TO_HA.get(key)

    @property
    @override
    def preset_mode(self) -> str | None:
        """Return natural when natural wind is on, otherwise normal."""
        if FanEntityFeature.PRESET_MODE not in self._attr_supported_features:
            return None
        natural = self.entity_description.natural_wind_prop.get(self._node)
        if natural is None:
            return None
        return PRESET_MODE_NATURAL if natural else PRESET_MODE_NORMAL

    @override
    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally setting speed and/or natural wind."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        if percentage == 0:
            self._send_ceiling_fan(power=False)
            return

        kwargs_out: dict[str, Any] = {"power": True}
        if (
            percentage is not None
            and EPC_CEILING_FAN_AIR_FLOW_RATE in self._node.set_epcs
        ):
            kwargs_out["speed"] = percentage_to_ordered_list_item(
                _CEILING_FAN_SPEED_LEVELS, percentage
            )
        if (
            preset_mode is not None
            and EPC_CEILING_FAN_NATURAL_WIND in self._node.set_epcs
        ):
            kwargs_out["natural_wind"] = preset_mode == PRESET_MODE_NATURAL
        self._send_ceiling_fan(**kwargs_out)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off, leaving the stored speed and lamp alone."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        self._send_ceiling_fan(power=False)

    @override
    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed. Percentage 0 turns the fan off."""
        if percentage == 0:
            await self.async_turn_off()
            return
        if EPC_CEILING_FAN_AIR_FLOW_RATE not in self._node.set_epcs:
            return
        self._send_ceiling_fan(
            power=True,
            speed=percentage_to_ordered_list_item(
                _CEILING_FAN_SPEED_LEVELS, percentage
            ),
        )

    @override
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set normal or natural wind without resending the speed."""
        if EPC_CEILING_FAN_NATURAL_WIND not in self._node.set_epcs:
            return
        self._send_ceiling_fan(
            power=True,
            natural_wind=preset_mode == PRESET_MODE_NATURAL,
        )

    @override
    async def async_set_direction(self, direction: str) -> None:
        """Set air flow direction. Forward is down; reverse is up."""
        if EPC_CEILING_FAN_AIR_FLOW_DIRECTION not in self._node.set_epcs:
            return
        if (echonet_dir := _HA_DIRECTION_TO_CEILING_FAN.get(direction)) is None:
            return
        self._send_ceiling_fan(power=True, air_flow_direction=echonet_dir)
