"""Water heater platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import Any

from pyhems import NodeState

from homeassistant.components.water_heater import (
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityDescription,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CLASS_CODE_ELECTRIC_WATER_HEATER,
    DOMAIN,
    EPC_MEASURED_WATER_TEMPERATURE,
    EPC_OPERATION_MODE,
    EPC_OPERATION_STATUS,
    EPC_TARGET_TEMPERATURE,
)
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_dedicated_platform
from .prop import BinaryProp, EnumProp, NumericProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1

_TRANSLATION_KEY = "electric_water_heater"


@dataclass(frozen=True, kw_only=True)
class EchonetLiteWaterHeaterEntityDescription(WaterHeaterEntityDescription):
    """Description for the ECHONET Lite electric water heater entity.

    pyhems definitions are protocol-level and shared across all
    instances, so we build one description at ``async_setup_entry`` time
    and reuse it for every discovered node. The pattern mirrors the
    climate platform.
    """

    target_temp_prop: NumericProp
    current_temp_prop: NumericProp
    op_status: BinaryProp
    op_mode: EnumProp
    target_temp_min: float | None = None
    target_temp_max: float | None = None
    target_temp_step: float | None = None


def _create_water_heater_description() -> EchonetLiteWaterHeaterEntityDescription:
    """Build the entity description from pyhems definitions.

    get_codec_for_epc is guaranteed by pyhems test_platform_epc_codec_type
    to return NumericCodec for EPC 0xB3 and 0xC1 on class 0x026B.
    """
    target_prop = NumericProp.from_registry(
        CLASS_CODE_ELECTRIC_WATER_HEATER, EPC_TARGET_TEMPERATURE
    )
    current_prop = NumericProp.from_registry(
        CLASS_CODE_ELECTRIC_WATER_HEATER, EPC_MEASURED_WATER_TEMPERATURE
    )
    minimum = target_prop.codec.minimum
    maximum = target_prop.codec.maximum
    scale = target_prop.codec.scale
    return EchonetLiteWaterHeaterEntityDescription(
        key="water_heater",
        target_temp_prop=target_prop,
        current_temp_prop=current_prop,
        op_status=BinaryProp.from_registry(
            CLASS_CODE_ELECTRIC_WATER_HEATER, EPC_OPERATION_STATUS
        ),
        op_mode=EnumProp.from_registry(
            CLASS_CODE_ELECTRIC_WATER_HEATER, EPC_OPERATION_MODE
        ),
        target_temp_min=None if minimum is None else minimum * scale,
        target_temp_max=None if maximum is None else maximum * scale,
        target_temp_step=scale,
    )


_DESCRIPTIONS: dict[int, EchonetLiteWaterHeaterEntityDescription] = {
    CLASS_CODE_ELECTRIC_WATER_HEATER: _create_water_heater_description()
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite water heater entities from a config entry."""
    setup_dedicated_platform(
        entry, async_add_entities, _DESCRIPTIONS, EchonetLiteWaterHeater
    )


class EchonetLiteWaterHeater(EchonetLiteEntity, WaterHeaterEntity):
    """Representation of an ECHONET Lite electric water heater."""

    entity_description: EchonetLiteWaterHeaterEntityDescription
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision: float = PRECISION_WHOLE
    _attr_translation_key = _TRANSLATION_KEY

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteWaterHeaterEntityDescription,
    ) -> None:
        """Initialize an ECHONET Lite water heater entity."""
        super().__init__(coordinator, node)
        self.entity_description = description
        self._attr_unique_id = f"{node.device_key}-{description.key}"
        if description.target_temp_min is not None:
            self._attr_min_temp = description.target_temp_min
        if description.target_temp_max is not None:
            self._attr_max_temp = description.target_temp_max
        if description.target_temp_step is not None:
            self._attr_target_temperature_step = description.target_temp_step

        features = WaterHeaterEntityFeature(0)
        if EPC_TARGET_TEMPERATURE in node.set_epcs:
            features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE
        if EPC_OPERATION_STATUS in node.set_epcs:
            features |= WaterHeaterEntityFeature.ON_OFF

        # STATE_OFF is only meaningful when 0x80 is writable; some
        # always-on water heaters do not allow turning off via 0x80.
        operation_list: list[str] = (
            [STATE_OFF] if EPC_OPERATION_STATUS in node.set_epcs else []
        )
        if EPC_OPERATION_MODE in node.set_epcs:
            features |= WaterHeaterEntityFeature.OPERATION_MODE
            # Preserve the EDT-byte order so the UI lists modes in the
            # order defined by the ECHONET Lite specification.
            operation_list.extend(
                k for k, _ in sorted(description.op_mode.codec.by_key.items())
            )

        self._attr_supported_features = features
        self._attr_operation_list = operation_list

    @property
    def is_away_mode_on(self) -> bool | None:
        """Return True when the heater is in away mode (EPC 0xB0 = 0x43).

        ``AWAY_MODE`` is intentionally not included in
        ``supported_features``; this property is exposed as a read-only
        state attribute so automations can act on it without the HA UI
        showing an away-mode toggle.
        """
        key = self.entity_description.op_mode.get(self._node)
        return None if key is None else key == "manual_no_heating"

    @property
    def current_operation(self) -> str | None:
        """Return the current operation mode.

        ``state`` is reported as ``current_operation`` by HA's
        ``WaterHeaterEntity`` base class, so OFF must be one of the
        operation list values (``STATE_OFF``).
        """
        if (status_on := self.entity_description.op_status.get(self._node)) is None:
            return None
        if not status_on:
            return STATE_OFF
        return self.entity_description.op_mode.get(self._node)

    @property
    def current_temperature(self) -> float | None:
        """Return the measured water temperature."""
        value = self.entity_description.current_temp_prop.get(self._node)
        return float(value) if value is not None else None

    @property
    def target_temperature(self) -> float | None:
        """Return the configured target water temperature."""
        value = self.entity_description.target_temp_prop.get(self._node)
        return float(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the water heater."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        await self._async_send_prop(self.entity_description.op_status, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the water heater."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        await self._async_send_prop(self.entity_description.op_status, False)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set the operation mode (HA service handler)."""
        if operation_mode == STATE_OFF:
            await self.async_turn_off()
            return
        if operation_mode not in self.entity_description.op_mode.options:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_value",
                translation_placeholders={"value": operation_mode},
            )
        if EPC_OPERATION_MODE not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_MODE:02X}"},
            )
        properties = [self.entity_description.op_mode.make_property(operation_mode)]
        if EPC_OPERATION_STATUS in self._node.set_epcs:
            properties.append(self.entity_description.op_status.make_property(True))
        await self._async_send_properties(properties)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target water temperature."""
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
