"""Climate platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
import logging
from typing import Any

from pyhems import DefinitionsRegistry, NodeState

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
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

from .const import (
    CLASS_CODE_HOME_AIR_CONDITIONER,
    DOMAIN,
    EPC_FAN_SPEED,
    EPC_OPERATION_MODE,
    EPC_OPERATION_STATUS,
    EPC_ROOM_HUMIDITY,
    EPC_ROOM_TEMPERATURE,
    EPC_SPECIAL_STATE,
    EPC_SWING_AIR_FLOW,
    EPC_TARGET_TEMPERATURE,
)
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_echonet_lite_device_platform
from .prop import BinaryProp, EnumProp, NumericProp
from .types import EchonetLiteConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# Class codes handled by this platform
CLIMATE_CLASS_CODES: frozenset[int] = frozenset({CLASS_CODE_HOME_AIR_CONDITIONER})

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
# (0xB0) as independent axes. Value 0xB0 = 0x40 (pyhems key ``"other"``) is
# used by some appliances as a persistent, vendor-defined mode (internal
# clean, coil drying, air purification, etc.) that has no direct equivalent
# in Home Assistant's ``HVACMode`` enum. We map it to ``HVACMode.FAN_ONLY``
# + ``HVACAction.IDLE`` so that:
#   - ``hvac_mode`` stays a valid enum value (avoids a chronically
#     "unknown" entity on devices that sit in "other" for long periods);
#   - "circulation" (0x45, fan-only) remains distinguishable because it
#     maps to ``HVACAction.FAN`` while "other" maps to ``HVACAction.IDLE``.
# "other" is not written back by this integration; ``async_set_hvac_mode``
# only writes values present in ``_HA_TO_PYHEMS_MODE``.
_HA_TO_PYHEMS_MODE: dict[HVACMode, str] = {
    HVACMode.AUTO: "auto",
    HVACMode.COOL: "cooling",
    HVACMode.HEAT: "heating",
    HVACMode.DRY: "dehumidification",
    HVACMode.FAN_ONLY: "circulation",
}

# pyhems key (from EnumCodec) → HA mode. Covers "other" (0x40) read-only mode.
_PYHEMS_TO_HA_MODE: dict[str, HVACMode] = {
    "other": HVACMode.FAN_ONLY,  # vendor-defined; see comment above
    "auto": HVACMode.AUTO,
    "cooling": HVACMode.COOL,
    "heating": HVACMode.HEAT,
    "dehumidification": HVACMode.DRY,
    "circulation": HVACMode.FAN_ONLY,
}

# pyhems key → HA action for EPC 0xB0 (operation mode).
_PYHEMS_TO_HA_ACTION: dict[str, HVACAction | None] = {
    "other": HVACAction.IDLE,  # distinguishes from "circulation" (FAN)
    "auto": None,  # see _infer_auto_action()
    "cooling": HVACAction.COOLING,
    "heating": HVACAction.HEATING,
    "dehumidification": HVACAction.DRYING,
    "circulation": HVACAction.FAN,
}

# pyhems key → HA action for EPC 0xAA (special state).
_PYHEMS_SPECIAL_STATE_TO_ACTION: dict[str, HVACAction | None] = {
    "normal": None,  # falls through to operation mode logic
    "defrosting": HVACAction.DEFROSTING,
    "preheating": HVACAction.PREHEATING,
    "heat_removal": HVACAction.IDLE,  # heat removal
}

# Swing mode mapping (0xA3 Swing direction setting)
_HA_TO_ECHONET_SWING: dict[str, int] = {
    SWING_OFF: 0x31,
    SWING_VERTICAL: 0x41,
    SWING_HORIZONTAL: 0x42,
    SWING_BOTH: 0x43,
}


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
    target_temp_prop: NumericProp
    target_temp_min: float | None = None
    target_temp_max: float | None = None
    target_temp_step: float | None = None
    target_temp_precision: float = PRECISION_WHOLE
    room_temp_prop: NumericProp
    humidity_prop: NumericProp
    fan_mode_prop: EnumProp
    swing_mode_prop: EnumProp


def _create_climate_description(
    class_code: int,
    definitions: DefinitionsRegistry,
) -> EchonetLiteClimateEntityDescription:
    """Build a climate description from pyhems definitions.

    get_codec_for_epc is guaranteed by pyhems test_platform_epc_codec_type
    to return NumericCodec for the EPCs used here (0xB3, 0xBB, 0xBA on class 0x0130).
    """
    target_temp_prop = NumericProp.from_registry(
        definitions, class_code, EPC_TARGET_TEMPERATURE
    )
    room_temp_prop = NumericProp.from_registry(
        definitions, class_code, EPC_ROOM_TEMPERATURE
    )
    humidity_prop = NumericProp.from_registry(
        definitions, class_code, EPC_ROOM_HUMIDITY
    )

    scale = target_temp_prop.codec.scale
    return EchonetLiteClimateEntityDescription(
        key="climate",
        class_code=class_code,
        target_temp_prop=target_temp_prop,
        target_temp_min=(
            target_temp_prop.codec.minimum * scale
            if target_temp_prop.codec.minimum is not None
            else None
        ),
        target_temp_max=(
            target_temp_prop.codec.maximum * scale
            if target_temp_prop.codec.maximum is not None
            else None
        ),
        target_temp_step=scale,
        target_temp_precision=_precision_from_scale(scale),
        room_temp_prop=room_temp_prop,
        humidity_prop=humidity_prop,
        fan_mode_prop=EnumProp.from_registry(definitions, class_code, EPC_FAN_SPEED),
        swing_mode_prop=EnumProp.from_mapping(
            EPC_SWING_AIR_FLOW, dict(_HA_TO_ECHONET_SWING)
        ),
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
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_translation_key = "climate"
    _attr_precision: float = PRECISION_WHOLE
    _attr_hvac_modes = _SUPPORTED_HVAC_MODES

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
            self._attr_fan_modes = description.fan_mode_prop.options
        if EPC_SWING_AIR_FLOW in node.set_epcs:
            features |= ClimateEntityFeature.SWING_MODE
            swing_modes = list(_HA_TO_ECHONET_SWING.keys())
        if EPC_OPERATION_STATUS in node.set_epcs:
            features |= ClimateEntityFeature.TURN_ON
            features |= ClimateEntityFeature.TURN_OFF
        self._attr_supported_features = features
        self._attr_swing_modes = swing_modes
        definitions = coordinator.config_entry.runtime_data.definitions
        class_code = node.eoj.class_code
        self._op_status = BinaryProp.from_registry(
            definitions, class_code, EPC_OPERATION_STATUS
        )
        self._op_mode_prop = EnumProp.from_registry(
            definitions, class_code, EPC_OPERATION_MODE
        )
        self._special_state_prop = EnumProp.from_registry(
            definitions, class_code, EPC_SPECIAL_STATE
        )

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current HVAC mode."""
        if (status := self._operation_status()) is None:
            return None
        if not status:
            return HVACMode.OFF
        key = self._op_mode_prop.get(self._node)
        return _PYHEMS_TO_HA_MODE.get(key) if key is not None else None

    def _operation_status(self) -> bool | None:
        """Return decoded operation status (True=on, False=off, None=unknown)."""
        return self._op_status.get(self._node)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action."""
        special_key = self._special_state_prop.get(self._node)
        if special_key is not None and special_key in _PYHEMS_SPECIAL_STATE_TO_ACTION:
            if (action := _PYHEMS_SPECIAL_STATE_TO_ACTION[special_key]) is not None:
                return action
        if (status := self._operation_status()) is None:
            return None
        if not status:
            return HVACAction.OFF
        if (mode_key := self._op_mode_prop.get(self._node)) is None:
            return None
        if mode_key not in _PYHEMS_TO_HA_ACTION:
            return None
        action = _PYHEMS_TO_HA_ACTION[mode_key]
        return action if action is not None else self._infer_auto_action()

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        return self.entity_description.fan_mode_prop.get(self._node)

    @property
    def swing_mode(self) -> str | None:
        """Return the current swing mode based on vertical/horizontal settings."""
        return self.entity_description.swing_mode_prop.get(self._node)

    @property
    def current_temperature(self) -> float | None:
        """Return the measured indoor temperature."""
        value = self.entity_description.room_temp_prop.get(self._node)
        return float(value) if value is not None else None

    @property
    def current_humidity(self) -> float | None:
        """Return the measured indoor relative humidity."""
        value = self.entity_description.humidity_prop.get(self._node)
        return float(value) if value is not None else None

    @property
    def target_temperature(self) -> float | None:
        """Return the currently configured setpoint."""
        value = self.entity_description.target_temp_prop.get(self._node)
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
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_MODE:02X}"},
            )
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        pyhems_mode = _HA_TO_PYHEMS_MODE.get(hvac_mode)
        if pyhems_mode is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_value",
                translation_placeholders={"value": str(hvac_mode)},
            )
        await self._async_send_properties(
            [
                self._op_mode_prop.make_property(pyhems_mode),
                self._op_status.make_property(True),
            ]
        )

    async def async_turn_on(self) -> None:
        """Turn on the climate device."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        await self._async_send_prop(self._op_status, True)

    async def async_turn_off(self) -> None:
        """Turn off the climate device."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        await self._async_send_prop(self._op_status, False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature for the current mode."""
        if ATTR_TEMPERATURE not in kwargs or kwargs[ATTR_TEMPERATURE] is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="target_temperature_required",
            )
        if EPC_TARGET_TEMPERATURE not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={
                    "epc_list": f"0x{EPC_TARGET_TEMPERATURE:02X}"
                },
            )
        temperature = float(kwargs[ATTR_TEMPERATURE])
        clamped = min(max(temperature, self._attr_min_temp), self._attr_max_temp)
        await self._async_send_prop(self.entity_description.target_temp_prop, clamped)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan mode."""
        if fan_mode not in self.entity_description.fan_mode_prop.options:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_value",
                translation_placeholders={"value": fan_mode},
            )
        await self._async_send_prop(self.entity_description.fan_mode_prop, fan_mode)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set the swing mode."""
        if swing_mode not in _HA_TO_ECHONET_SWING:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_value",
                translation_placeholders={"value": swing_mode},
            )
        await self._async_send_prop(self.entity_description.swing_mode_prop, swing_mode)

    def _infer_auto_action(self) -> HVACAction:
        """Infer HVAC action for AUTO mode from temperatures."""
        target = self.target_temperature
        current = self.current_temperature
        if target is None or current is None:
            return HVACAction.IDLE
        if target <= current:
            return HVACAction.COOLING
        return HVACAction.HEATING


__all__ = ["EchonetLiteClimate"]
