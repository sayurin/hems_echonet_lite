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

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pyhems import (
    DefinitionsRegistry,
    NodeState,
    Property,
    create_numeric_decoder,
    create_numeric_encoder,
)

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
from .entity import EchonetLiteEntity, setup_echonet_lite_device_platform
from .types import EchonetLiteConfigEntry

_T = TypeVar("_T")

PARALLEL_UPDATES = 1

# Operation status raw values (EPC 0x80)
_OP_STATUS_ON = 0x30
_OP_STATUS_OFF = 0x31

# Mapping from EPC 0xB0 raw byte to a snake_case operation name. The
# string is exposed to HA as ``current_operation`` and is used directly
# as a translation key.
#
# EPC 0xB0 semantics:
#   auto       (0x41) – Automatic water heating
#   manual     (0x42) – Manual water heating (heating active)
#   manual_off (0x43) – Manual water heating stopped (heating inactive;
#                       corresponds to "away" / 外出 in Japanese UI)
_OPERATION_MODE_MAP: dict[int, str] = {
    0x41: "auto",
    0x42: "manual",
    0x43: "manual_off",
}

# Raw EPC 0xB0 byte that represents the "away" state (heating stopped).
_OP_MODE_AWAY = 0x43

_TRANSLATION_KEY = "electric_water_heater"


@dataclass(frozen=True, kw_only=True)
class EchonetLiteWaterHeaterEntityDescription(WaterHeaterEntityDescription):
    """Description for the ECHONET Lite electric water heater entity.

    pyhems definitions are protocol-level and shared across all
    instances, so we build one description at ``async_setup_entry`` time
    and reuse it for every discovered node. The pattern mirrors the
    climate platform.
    """

    target_temp_encoder: Callable[[float | int], bytes] | None = None
    target_temp_decoder: Callable[[bytes], float | int | None] | None = None
    current_temp_decoder: Callable[[bytes], float | int | None] | None = None
    target_temp_min: float | None = None
    target_temp_max: float | None = None
    target_temp_step: float | None = None


def _temperature_decoder(
    entity_def: Any,
) -> Callable[[bytes], float | int | None]:
    """Build a decoder for a Celsius temperature EPC from a definition."""
    return create_numeric_decoder(
        mra_format=entity_def.format,
        minimum=entity_def.minimum,
        maximum=entity_def.maximum,
        scale=entity_def.multiple_of,
        byte_offset=entity_def.byte_offset,
    )


def _create_water_heater_description(
    definitions: DefinitionsRegistry,
) -> EchonetLiteWaterHeaterEntityDescription:
    """Build the entity description from pyhems definitions."""
    entities = {
        e.epc: e for e in definitions.entities.get(CLASS_CODE_ELECTRIC_WATER_HEATER, ())
    }

    target_temp_decoder: Callable[[bytes], float | int | None] | None = None
    target_temp_encoder: Callable[[float | int], bytes] | None = None
    target_temp_min: float | None = None
    target_temp_max: float | None = None
    target_temp_step: float | None = None
    if (
        entity_def := entities.get(EPC_TARGET_TEMPERATURE)
    ) and entity_def.format is not None:
        scale = entity_def.multiple_of
        target_temp_min = (
            entity_def.minimum * scale if entity_def.minimum is not None else None
        )
        target_temp_max = (
            entity_def.maximum * scale if entity_def.maximum is not None else None
        )
        target_temp_step = scale
        target_temp_decoder = _temperature_decoder(entity_def)
        target_temp_encoder = create_numeric_encoder(
            mra_format=entity_def.format, scale=scale
        )

    current_temp_decoder: Callable[[bytes], float | int | None] | None = None
    if (
        entity_def := entities.get(EPC_MEASURED_WATER_TEMPERATURE)
    ) and entity_def.format is not None:
        current_temp_decoder = _temperature_decoder(entity_def)

    return EchonetLiteWaterHeaterEntityDescription(
        key="water_heater",
        target_temp_encoder=target_temp_encoder,
        target_temp_decoder=target_temp_decoder,
        current_temp_decoder=current_temp_decoder,
        target_temp_min=target_temp_min,
        target_temp_max=target_temp_max,
        target_temp_step=target_temp_step,
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
        if (
            description.target_temp_encoder is not None
            and EPC_TARGET_TEMPERATURE in node.set_epcs
        ):
            features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE
        if EPC_OPERATION_STATUS in node.set_epcs:
            features |= WaterHeaterEntityFeature.ON_OFF

        # Reverse map: snake_case key -> raw EDT byte. Built once.
        self._mode_to_edt: dict[str, int] = {
            name: raw for raw, name in _OPERATION_MODE_MAP.items()
        }
        operation_list: list[str] = [STATE_OFF]
        if EPC_OPERATION_MODE in node.set_epcs:
            features |= WaterHeaterEntityFeature.OPERATION_MODE
            # Preserve the EDT-byte order so the UI lists modes in the
            # order defined by the ECHONET Lite specification.
            operation_list.extend(
                _OPERATION_MODE_MAP[raw] for raw in sorted(_OPERATION_MODE_MAP)
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
        mode_raw = self._get_value(EPC_OPERATION_MODE, lambda edt: edt[0])
        if mode_raw is None:
            return None
        return mode_raw == _OP_MODE_AWAY

    @property
    def current_operation(self) -> str | None:
        """Return the current operation mode.

        ``state`` is reported as ``current_operation`` by HA's
        ``WaterHeaterEntity`` base class, so OFF must be one of the
        operation list values (``STATE_OFF``).
        """
        status = self._get_value(EPC_OPERATION_STATUS, lambda edt: edt[0])
        if status == _OP_STATUS_OFF:
            return STATE_OFF
        if status != _OP_STATUS_ON:
            return None
        return self._get_value(
            EPC_OPERATION_MODE,
            lambda edt: _OPERATION_MODE_MAP.get(edt[0]),
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the measured water temperature, when available."""
        decoder = self.entity_description.current_temp_decoder
        if decoder is None:
            return None
        value = self._get_value(EPC_MEASURED_WATER_TEMPERATURE, decoder)
        return float(value) if value is not None else None

    @property
    def target_temperature(self) -> float | None:
        """Return the configured target water temperature."""
        decoder = self.entity_description.target_temp_decoder
        if decoder is None:
            return None
        value = self._get_value(EPC_TARGET_TEMPERATURE, decoder)
        return float(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the water heater."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_status_not_writable",
            )
        await self._async_send_property(EPC_OPERATION_STATUS, bytes([_OP_STATUS_ON]))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the water heater."""
        if EPC_OPERATION_STATUS not in self._node.set_epcs:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="operation_status_not_writable",
            )
        await self._async_send_property(EPC_OPERATION_STATUS, bytes([_OP_STATUS_OFF]))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set the operation mode (HA service handler)."""
        if operation_mode == STATE_OFF:
            await self.async_turn_off()
            return
        edt_byte = self._mode_to_edt.get(operation_mode)
        if edt_byte is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_operation_mode",
                translation_placeholders={"operation_mode": operation_mode},
            )
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
        # Send mode + ON together so flipping the operation mode from
        # the "Off" state in the UI also turns the device on.
        await self._async_send_properties(
            [
                Property(epc=EPC_OPERATION_MODE, edt=bytes([edt_byte])),
                Property(epc=EPC_OPERATION_STATUS, edt=bytes([_OP_STATUS_ON])),
            ]
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target water temperature."""
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
        await self._async_send_property(EPC_TARGET_TEMPERATURE, encoder(clamped))

    def _get_value(self, epc: int, converter: Callable[[bytes], _T]) -> _T | None:
        """Helper to get and decode a property value from the node."""
        if edt := self._node.properties.get(epc):
            return converter(edt)
        return None


__all__ = ["EchonetLiteWaterHeater"]
