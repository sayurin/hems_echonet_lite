"""Panasonic/KDK ceiling-fan (class 0x013A) shared helpers and fan entity.

Panasonic/KDK ceiling fans reject a lone changed EPC. Fan writes prefix
operation status (``0x80``), buzzer (``0xFC``), and Wi-Fi control source
(``0xFD``), then any EPCs the caller already encoded via ``make_property``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, override

from pyhems import DeviceClass, NodeState, Property, get_codec_for_epc

from homeassistant.components.fan import (
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    FanEntity,
    FanEntityDescription,
    FanEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from ...const import (
    DOMAIN,
    EPC_CEILING_FAN_AIR_FLOW_DIRECTION,
    EPC_CEILING_FAN_AIR_FLOW_RATE,
    EPC_CEILING_FAN_BUZZER,
    EPC_CEILING_FAN_CONTROL_SOURCE,
    EPC_CEILING_FAN_NATURAL_WIND,
    EPC_OPERATION_STATUS,
    MANUFACTURER_CODE_PANASONIC,
)
from ...coordinator import EchonetLiteCoordinator
from ...entity import EchonetLiteEntity, setup_dedicated_platform
from ...prop import BinaryProp, EnumProp
from ...runtime import EchonetLiteConfigEntry

# Inserted with fan control writes (may be absent from node.set_epcs).
CEILING_FAN_COMPANION_EPCS: frozenset[int] = frozenset(
    {
        EPC_OPERATION_STATUS,
        EPC_CEILING_FAN_BUZZER,
        EPC_CEILING_FAN_CONTROL_SOURCE,
    }
)

_CLASS = DeviceClass.CEILING_FAN

# Ten discrete speed steps at 10% … 100%.
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

# Natural wind varies the breeze; it does not swing the head, so it is not
# FanEntityFeature.OSCILLATE.
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


def is_panasonic_ceiling_fan(node: Any) -> bool:
    """Return True when the node is a Panasonic/KDK ceiling fan (``0x0000FE``)."""
    return (
        node.eoj.class_code == _CLASS
        and node.manufacturer_code == MANUFACTURER_CODE_PANASONIC
    )


def _encode(epc: int, value: object) -> bytes:
    """Encode one framing EPC (power / buzzer) with the catalog codec."""
    return get_codec_for_epc(_CLASS, epc).encode(value)


def frame_ceiling_fan_setc(
    *,
    power: bool,
    changes: Sequence[Property] = (),
    silent: bool = True,
) -> list[Property]:
    """Prefix ``0x80`` + ``0xFC`` + ``0xFD`` onto caller-provided fan property changes.

    ``silent=True`` writes buzzer ``0x31``; ``silent=False`` writes ``0x30``.
    Control source is always Wi-Fi. Power off must not carry other EPCs
    (raises ``ValueError`` if ``changes`` is non-empty).
    """
    if not power and changes:
        raise ValueError(
            "Power off carries only operation status, buzzer, and control source"
        )
    properties = [
        Property(epc=EPC_OPERATION_STATUS, edt=_encode(EPC_OPERATION_STATUS, power)),
        Property(
            epc=EPC_CEILING_FAN_BUZZER,
            edt=_encode(EPC_CEILING_FAN_BUZZER, not silent),
        ),
        Property(
            epc=EPC_CEILING_FAN_CONTROL_SOURCE,
            edt=_encode(EPC_CEILING_FAN_CONTROL_SOURCE, "wifi"),
        ),
    ]
    if power and changes:
        properties.extend(changes)
    return properties


@dataclass(frozen=True, kw_only=True)
class EchonetLiteCeilingFanEntityDescription(FanEntityDescription):
    """Description for a ceiling-fan (0x013A) entity."""

    op_status: BinaryProp
    air_flow_prop: EnumProp
    direction_prop: EnumProp
    natural_wind_prop: BinaryProp


def _create_ceiling_fan_description() -> EchonetLiteCeilingFanEntityDescription:
    """Build a ceiling-fan description from pyhems definitions."""
    class_code = DeviceClass.CEILING_FAN
    # Codecs are unique per EPC on this class; manufacturer is enforced at
    # entity-creation time (see ``is_panasonic_ceiling_fan``).
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


_CEILING_FAN_DESCRIPTIONS: dict[int, EchonetLiteCeilingFanEntityDescription] = {
    DeviceClass.CEILING_FAN: _create_ceiling_fan_description(),
}


def _should_create_ceiling_fan(
    node: NodeState,
    _description: EchonetLiteCeilingFanEntityDescription,
) -> bool:
    """Create the fan entity only for Panasonic/KDK ceiling fans."""
    return is_panasonic_ceiling_fan(node)


def setup_panasonic_ceiling_fan_platform(
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register Panasonic/KDK ceiling-fan fan entities."""
    setup_dedicated_platform(
        entry,
        async_add_entities,
        Platform.FAN.value,
        _CEILING_FAN_DESCRIPTIONS,
        EchonetLiteCeilingFan,
        should_create=_should_create_ceiling_fan,
    )


class EchonetLiteCeilingFan(EchonetLiteEntity, FanEntity):
    """Representation of an ECHONET Lite ceiling fan (class 0x013A).

    Fan writes use ``frame_ceiling_fan_setc`` so every fan SetC carries power
    and the silent buzzer, then any changed fan EPCs. The lamp is a separate
    light entity in ``ceiling_fan_light``. Panasonic/KDK only (maker-specific
    EPCs ``0xF0``–``0xF7``, ``0xFC``, ``0xFD``).
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

    async def _send_ceiling_fan_setc(
        self, *, power: bool, changes: list[Property] | None = None
    ) -> None:
        """Send one silent ceiling-fan SetC with framed power + buzzer."""
        await self._send_properties(
            frame_ceiling_fan_setc(power=power, changes=changes or ()),
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
            await self._send_ceiling_fan_setc(power=False)
            return

        changes: list[Property] = []
        if (
            percentage is not None
            and EPC_CEILING_FAN_AIR_FLOW_RATE in self._node.set_epcs
        ):
            changes.append(
                self.entity_description.air_flow_prop.make_property(
                    percentage_to_ordered_list_item(
                        _CEILING_FAN_SPEED_LEVELS, percentage
                    )
                )
            )
        if (
            preset_mode is not None
            and EPC_CEILING_FAN_NATURAL_WIND in self._node.set_epcs
        ):
            changes.append(
                self.entity_description.natural_wind_prop.make_property(
                    preset_mode == PRESET_MODE_NATURAL
                )
            )
        await self._send_ceiling_fan_setc(power=True, changes=changes)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off, leaving the stored speed and lamp alone."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        await self._send_ceiling_fan_setc(power=False)

    @override
    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed. Percentage 0 turns the fan off."""
        if percentage == 0:
            await self.async_turn_off()
            return
        if EPC_CEILING_FAN_AIR_FLOW_RATE not in self._node.set_epcs:
            return
        await self._send_ceiling_fan_setc(
            power=True,
            changes=[
                self.entity_description.air_flow_prop.make_property(
                    percentage_to_ordered_list_item(
                        _CEILING_FAN_SPEED_LEVELS, percentage
                    )
                )
            ],
        )

    @override
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set normal or natural wind without resending the speed."""
        if EPC_CEILING_FAN_NATURAL_WIND not in self._node.set_epcs:
            return
        await self._send_ceiling_fan_setc(
            power=True,
            changes=[
                self.entity_description.natural_wind_prop.make_property(
                    preset_mode == PRESET_MODE_NATURAL
                )
            ],
        )

    @override
    async def async_set_direction(self, direction: str) -> None:
        """Set air flow direction. Forward is down; reverse is up."""
        if EPC_CEILING_FAN_AIR_FLOW_DIRECTION not in self._node.set_epcs:
            return
        if (echonet_dir := _HA_DIRECTION_TO_CEILING_FAN.get(direction)) is None:
            return
        await self._send_ceiling_fan_setc(
            power=True,
            changes=[
                self.entity_description.direction_prop.make_property(echonet_dir)
            ],
        )
