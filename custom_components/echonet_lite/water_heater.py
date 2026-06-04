"""Water heater platform for the HEMS Echonet Lite integration.

Supports the ECHONET Lite electric water heater class (0x026B, electric heat-pump
water heater).

Like the climate platform, this platform exposes a high-level ``WaterHeaterEntity``
that aggregates the operation status (EPC 0x80), the operation mode (EPC 0xB0) and
the target / current temperature EPCs into a single entity. EPCs that are aggregated
by this entity are listed in :data:`DEDICATED_PLATFORM_EPCS` so that the generic
sensor / number / select / switch platforms do not produce duplicate entities for
them.

Following the same convention as climate -- which exposes the room temperature both
via ``current_temperature`` and as a standalone sensor -- this platform deliberately
does **not** suppress the measured-water-temperature EPC (0xC1). The user sees the
same temperature via both surfaces.
"""

from dataclasses import dataclass
from typing import Any

from pyhems import DefinitionsRegistry, NodeState

from homeassistant.components.water_heater import (
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityDescription,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity import Entity
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
from .entity import (
    BinaryProp,
    EchonetLiteEntity,
    EnumProp,
    NumericProp,
    setup_echonet_lite_device_platform,
)
from .types import EchonetLiteConfigEntry

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
    target_temp_min: float | None = None
    target_temp_max: float | None = None
    target_temp_step: float | None = None


def _create_water_heater_description(
    definitions: DefinitionsRegistry,
) -> EchonetLiteWaterHeaterEntityDescription:
    """Build the entity description from pyhems definitions.

    get_codec_for_epc is guaranteed by pyhems test_platform_epc_codec_type
    to return NumericCodec for EPC 0xB3 and 0xC1 on class 0x026B.
    """
    target_prop = NumericProp.from_registry(
        definitions, CLASS_CODE_ELECTRIC_WATER_HEATER, EPC_TARGET_TEMPERATURE
    )
    current_prop = NumericProp.from_registry(
        definitions, CLASS_CODE_ELECTRIC_WATER_HEATER, EPC_MEASURED_WATER_TEMPERATURE
    )

    scale = target_prop.codec.scale
    return EchonetLiteWaterHeaterEntityDescription(
        key="water_heater",
        target_temp_prop=target_prop,
        current_temp_prop=current_prop,
        target_temp_min=(
            target_prop.codec.minimum * scale
            if target_prop.codec.minimum is not None
            else None
        ),
        target_temp_max=(
            target_prop.codec.maximum * scale
            if target_prop.codec.maximum is not None
            else None
        ),
        target_temp_step=scale,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite water heater entities from a config entry."""
    description = _create_water_heater_description(entry.runtime_data.definitions)

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        if node.eoj.class_code != CLASS_CODE_ELECTRIC_WATER_HEATER:
            return []
        return [EchonetLiteWaterHeater(coordinator, node, description)]

    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        entity_factory=_entity_factory,
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

        definitions = coordinator.config_entry.runtime_data.definitions
        class_code = node.eoj.class_code
        # Build op_mode from pyhems definitions; camelCase keys are auto-converted to snake_case.
        self._op_mode = EnumProp.from_registry(
            definitions, class_code, EPC_OPERATION_MODE
        )
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
                k for k, _ in sorted(self._op_mode.codec.by_key.items())
            )

        self._attr_supported_features = features
        self._attr_operation_list = operation_list
        self._op_status = BinaryProp.from_registry(
            definitions, class_code, EPC_OPERATION_STATUS
        )

    @property
    def is_away_mode_on(self) -> bool | None:
        """Return True when the heater is in away mode (EPC 0xB0 = 0x43).

        ``AWAY_MODE`` is intentionally not included in
        ``supported_features``; this property is exposed as a read-only
        state attribute so automations can act on it without the HA UI
        showing an away-mode toggle.
        """
        key = self._op_mode.get(self._node)
        return None if key is None else key == "manual_no_heating"

    @property
    def current_operation(self) -> str | None:
        """Return the current operation mode.

        ``state`` is reported as ``current_operation`` by HA's
        ``WaterHeaterEntity`` base class, so OFF must be one of the
        operation list values (``STATE_OFF``).
        """
        if (status_on := self._op_status.get(self._node)) is None:
            return None
        return STATE_OFF if not status_on else self._op_mode.get(self._node)

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
        await self._async_send_prop(self._op_status, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the water heater."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": f"0x{EPC_OPERATION_STATUS:02X}"},
            )
        await self._async_send_prop(self._op_status, False)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set the operation mode (HA service handler)."""
        if operation_mode == STATE_OFF:
            await self.async_turn_off()
            return
        if operation_mode not in self._op_mode.options:
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
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            # Always-on devices do not allow writing 0x80; send only the
            # operation mode and let 0x80 stay at its current value.
            await self._async_send_prop(self._op_mode, operation_mode)
            return
        # Send mode + ON together so flipping the operation mode from
        # the "Off" state in the UI also turns the device on.
        await self._async_send_properties(
            [
                self._op_mode.make_property(operation_mode),
                self._op_status.make_property(True),
            ]
        )

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


__all__ = ["EchonetLiteWaterHeater"]
