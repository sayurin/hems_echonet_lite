"""Panasonic/KDK ceiling-fan lamp entity (class 0x013A).

Lamp power is EPC 0xF3, independent of fan operation status. Light SetC omits
``0x80`` and prefixes silent buzzer (``0xFC``) and Wi-Fi control source
(``0xFD``) so fan power is left unchanged.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, override

from pyhems import DeviceClass, NodeState, Property, get_codec_for_epc

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityDescription,
    LightEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from ...const import (
    EPC_CEILING_FAN_BRIGHTNESS,
    EPC_CEILING_FAN_BUZZER,
    EPC_CEILING_FAN_COLOR,
    EPC_CEILING_FAN_CONTROL_SOURCE,
    EPC_CEILING_FAN_LIGHT,
    EPC_CEILING_FAN_LIGHT_MODE,
    EPC_CEILING_FAN_NIGHT_LIGHTING,
)
from ...coordinator import EchonetLiteCoordinator
from ...entity import EchonetLiteEntity, setup_dedicated_platform
from ...prop import BinaryProp, EnumProp, NumericProp
from ...runtime import EchonetLiteConfigEntry
from .ceiling_fan import is_panasonic_ceiling_fan

_CLASS = DeviceClass.CEILING_FAN

# Light-only writes omit operation status; fan power is left unchanged.
CEILING_FAN_LIGHT_COMPANION_EPCS: frozenset[int] = frozenset(
    {
        EPC_CEILING_FAN_BUZZER,
        EPC_CEILING_FAN_CONTROL_SOURCE,
    }
)

# Continuous color (0 warm … 100 cool) kelvin endpoints.
_CEILING_FAN_MIN_KELVIN: Final = 2700
_CEILING_FAN_MAX_KELVIN: Final = 6500

# Night lighting level keys map to ECHONET bytes 0x01 / 0x32 / 0x64 (1 / 50 / 100).
_NIGHT_LIGHTING_TO_PCT: Final[dict[str, int]] = {
    "low": 1,
    "medium": 50,
    "high": 100,
}
_NIGHT_PCT_LEVELS: Final[tuple[int, ...]] = (1, 50, 100)
_NIGHT_PCT_TO_KEY: Final[dict[int, str]] = {
    pct: key for key, pct in _NIGHT_LIGHTING_TO_PCT.items()
}

_LIGHT_MODE_NORMAL = "normal"
_LIGHT_MODE_NIGHT = "night"


def _encode(epc: int, value: object) -> bytes:
    """Encode one framing EPC (buzzer / control source) with the catalog codec."""
    return get_codec_for_epc(_CLASS, epc).encode(value)


def frame_ceiling_fan_light_setc(
    *,
    changes: Sequence[Property],
    silent: bool = True,
) -> list[Property]:
    """Prefix ``0xFC`` + ``0xFD`` onto light property changes (no ``0x80``).

    Lamp EPCs are independent of fan operation status; omitting ``0x80`` leaves
    fan power unchanged. ``changes`` must be non-empty.
    """
    if not changes:
        raise ValueError("Light SetC requires at least one light property change")
    properties = [
        Property(
            epc=EPC_CEILING_FAN_BUZZER,
            edt=_encode(EPC_CEILING_FAN_BUZZER, not silent),
        ),
        Property(
            epc=EPC_CEILING_FAN_CONTROL_SOURCE,
            edt=_encode(EPC_CEILING_FAN_CONTROL_SOURCE, "wifi"),
        ),
    ]
    properties.extend(changes)
    return properties


def _brightness_pct_to_ha(pct: int) -> int:
    """Convert an ECHONET brightness percentage (0-100) to HA's 1-255 scale."""
    pct = max(0, min(100, pct))
    return max(1, round(pct * 255 / 100))


def _brightness_ha_to_pct(value: int) -> int:
    """Convert HA's 0-255 brightness scale to ECHONET percentage 1-100."""
    return max(1, min(100, round(value * 100 / 255)))


def _ceiling_fan_color_to_kelvin(color: int) -> int:
    """Map ceiling-fan color 0 (warm) … 100 (cool) to kelvin."""
    color = max(0, min(100, color))
    return round(
        _CEILING_FAN_MIN_KELVIN
        + color * (_CEILING_FAN_MAX_KELVIN - _CEILING_FAN_MIN_KELVIN) / 100
    )


def _kelvin_to_ceiling_fan_color(kelvin: int) -> int:
    """Map kelvin to ceiling-fan color 0 (warm) … 100 (cool)."""
    kelvin = max(_CEILING_FAN_MIN_KELVIN, min(_CEILING_FAN_MAX_KELVIN, kelvin))
    return round(
        (kelvin - _CEILING_FAN_MIN_KELVIN)
        * 100
        / (_CEILING_FAN_MAX_KELVIN - _CEILING_FAN_MIN_KELVIN)
    )


def _snap_night_lighting(ha_brightness: int) -> str:
    """Snap an HA brightness value to the nearest night-lighting key."""
    pct = _brightness_ha_to_pct(ha_brightness)
    nearest = min(_NIGHT_PCT_LEVELS, key=lambda level: abs(level - pct))
    return _NIGHT_PCT_TO_KEY[nearest]


def _epc_advertised(node: NodeState, epc: int) -> bool:
    """Return True if the EPC is in the node's get or set property map."""
    return epc in node.get_epcs or epc in node.set_epcs


@dataclass(frozen=True, kw_only=True)
class EchonetLiteCeilingFanLightEntityDescription(LightEntityDescription):
    """Description for the lamp on a ceiling fan (class 0x013A)."""

    light_prop: BinaryProp
    mode_prop: EnumProp
    brightness_prop: NumericProp
    color_prop: NumericProp
    night_lighting_prop: EnumProp


def _create_ceiling_fan_light_description() -> (
    EchonetLiteCeilingFanLightEntityDescription
):
    """Build a ceiling-fan lamp description from pyhems definitions."""
    class_code = DeviceClass.CEILING_FAN
    return EchonetLiteCeilingFanLightEntityDescription(
        key="light",
        translation_key="ceiling_fan_light",
        light_prop=BinaryProp.from_registry(class_code, EPC_CEILING_FAN_LIGHT),
        mode_prop=EnumProp.from_registry(class_code, EPC_CEILING_FAN_LIGHT_MODE),
        brightness_prop=NumericProp.from_registry(
            class_code, EPC_CEILING_FAN_BRIGHTNESS
        ),
        color_prop=NumericProp.from_registry(class_code, EPC_CEILING_FAN_COLOR),
        night_lighting_prop=EnumProp.from_registry(
            class_code, EPC_CEILING_FAN_NIGHT_LIGHTING
        ),
    )


_CEILING_FAN_LIGHT_DESCRIPTIONS: dict[
    int, EchonetLiteCeilingFanLightEntityDescription
] = {
    DeviceClass.CEILING_FAN: _create_ceiling_fan_light_description(),
}


def _ceiling_fan_has_light(
    node: NodeState,
    _description: EchonetLiteCeilingFanLightEntityDescription,
) -> bool:
    """Create the lamp entity only for Panasonic units that advertise EPC 0xF3."""
    return is_panasonic_ceiling_fan(node) and _epc_advertised(
        node, EPC_CEILING_FAN_LIGHT
    )


def setup_panasonic_ceiling_fan_light_platform(
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register Panasonic/KDK ceiling-fan lamp entities."""
    setup_dedicated_platform(
        entry,
        async_add_entities,
        Platform.LIGHT.value,
        _CEILING_FAN_LIGHT_DESCRIPTIONS,
        EchonetLiteCeilingFanLight,
        should_create=_ceiling_fan_has_light,
    )


class EchonetLiteCeilingFanLight(EchonetLiteEntity, LightEntity):
    """Lamp on an ECHONET Lite ceiling fan (class 0x013A).

    Lamp power is EPC 0xF3, not shared operation status. Writes use
    ``frame_ceiling_fan_light_setc`` so SetC carries silent buzzer and control
    source without fan operation status; fan power is left unchanged.
    """

    entity_description: EchonetLiteCeilingFanLightEntityDescription

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteCeilingFanLightEntityDescription,
    ) -> None:
        """Initialize the ceiling-fan lamp from the node's advertised EPCs."""
        super().__init__(coordinator, node)
        self.entity_description = description
        self._attr_unique_id = f"{node.device_key}-{description.key}"
        # Named "Light" under has_entity_name so the device shows
        # "Ceiling fan Light".
        self._attr_translation_key = description.translation_key

        lamp_epcs = frozenset(
            {
                EPC_CEILING_FAN_LIGHT,
                EPC_CEILING_FAN_LIGHT_MODE,
                EPC_CEILING_FAN_BRIGHTNESS,
                EPC_CEILING_FAN_COLOR,
                EPC_CEILING_FAN_NIGHT_LIGHTING,
            }
        )
        self._subscribed_epcs = lamp_epcs & node.get_epcs

        self._supports_brightness = (
            EPC_CEILING_FAN_BRIGHTNESS in node.set_epcs
            or EPC_CEILING_FAN_NIGHT_LIGHTING in node.set_epcs
        )
        self._supports_color_temp = EPC_CEILING_FAN_COLOR in node.set_epcs
        self._supports_effect = EPC_CEILING_FAN_LIGHT_MODE in node.set_epcs
        self._has_brightness_epc = _epc_advertised(node, EPC_CEILING_FAN_BRIGHTNESS)
        self._has_night_epc = _epc_advertised(node, EPC_CEILING_FAN_NIGHT_LIGHTING)
        self._has_color_epc = _epc_advertised(node, EPC_CEILING_FAN_COLOR)
        self._has_mode_epc = _epc_advertised(node, EPC_CEILING_FAN_LIGHT_MODE)

        if self._supports_color_temp:
            modes = {ColorMode.COLOR_TEMP}
        elif self._supports_brightness:
            modes = {ColorMode.BRIGHTNESS}
        else:
            modes = {ColorMode.ONOFF}
        self._attr_supported_color_modes = modes
        self._attr_color_mode = next(iter(modes))

        if self._supports_color_temp:
            self._attr_min_color_temp_kelvin = _CEILING_FAN_MIN_KELVIN
            self._attr_max_color_temp_kelvin = _CEILING_FAN_MAX_KELVIN

        if self._supports_effect:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = [
                key
                for key in (_LIGHT_MODE_NORMAL, _LIGHT_MODE_NIGHT)
                if key in description.mode_prop.options
            ]

    async def _send_ceiling_fan_light_setc(self, changes: list[Property]) -> None:
        """Send one silent light-only SetC (no fan operation status)."""
        await self._send_properties(
            frame_ceiling_fan_light_setc(changes=changes),
            allow_unadvertised_epcs=CEILING_FAN_LIGHT_COMPANION_EPCS,
        )

    def _current_light_mode(self) -> str | None:
        """Return the stored lighting mode key, or None if unavailable."""
        if not self._has_mode_epc:
            return None
        return self.entity_description.mode_prop.get(self._node)

    @property
    @override
    def is_on(self) -> bool | None:
        """Return True when the lamp (EPC 0xF3) is on."""
        return self.entity_description.light_prop.get(self._node)

    @property
    @override
    def brightness(self) -> int | None:
        """Return brightness from main level or night lighting level."""
        if not self._has_brightness_epc and not self._has_night_epc:
            return None
        mode = self._current_light_mode()
        if mode == _LIGHT_MODE_NIGHT and self._has_night_epc:
            key = self.entity_description.night_lighting_prop.get(self._node)
            if key is None:
                return None
            pct = _NIGHT_LIGHTING_TO_PCT.get(key)
            return None if pct is None else _brightness_pct_to_ha(pct)
        if self._has_brightness_epc:
            pct = self.entity_description.brightness_prop.get(self._node)
            return None if pct is None else _brightness_pct_to_ha(int(pct))
        return None

    @property
    @override
    def color_temp_kelvin(self) -> int | None:
        """Return warm–cool color as kelvin."""
        if not self._has_color_epc:
            return None
        color = self.entity_description.color_prop.get(self._node)
        return None if color is None else _ceiling_fan_color_to_kelvin(int(color))

    @property
    @override
    def effect(self) -> str | None:
        """Return main or night lighting mode."""
        if not self._has_mode_epc:
            return None
        return self._current_light_mode()

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the lamp on, preserving mode when known."""
        description = self.entity_description

        # Resolve the lighting mode. Prefer an explicit effect; otherwise keep
        # the stored mode so a bare toggle does not leave night mode.
        requested_effect = kwargs.get(ATTR_EFFECT)
        if (
            requested_effect is not None
            and EPC_CEILING_FAN_LIGHT_MODE in self._node.set_epcs
            and requested_effect in description.mode_prop.options
        ):
            light_mode: str | None = requested_effect
        else:
            light_mode = self._current_light_mode()

        brightness = kwargs.get(ATTR_BRIGHTNESS)
        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        night_lighting: str | None = None
        brightness_pct: int | None = None
        color_value: int | None = None

        if brightness is not None:
            target_mode = light_mode or _LIGHT_MODE_NORMAL
            if (
                target_mode == _LIGHT_MODE_NIGHT
                and EPC_CEILING_FAN_NIGHT_LIGHTING in self._node.set_epcs
            ):
                light_mode = _LIGHT_MODE_NIGHT
                night_lighting = _snap_night_lighting(int(brightness))
            elif EPC_CEILING_FAN_BRIGHTNESS in self._node.set_epcs:
                brightness_pct = _brightness_ha_to_pct(int(brightness))
                if light_mode is None:
                    light_mode = _LIGHT_MODE_NORMAL

        if kelvin is not None and EPC_CEILING_FAN_COLOR in self._node.set_epcs:
            color_value = _kelvin_to_ceiling_fan_color(int(kelvin))
            if light_mode is None:
                light_mode = _LIGHT_MODE_NORMAL

        changes: list[Property] = [description.light_prop.make_property(True)]
        if (
            light_mode is not None
            and EPC_CEILING_FAN_LIGHT_MODE in self._node.set_epcs
        ):
            changes.append(description.mode_prop.make_property(light_mode))
        if night_lighting is not None:
            changes.append(
                description.night_lighting_prop.make_property(night_lighting)
            )
        elif brightness_pct is not None:
            changes.append(description.brightness_prop.make_property(brightness_pct))
        if color_value is not None:
            changes.append(description.color_prop.make_property(color_value))

        await self._send_ceiling_fan_light_setc(changes)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the lamp off without changing fan power."""
        await self._send_ceiling_fan_light_setc(
            [self.entity_description.light_prop.make_property(False)],
        )
