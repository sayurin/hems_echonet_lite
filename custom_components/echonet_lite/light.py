"""Light platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import Any, Final

from pyhems import NodeState

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityDescription,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CLASS_CODE_GENERAL_LIGHTING as CC_GENERAL,
    CLASS_CODE_MONO_FUNCTIONAL_LIGHTING as CC_MONO,
    EPC_LIGHT_COLOR,
    EPC_LIGHT_LEVEL,
    EPC_LIGHTING_MODE,
    EPC_OPERATION_STATUS,
)
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_dedicated_platform
from .prop import BinaryProp, EnumProp, NumericProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1

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


@dataclass(frozen=True, kw_only=True)
class EchonetLiteLightEntityDescription(LightEntityDescription):
    """Description for an ECHONET Lite lighting entity."""

    op_status: BinaryProp
    brightness_prop: NumericProp
    color_prop: EnumProp | None = None
    mode_prop: EnumProp | None = None


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


_DESCRIPTIONS: dict[int, EchonetLiteLightEntityDescription] = {
    CC_GENERAL: _create_light_description(
        CC_GENERAL, "general_lighting", build_color=True, build_mode=True
    ),
    CC_MONO: _create_light_description(CC_MONO, "mono_functional_lighting"),
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite light entities from a config entry."""
    setup_dedicated_platform(entry, async_add_entities, _DESCRIPTIONS, EchonetLiteLight)


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
    def is_on(self) -> bool | None:
        """Return True if the device is reporting Operation status = ON."""
        return self.entity_description.op_status.get(self._node)

    @property
    def brightness(self) -> int | None:
        """Return brightness on HA's 0-255 scale, derived from EPC 0xB0 (%)."""
        if not self._supports_brightness:
            return None
        pct = self.entity_description.brightness_prop.get(self._node)
        return None if pct is None else _brightness_pct_to_ha(int(pct))

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the currently active color temperature preset in kelvin."""
        if not self._supports_color_temp:
            return None
        key = self.entity_description.color_prop.get(self._node)  # type: ignore[union-attr]
        return None if key is None else _COLOR_KEY_TO_KELVIN.get(key)

    @property
    def effect(self) -> str | None:
        """Return the active lighting mode as the effect name."""
        if not self._supports_effect:
            return None
        return self.entity_description.mode_prop.get(self._node)  # type: ignore[union-attr]

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, applying any brightness/color/effect overrides."""
        # Always send the power-on command first so subsequent setters apply
        # to an already-powered device.
        await self._async_send_prop(self.entity_description.op_status, True)
        if (
            self._supports_brightness
            and (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None
        ):
            pct = _brightness_ha_to_pct(int(brightness))
            await self._async_send_prop(
                self.entity_description.brightness_prop, float(pct)
            )
        if (
            self._supports_color_temp
            and (kelvin := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None
        ):
            await self._async_send_prop(
                self.entity_description.color_prop,  # type: ignore[arg-type]
                _closest_kelvin_key(int(kelvin)),
            )
        if (
            self._supports_effect
            and (effect := kwargs.get(ATTR_EFFECT)) is not None
            and effect in self.entity_description.mode_prop.options  # type: ignore[union-attr]
        ):
            await self._async_send_prop(
                self.entity_description.mode_prop,  # type: ignore[arg-type]
                effect,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off via the operation status codec."""
        await self._async_send_prop(self.entity_description.op_status, False)
