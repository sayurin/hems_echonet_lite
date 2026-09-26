"""Light platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import Any, Final, override

from pyhems import DeviceClass, NodeState

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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .ceiling_fan import (
    CEILING_FAN_COMPANION_EPCS,
    build_ceiling_fan_properties,
)
from .const import (
    DEDICATED_PLATFORM_REQUIRED_EPCS,
    EPC_CEILING_FAN_BRIGHTNESS,
    EPC_CEILING_FAN_COLOR,
    EPC_CEILING_FAN_LIGHT,
    EPC_CEILING_FAN_LIGHT_MODE,
    EPC_CEILING_FAN_NIGHT_LIGHTING,
    EPC_LIGHT_COLOR,
    EPC_LIGHT_LEVEL,
    EPC_LIGHTING_MODE,
    EPC_OPERATION_STATUS,
)
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_dedicated_platform
from .prop import BinaryProp, EnumProp, NumericProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0

# Mapping between EPC 0xB1 (Light color setting) snake_case enum keys and the
# kelvin presets exposed to Home Assistant.
# Keys correspond to camel_to_snake() applied to the pyhems EnumCodec keys.
_COLOR_KEY_TO_KELVIN: Final[dict[str, int]] = {
    "incandescent": 2700,  # Incandescent lamp color
    "white": 4000,  # White
    "daylight_white": 5000,  # Daylight white
    "daylight_color": 6500,  # Daylight color
}
_KELVIN_TO_COLOR_KEY: Final[dict[int, str]] = {
    k: v for v, k in _COLOR_KEY_TO_KELVIN.items()
}
_MIN_KELVIN = min(_COLOR_KEY_TO_KELVIN.values())
_MAX_KELVIN = max(_COLOR_KEY_TO_KELVIN.values())

# Ceiling-fan continuous color (0 warm … 100 cool) uses the same kelvin
# endpoints as general lighting presets.
_CEILING_FAN_MIN_KELVIN: Final = _MIN_KELVIN
_CEILING_FAN_MAX_KELVIN: Final = _MAX_KELVIN

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


def _brightness_pct_to_ha(pct: int) -> int:
    """Convert an ECHONET brightness percentage (0-100) to HA's 1-255 scale."""
    pct = max(0, min(100, pct))
    return max(1, round(pct * 255 / 100))


def _brightness_ha_to_pct(value: int) -> int:
    """Convert HA's 0-255 brightness scale to ECHONET percentage 1-100.

    ECHONET Lite does not define brightness = 0%; "off" is expressed via
    EPC 0x80 instead. Clamp to at least 1 so a non-zero HA brightness is
    never silently truncated to a turn-off command.
    """
    return max(1, min(100, round(value * 100 / 255)))


def _closest_kelvin_key(kelvin: int) -> str:
    """Snap an arbitrary kelvin value to the closest supported preset key.

    Home Assistant always sends a continuous value via
    ``ATTR_COLOR_TEMP_KELVIN``; the device only exposes four presets so we
    snap by absolute distance.
    """
    return _KELVIN_TO_COLOR_KEY[
        min(_KELVIN_TO_COLOR_KEY, key=lambda k: abs(k - kelvin))
    ]


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
class EchonetLiteLightEntityDescription(LightEntityDescription):
    """Description for an ECHONET Lite lighting entity."""

    op_status: BinaryProp
    brightness_prop: NumericProp
    color_prop: EnumProp | None = None
    mode_prop: EnumProp | None = None


@dataclass(frozen=True, kw_only=True)
class EchonetLiteCeilingFanLightEntityDescription(LightEntityDescription):
    """Description for the lamp on a ceiling fan (class 0x013A)."""

    light_prop: BinaryProp
    mode_prop: EnumProp
    brightness_prop: NumericProp
    color_prop: NumericProp
    night_lighting_prop: EnumProp


def _create_light_description(
    class_code: int,
    translation_key: str,
    *,
    build_color: bool = False,
    build_mode: bool = False,
) -> EchonetLiteLightEntityDescription:
    """Build a light description from pyhems definitions."""
    return EchonetLiteLightEntityDescription(
        key="light",
        translation_key=translation_key,
        op_status=BinaryProp.from_registry(class_code, EPC_OPERATION_STATUS),
        brightness_prop=NumericProp.from_registry(class_code, EPC_LIGHT_LEVEL),
        color_prop=(
            EnumProp.from_registry(class_code, EPC_LIGHT_COLOR) if build_color else None
        ),
        mode_prop=(
            EnumProp.from_registry(class_code, EPC_LIGHTING_MODE)
            if build_mode
            else None
        ),
    )


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


_DESCRIPTIONS: dict[int, EchonetLiteLightEntityDescription] = {
    DeviceClass.GENERAL_LIGHTING: _create_light_description(
        DeviceClass.GENERAL_LIGHTING,
        "general_lighting",
        build_color=True,
        build_mode=True,
    ),
    DeviceClass.MONO_FUNCTIONAL_LIGHTING: _create_light_description(
        DeviceClass.MONO_FUNCTIONAL_LIGHTING, "mono_functional_lighting"
    ),
}

_CEILING_FAN_LIGHT_DESCRIPTIONS: dict[
    int, EchonetLiteCeilingFanLightEntityDescription
] = {
    DeviceClass.CEILING_FAN: _create_ceiling_fan_light_description(),
}


def _ceiling_fan_has_light(
    node: NodeState,
    _description: EchonetLiteCeilingFanLightEntityDescription,
) -> bool:
    """Create the lamp entity only when EPC 0xF3 is advertised."""
    return _epc_advertised(node, EPC_CEILING_FAN_LIGHT)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite light entities from a config entry."""
    setup_dedicated_platform(
        entry,
        async_add_entities,
        Platform.LIGHT.value,
        _DESCRIPTIONS,
        EchonetLiteLight,
    )
    setup_dedicated_platform(
        entry,
        async_add_entities,
        Platform.LIGHT.value,
        _CEILING_FAN_LIGHT_DESCRIPTIONS,
        EchonetLiteCeilingFanLight,
        should_create=_ceiling_fan_has_light,
    )


class EchonetLiteLight(EchonetLiteEntity, LightEntity):
    """Representation of an ECHONET Lite lighting device."""

    _attr_name = None
    entity_description: EchonetLiteLightEntityDescription

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteLightEntityDescription,
    ) -> None:
        """Initialize the light entity based on the node's advertised EPCs."""
        super().__init__(coordinator, node)
        self.entity_description = description
        self._attr_unique_id = f"{node.device_key}-{description.key}"
        self._subscribed_epcs = DEDICATED_PLATFORM_REQUIRED_EPCS.get(
            node.eoj.class_code, frozenset()
        )

        # Determine supported color modes from the writable property map.
        # 0xB1 (color) implies brightness via 0xB0; if 0xB1 is missing but
        # 0xB0 is writable we still get BRIGHTNESS. Otherwise it's ONOFF.
        supports_brightness = EPC_LIGHT_LEVEL in node.set_epcs
        supports_color_temp = (
            description.color_prop is not None and EPC_LIGHT_COLOR in node.set_epcs
        )
        supports_effect = (
            description.mode_prop is not None and EPC_LIGHTING_MODE in node.set_epcs
        )

        if supports_color_temp:
            modes = {ColorMode.COLOR_TEMP}
        elif supports_brightness:
            modes = {ColorMode.BRIGHTNESS}
        else:
            modes = {ColorMode.ONOFF}
        self._attr_supported_color_modes = modes
        self._attr_color_mode = next(iter(modes))

        if supports_color_temp:
            self._attr_min_color_temp_kelvin = _MIN_KELVIN
            self._attr_max_color_temp_kelvin = _MAX_KELVIN

        self._supports_brightness = supports_brightness
        self._supports_color_temp = supports_color_temp
        self._supports_effect = supports_effect

        if supports_effect:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = description.mode_prop.options  # type: ignore[union-attr]

    @property
    @override
    def is_on(self) -> bool | None:
        """Return True if the device is reporting Operation status = ON."""
        return self.entity_description.op_status.get(self._node)

    @property
    @override
    def brightness(self) -> int | None:
        """Return brightness on HA's 0-255 scale, derived from EPC 0xB0 (%)."""
        if not self._supports_brightness:
            return None
        pct = self.entity_description.brightness_prop.get(self._node)
        return None if pct is None else _brightness_pct_to_ha(int(pct))

    @property
    @override
    def color_temp_kelvin(self) -> int | None:
        """Return the currently active color temperature preset in kelvin."""
        if not self._supports_color_temp:
            return None
        key = self.entity_description.color_prop.get(self._node)  # type: ignore[union-attr]
        return None if key is None else _COLOR_KEY_TO_KELVIN.get(key)

    @property
    @override
    def effect(self) -> str | None:
        """Return the active lighting mode as the effect name."""
        if not self._supports_effect:
            return None
        return self.entity_description.mode_prop.get(self._node)  # type: ignore[union-attr]

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, applying any brightness/color/effect overrides."""
        # Always send the power-on command first so subsequent setters apply
        # to an already-powered device.
        self._send_prop(self.entity_description.op_status, True)
        if (
            self._supports_brightness
            and (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None
        ):
            pct = _brightness_ha_to_pct(int(brightness))
            self._send_prop(self.entity_description.brightness_prop, float(pct))
        if (
            self._supports_color_temp
            and (kelvin := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None
        ):
            self._send_prop(
                self.entity_description.color_prop,  # type: ignore[arg-type]
                _closest_kelvin_key(int(kelvin)),
            )
        if (
            self._supports_effect
            and (effect := kwargs.get(ATTR_EFFECT)) is not None
            and effect in self.entity_description.mode_prop.options  # type: ignore[union-attr]
        ):
            self._send_prop(
                self.entity_description.mode_prop,  # type: ignore[arg-type]
                effect,
            )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off via the operation status codec."""
        self._send_prop(self.entity_description.op_status, False)


class EchonetLiteCeilingFanLight(EchonetLiteEntity, LightEntity):
    """Lamp on an ECHONET Lite ceiling fan (class 0x013A).

    Lamp power is EPC 0xF3, not shared operation status. Every write goes
    through ``ceiling_fan_set_properties`` with ``power=True`` (fan on) so the
    helper can include Wi-Fi control source, buzzer, and melody.
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

    def _send_ceiling_fan(self, **kwargs: Any) -> None:
        """Build and send one silent ceiling-fan SetC."""
        self._send_properties(
            build_ceiling_fan_properties(**kwargs),
            allow_unadvertised_epcs=CEILING_FAN_COMPANION_EPCS,
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
        write: dict[str, Any] = {"power": True, "light": True}

        # Resolve the lighting mode. Prefer an explicit effect; otherwise keep
        # the stored mode so a bare toggle does not leave night mode.
        requested_effect = kwargs.get(ATTR_EFFECT)
        if (
            requested_effect is not None
            and EPC_CEILING_FAN_LIGHT_MODE in self._node.set_epcs
            and requested_effect in self.entity_description.mode_prop.options
        ):
            light_mode = requested_effect
        else:
            light_mode = self._current_light_mode()

        brightness = kwargs.get(ATTR_BRIGHTNESS)
        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)

        if light_mode is not None and EPC_CEILING_FAN_LIGHT_MODE in self._node.set_epcs:
            write["light_mode"] = light_mode

        if brightness is not None:
            target_mode = light_mode or _LIGHT_MODE_NORMAL
            if (
                target_mode == _LIGHT_MODE_NIGHT
                and EPC_CEILING_FAN_NIGHT_LIGHTING in self._node.set_epcs
            ):
                write["light_mode"] = _LIGHT_MODE_NIGHT
                write["night_lighting"] = _snap_night_lighting(int(brightness))
            elif EPC_CEILING_FAN_BRIGHTNESS in self._node.set_epcs:
                write["brightness"] = _brightness_ha_to_pct(int(brightness))
                if "light_mode" not in write:
                    write["light_mode"] = _LIGHT_MODE_NORMAL

        if kelvin is not None and EPC_CEILING_FAN_COLOR in self._node.set_epcs:
            write["color"] = _kelvin_to_ceiling_fan_color(int(kelvin))
            if "light_mode" not in write:
                write["light_mode"] = _LIGHT_MODE_NORMAL

        self._send_ceiling_fan(**write)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the lamp off. Fan power stays on (helper requires power=True)."""
        self._send_ceiling_fan(power=True, light=False)
