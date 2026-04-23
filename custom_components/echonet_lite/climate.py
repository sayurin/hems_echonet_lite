"""Climate platform for the HEMS Echonet Lite integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any, TypeVar

from pyhems import (
    CLASS_CODE_HOME_AIR_CONDITIONER,
    DefinitionsRegistry,
    NodeState,
    Property,
    create_numeric_decoder,
    create_numeric_encoder,
)

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    FAN_AUTO,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_VERTICAL,
    ClimateEntity,
    ClimateEntityDescription,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_echonet_lite_device_platform
from .types import EchonetLiteConfigEntry

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

PARALLEL_UPDATES = 1

# Climate class codes (local to this platform)
CLIMATE_CLASS_CODES: frozenset[int] = frozenset({CLASS_CODE_HOME_AIR_CONDITIONER})

# Climate-specific EPCs (local to this platform)
EPC_OPERATION_STATUS = 0x80
EPC_FAN_SPEED = 0xA0
EPC_SWING_AIR_FLOW = 0xA3
EPC_SPECIAL_STATE = 0xAA
EPC_OPERATION_MODE = 0xB0
EPC_TARGET_TEMPERATURE = 0xB3
EPC_ROOM_HUMIDITY = 0xBA
EPC_ROOM_TEMPERATURE = 0xBB

_SUPPORTED_HVAC_MODES: list[HVACMode] = [
    HVACMode.OFF,
    HVACMode.AUTO,
    HVACMode.COOL,
    HVACMode.HEAT,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
]

# Mapping between HA ``HVACMode`` and ECHONET Lite operation mode (EPC 0xB0).
#
# ECHONET Lite models operation status (0x80 = ON/OFF) and operation mode
# (0xB0) as independent axes. Value 0xB0 = 0x40 ("other") is used by some
# appliances as a persistent, vendor-defined mode (internal clean, coil
# drying, air purification, etc.) that has no direct equivalent in Home
# Assistant's ``HVACMode`` enum. We map it to ``HVACMode.FAN_ONLY`` +
# ``HVACAction.IDLE`` so that:
#   - ``hvac_mode`` stays a valid enum value (avoids a chronically
#     "unknown" entity on devices that sit in 0x40 for long periods);
#   - 0x45 ("fan only") remains distinguishable because it maps to
#     ``HVACAction.FAN`` while 0x40 maps to ``HVACAction.IDLE``.
# 0x40 is not written back by this integration; ``async_set_hvac_mode``
# only writes values present in ``_HA_TO_ECHONET_MODE``.
_HA_TO_ECHONET_MODE: dict[HVACMode, int] = {
    HVACMode.AUTO: 0x41,
    HVACMode.COOL: 0x42,
    HVACMode.HEAT: 0x43,
    HVACMode.DRY: 0x44,
    HVACMode.FAN_ONLY: 0x45,
}
_ECHONET_TO_HA_MODE: dict[int, HVACMode] = {
    0x40: HVACMode.FAN_ONLY,  # "other" — vendor-defined; see module comment
    0x41: HVACMode.AUTO,
    0x42: HVACMode.COOL,
    0x43: HVACMode.HEAT,
    0x44: HVACMode.DRY,
    0x45: HVACMode.FAN_ONLY,
}

_ECHONET_TO_HA_ACTION: dict[int, HVACAction | None] = {
    0x40: HVACAction.IDLE,  # "other" — distinguishes from 0x45 (FAN)
    0x41: None,  # auto — see _infer_auto_action()
    0x42: HVACAction.COOLING,
    0x43: HVACAction.HEATING,
    0x44: HVACAction.DRYING,
    0x45: HVACAction.FAN,
}

# Special state mapping (EPC 0xAA)
_ECHONET_SPECIAL_STATE_TO_ACTION: dict[int, HVACAction | None] = {
    0x40: None,  # normal — falls through to operation mode logic
    0x41: HVACAction.DEFROSTING,
    0x42: HVACAction.PREHEATING,
    0x43: HVACAction.IDLE,  # heat removal
}

# Fan speed mapping (0xA0 Air flow rate setting)
_HA_TO_ECHONET_FAN: dict[str, int] = {
    FAN_AUTO: 0x41,
    "level_1": 0x31,
    "level_2": 0x32,
    "level_3": 0x33,
    "level_4": 0x34,
    "level_5": 0x35,
    "level_6": 0x36,
    "level_7": 0x37,
    "level_8": 0x38,
}
_ECHONET_TO_HA_FAN = {v: k for k, v in _HA_TO_ECHONET_FAN.items()}

# Swing mode mapping (0xA3 Swing direction setting)
_HA_TO_ECHONET_SWING: dict[str, int] = {
    SWING_OFF: 0x31,
    SWING_VERTICAL: 0x41,
    SWING_HORIZONTAL: 0x42,
    SWING_BOTH: 0x43,
}
_ECHONET_TO_HA_SWING = {v: k for k, v in _HA_TO_ECHONET_SWING.items()}


def _decode_unsigned_temperature(edt: bytes) -> float | None:
    """Fallback decoder used only when no EPC 0xB3 definition is available."""
    if len(edt) != 1:
        return None
    value = edt[0]
    return None if value == 0xFD else float(value)


def _precision_from_scale(scale: float) -> float:
    """Return the closest HA precision constant for a definition scale."""
    if scale <= PRECISION_TENTHS:
        return PRECISION_TENTHS
    if scale <= PRECISION_HALVES:
        return PRECISION_HALVES
    return PRECISION_WHOLE


@dataclass(frozen=True, kw_only=True)
class EchonetLiteClimateEntityDescription(ClimateEntityDescription):
    """Climate description scoped to an ECHONET Lite class code.

    pyhems definitions are protocol-level metadata shared across all devices
    of a given class code (e.g. every 0x0130 Home Air Conditioner exposes the
    same 0xB3 target-temperature range and format). We therefore build one
    description per class code at ``async_setup_entry`` time and share it
    across every room/instance discovered on the network, rather than
    regenerating codecs inside each entity's ``__init__``.
    """

    class_code: int
    target_temp_encoder: Callable[[float | int], bytes] | None = None
    target_temp_decoder: Callable[[bytes], float | int | None] = (
        _decode_unsigned_temperature
    )
    target_temp_min: float | None = None
    target_temp_max: float | None = None
    target_temp_step: float | None = None
    target_temp_precision: float = PRECISION_WHOLE


def _create_climate_description(
    class_code: int,
    definitions: DefinitionsRegistry,
) -> EchonetLiteClimateEntityDescription:
    """Build a climate description from pyhems definitions.

    Falls back to conservative defaults (unsigned-byte 0..50 decoder, no
    encoder) when the definitions do not describe EPC 0xB3 for this class.
    """
    for entity_def in definitions.entities.get(class_code, ()):
        if entity_def.epc != EPC_TARGET_TEMPERATURE or entity_def.format is None:
            continue
        scale = entity_def.multiple_of
        target_temp_min = (
            entity_def.minimum * scale if entity_def.minimum is not None else None
        )
        target_temp_max = (
            entity_def.maximum * scale if entity_def.maximum is not None else None
        )
        return EchonetLiteClimateEntityDescription(
            key="climate",
            class_code=class_code,
            target_temp_decoder=create_numeric_decoder(
                mra_format=entity_def.format,
                minimum=entity_def.minimum,
                maximum=entity_def.maximum,
                scale=scale,
                byte_offset=entity_def.byte_offset,
            ),
            target_temp_encoder=create_numeric_encoder(
                mra_format=entity_def.format,
                scale=scale,
            ),
            target_temp_min=target_temp_min,
            target_temp_max=target_temp_max,
            target_temp_step=scale,
            target_temp_precision=_precision_from_scale(scale),
        )
    return EchonetLiteClimateEntityDescription(
        key="climate",
        class_code=class_code,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite climate entities from a config entry."""
    definitions = entry.runtime_data.definitions
    descriptions: dict[int, EchonetLiteClimateEntityDescription] = {
        class_code: _create_climate_description(class_code, definitions)
        for class_code in CLIMATE_CLASS_CODES
    }

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        description = descriptions.get(node.eoj.class_code)
        if description is None:
            return []
        return [EchonetLiteClimate(coordinator, node, description)]

    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        entity_factory=_entity_factory,
    )


class EchonetLiteClimate(EchonetLiteEntity, ClimateEntity):
    """Representation of an ECHONET Lite HVAC device.

    This implementation uses a property caching pattern via async_update() to
    efficiently manage the many climate entity properties. Instead of calling
    _get_property() separately for each property getter (hvac_mode, target_temperature,
    fan_mode, etc.).

    This approach provides several benefits:
    - Reduces multiple property lookups to a single batch operation per update cycle
    - Ensures consistency across all properties within a single update cycle
    - Aligns with Home Assistant's recommended entity update patterns
    - Simplifies property getters (no complex logic, just return cached values)
    """

    entity_description: EchonetLiteClimateEntityDescription
    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_translation_key = "climate"
    _attr_precision: float = PRECISION_WHOLE
    _attr_hvac_modes = _SUPPORTED_HVAC_MODES
    _attr_fan_modes = list(_HA_TO_ECHONET_FAN.keys())

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteClimateEntityDescription,
    ) -> None:
        """Initialize an ECHONET Lite climate entity."""
        super().__init__(coordinator, node)
        self.entity_description = description
        self._attr_unique_id = f"{node.device_key}-{description.key}"
        if description.target_temp_min is not None:
            self._attr_min_temp = description.target_temp_min
        if description.target_temp_max is not None:
            self._attr_max_temp = description.target_temp_max
        if description.target_temp_step is not None:
            self._attr_target_temperature_step = description.target_temp_step
        self._attr_precision = description.target_temp_precision
        features = ClimateEntityFeature(0)
        swing_modes: list[str] | None = None
        if EPC_TARGET_TEMPERATURE in node.set_epcs:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if EPC_FAN_SPEED in node.set_epcs:
            features |= ClimateEntityFeature.FAN_MODE
        if EPC_SWING_AIR_FLOW in node.set_epcs:
            features |= ClimateEntityFeature.SWING_MODE
            swing_modes = list(_HA_TO_ECHONET_SWING.keys())
        if EPC_OPERATION_STATUS in node.set_epcs:
            features |= ClimateEntityFeature.TURN_ON
            features |= ClimateEntityFeature.TURN_OFF
        self._attr_supported_features = features
        self._attr_swing_modes = swing_modes

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current HVAC mode."""
        status = self._get_value(EPC_OPERATION_STATUS, lambda edt: edt[0])
        if status == 0x30:
            return self._get_value(
                EPC_OPERATION_MODE, lambda edt: _ECHONET_TO_HA_MODE.get(edt[0])
            )
        if status == 0x31:
            return HVACMode.OFF
        return None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action."""
        special_raw = self._get_value(EPC_SPECIAL_STATE, lambda edt: edt[0])
        if special_raw is not None and special_raw in _ECHONET_SPECIAL_STATE_TO_ACTION:
            if (action := _ECHONET_SPECIAL_STATE_TO_ACTION[special_raw]) is not None:
                return action
        status = self._get_value(EPC_OPERATION_STATUS, lambda edt: edt[0])
        if status == 0x31:
            return HVACAction.OFF
        if status != 0x30:
            return None
        mode = self._get_value(EPC_OPERATION_MODE, lambda edt: edt[0])
        if mode is not None and mode in _ECHONET_TO_HA_ACTION:
            if (action := _ECHONET_TO_HA_ACTION[mode]) is not None:
                return action
            return self._infer_auto_action()
        return None

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        return self._get_value(
            EPC_FAN_SPEED, lambda edt: _ECHONET_TO_HA_FAN.get(edt[0])
        )

    @property
    def swing_mode(self) -> str | None:
        """Return the current swing mode based on vertical/horizontal settings."""
        return self._get_value(
            EPC_SWING_AIR_FLOW, lambda edt: _ECHONET_TO_HA_SWING.get(edt[0])
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the measured indoor temperature."""
        return self._get_value(EPC_ROOM_TEMPERATURE, _SIGNED_BYTE_TEMPERATURE_DECODER)

    @property
    def current_humidity(self) -> float | None:
        """Return the measured indoor relative humidity."""
        return self._get_value(EPC_ROOM_HUMIDITY, _HUMIDITY_DECODER)

    @property
    def target_temperature(self) -> float | None:
        """Return the currently configured setpoint."""
        value = self._get_value(
            EPC_TARGET_TEMPERATURE, self.entity_description.target_temp_decoder
        )
        return float(value) if value is not None else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the requested HVAC mode."""
        _LOGGER.debug(
            "async_set_hvac_mode: Requested mode=%s, current mode=%s",
            hvac_mode,
            self.hvac_mode,
        )
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return

        if EPC_OPERATION_MODE not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_mode_not_writable",
            )
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_status_not_writable",
            )
        echonet_mode = _HA_TO_ECHONET_MODE.get(hvac_mode)
        if echonet_mode is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={"hvac_mode": str(hvac_mode)},
            )
        await self._async_send_properties(
            [
                Property(epc=EPC_OPERATION_MODE, edt=bytes([echonet_mode])),
                Property(epc=EPC_OPERATION_STATUS, edt=b"\x30"),
            ]
        )

    async def async_turn_on(self) -> None:
        """Turn on the climate device."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_status_not_writable",
            )
        await self._async_send_property(EPC_OPERATION_STATUS, b"\x30")

    async def async_turn_off(self) -> None:
        """Turn off the climate device."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_status_not_writable",
            )
        await self._async_send_property(EPC_OPERATION_STATUS, b"\x31")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature for the current mode."""
        if ATTR_TEMPERATURE not in kwargs or kwargs[ATTR_TEMPERATURE] is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="target_temperature_required",
            )
        encoder = self.entity_description.target_temp_encoder
        if encoder is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_mode_not_writable",
            )
        temperature = float(kwargs[ATTR_TEMPERATURE])
        clamped = min(max(temperature, self._attr_min_temp), self._attr_max_temp)
        await self._async_send_property(EPC_TARGET_TEMPERATURE, encoder(clamped))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan mode."""
        fan_value = _HA_TO_ECHONET_FAN.get(fan_mode)
        if fan_value is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_fan_mode",
                translation_placeholders={"fan_mode": fan_mode},
            )
        await self._async_send_property(EPC_FAN_SPEED, bytes([fan_value]))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set the swing mode."""
        swing_value = _HA_TO_ECHONET_SWING.get(swing_mode)
        if swing_value is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_swing_mode",
                translation_placeholders={"swing_mode": swing_mode},
            )
        await self._async_send_property(EPC_SWING_AIR_FLOW, bytes([swing_value]))

    def _infer_auto_action(self) -> HVACAction:
        """Infer HVAC action for AUTO mode from temperatures."""
        target = self.target_temperature
        current = self.current_temperature
        if target is None or current is None:
            return HVACAction.IDLE
        if target <= current:
            return HVACAction.COOLING
        return HVACAction.HEATING

    def _get_value(self, epc: int, converter: Callable[[bytes], _T]) -> _T | None:
        """Helper to get and decode a property value from the node."""
        if edt := self._node.properties.get(epc):
            return converter(edt)
        return None


# Decoder for signed byte temperature (ECHONET Lite specification)
# min/max -127 to 125 excludes special values: 0x7E (126: immeasurable),
# 0x7F (127: overflow), 0x80 (-128: underflow)
_SIGNED_BYTE_TEMPERATURE_DECODER = create_numeric_decoder(
    mra_format="int8", minimum=-127, maximum=125
)

# Decoder for unsigned byte humidity (ECHONET Lite specification)
# Range 0-100%, 0xFD (253) and above are special/overflow values
_HUMIDITY_DECODER = create_numeric_decoder(mra_format="uint8", minimum=0, maximum=100)


__all__ = ["EchonetLiteClimate"]
