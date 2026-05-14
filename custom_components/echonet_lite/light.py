"""Light platform for the HEMS Echonet Lite integration.

Supports the ECHONET Lite lighting classes:

* 0x0290 General lighting -- on/off (0x80), brightness (0xB0),
  color temperature presets (0xB1) and lighting mode / effect (0xB6).
* 0x0291 Mono-functional lighting -- on/off (0x80), brightness (0xB0).
* 0x02A3 Lighting system -- on/off (0x80), brightness (0xB0).
* 0x02A4 Extended lighting system -- on/off (0x80), brightness (0xB0).

A single :class:`EchonetLiteLight` implementation handles all four classes,
adapting its color mode and supported features to the device's advertised
GET/SET property map at instantiation time. Brightness is scaled between
the ECHONET 0-100% percentage in EPC 0xB0 and Home Assistant's 0-255
byte scale.
"""

from __future__ import annotations

from typing import Any, Final

from pyhems import NodeState

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CLASS_CODE_EXTENDED_LIGHTING_SYSTEM,
    CLASS_CODE_GENERAL_LIGHTING,
    CLASS_CODE_LIGHTING_SYSTEM,
    CLASS_CODE_MONO_FUNCTIONAL_LIGHTING,
    EPC_LIGHT_COLOR,
    EPC_LIGHT_LEVEL,
    EPC_LIGHTING_MODE,
    EPC_OPERATION_STATUS,
)
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_echonet_lite_device_platform
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1

# Class codes handled by this platform and their translation keys.
_CLASS_CODE_TO_TRANSLATION_KEY: Final[dict[int, str]] = {
    CLASS_CODE_GENERAL_LIGHTING: "general_lighting",
    CLASS_CODE_MONO_FUNCTIONAL_LIGHTING: "mono_functional_lighting",
    CLASS_CODE_LIGHTING_SYSTEM: "lighting_system",
    CLASS_CODE_EXTENDED_LIGHTING_SYSTEM: "extended_lighting_system",
}

# EPC 0x80 (Operation status) raw values.
_POWER_ON = 0x30
_POWER_OFF = 0x31

# Mapping between EPC 0xB1 (Light color setting) raw values and the
# kelvin presets exposed to Home Assistant.
_COLOR_RAW_TO_KELVIN: Final[dict[int, int]] = {
    0x41: 2700,  # Incandescent lamp color
    0x42: 4000,  # White
    0x43: 5000,  # Daylight white
    0x44: 6500,  # Daylight color
}
_KELVIN_TO_COLOR_RAW: Final[dict[int, int]] = {
    k: v for v, k in _COLOR_RAW_TO_KELVIN.items()
}
_MIN_KELVIN = min(_COLOR_RAW_TO_KELVIN.values())
_MAX_KELVIN = max(_COLOR_RAW_TO_KELVIN.values())

# Mapping between EPC 0xB6 (Lighting mode setting) raw values and effect names.
# The keys here match the snake_case forms used in strings.json under
# ``entity.light.general_lighting.state_attributes.effect.state``.
_MODE_RAW_TO_EFFECT: Final[dict[int, str]] = {
    0x41: "auto",
    0x42: "normal",
    0x43: "night",
    0x45: "color",
}
_EFFECT_TO_MODE_RAW: Final[dict[str, int]] = {
    name: raw for raw, name in _MODE_RAW_TO_EFFECT.items()
}


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


def _closest_kelvin_raw(kelvin: int) -> int:
    """Snap an arbitrary kelvin value to the closest supported preset.

    Home Assistant always sends a continuous value via
    ``ATTR_COLOR_TEMP_KELVIN``; the device only exposes four presets so we
    snap by absolute distance.
    """
    return _KELVIN_TO_COLOR_RAW[
        min(_KELVIN_TO_COLOR_RAW, key=lambda k: abs(k - kelvin))
    ]


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite light entities from a config entry."""

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        if node.eoj.class_code not in _CLASS_CODE_TO_TRANSLATION_KEY:
            return []
        return [EchonetLiteLight(coordinator, node)]

    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        entity_factory=_entity_factory,
    )


class EchonetLiteLight(EchonetLiteEntity, LightEntity):
    """Representation of an ECHONET Lite lighting device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
    ) -> None:
        """Initialize the light entity based on the node's advertised EPCs."""
        super().__init__(coordinator, node)
        class_code = node.eoj.class_code
        self._attr_unique_id = f"{node.device_key}-light"
        self._attr_translation_key = _CLASS_CODE_TO_TRANSLATION_KEY[class_code]

        # Determine supported color modes from the writable property map.
        # 0xB1 (color) implies brightness via 0xB0; if 0xB1 is missing but
        # 0xB0 is writable we still get BRIGHTNESS. Otherwise it's ONOFF.
        supports_brightness = EPC_LIGHT_LEVEL in node.set_epcs
        supports_color_temp = (
            class_code == CLASS_CODE_GENERAL_LIGHTING
            and EPC_LIGHT_COLOR in node.set_epcs
        )
        supports_effect = (
            class_code == CLASS_CODE_GENERAL_LIGHTING
            and EPC_LIGHTING_MODE in node.set_epcs
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

        if supports_effect:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = list(_MODE_RAW_TO_EFFECT.values())

        self._supports_brightness = supports_brightness
        self._supports_color_temp = supports_color_temp
        self._supports_effect = supports_effect

    def _raw(self, epc: int) -> int | None:
        """Return the single-byte raw value for ``epc`` if known."""
        if edt := self._node.properties.get(epc):
            return edt[0]
        return None

    @property
    def is_on(self) -> bool | None:
        """Return True if the device is reporting Operation status = ON."""
        power = self._raw(EPC_OPERATION_STATUS)
        if power is None:
            return None
        return power == _POWER_ON

    @property
    def brightness(self) -> int | None:
        """Return brightness on HA's 0-255 scale, derived from EPC 0xB0 (%)."""
        if not self._supports_brightness:
            return None
        pct = self._raw(EPC_LIGHT_LEVEL)
        if pct is None:
            return None
        return _brightness_pct_to_ha(pct)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the currently active color temperature preset in kelvin."""
        if not self._supports_color_temp:
            return None
        raw = self._raw(EPC_LIGHT_COLOR)
        if raw is None:
            return None
        return _COLOR_RAW_TO_KELVIN.get(raw)

    @property
    def effect(self) -> str | None:
        """Return the active lighting mode as the effect name."""
        if not self._supports_effect:
            return None
        raw = self._raw(EPC_LIGHTING_MODE)
        if raw is None:
            return None
        return _MODE_RAW_TO_EFFECT.get(raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, applying any brightness/color/effect overrides."""
        # Always send the power-on command first so subsequent setters apply
        # to an already-powered device.
        await self._async_send_property(EPC_OPERATION_STATUS, bytes([_POWER_ON]))
        if (
            self._supports_brightness
            and (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None
        ):
            pct = _brightness_ha_to_pct(int(brightness))
            await self._async_send_property(EPC_LIGHT_LEVEL, bytes([pct]))
        if (
            self._supports_color_temp
            and (kelvin := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None
        ):
            raw = _closest_kelvin_raw(int(kelvin))
            await self._async_send_property(EPC_LIGHT_COLOR, bytes([raw]))
        if (
            self._supports_effect
            and (effect := kwargs.get(ATTR_EFFECT)) is not None
            and effect in _EFFECT_TO_MODE_RAW
        ):
            await self._async_send_property(
                EPC_LIGHTING_MODE, bytes([_EFFECT_TO_MODE_RAW[effect]])
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off via EPC 0x80 = 0x31."""
        await self._async_send_property(EPC_OPERATION_STATUS, bytes([_POWER_OFF]))


__all__ = ["EchonetLiteLight"]
