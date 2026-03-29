"""Number platform for the HEMS integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pyhems import (
    EntityDefinition,
    NodeState,
    create_numeric_decoder,
    create_numeric_encoder,
)

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EchonetLiteCoordinator
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    setup_echonet_lite_platform,
)
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1  # Serialize writes to prevent overwhelming device

# Mapping from MRA unit to (device_class, ha_unit)
_MRA_UNIT_TO_HA: dict[str, tuple[NumberDeviceClass | None, str | None]] = {
    "W": (NumberDeviceClass.POWER, UnitOfPower.WATT),
    "kW": (NumberDeviceClass.POWER, UnitOfPower.KILO_WATT),
    "Wh": (NumberDeviceClass.ENERGY, UnitOfEnergy.WATT_HOUR),
    "kWh": (NumberDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "Celsius": (NumberDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
    "%": (None, PERCENTAGE),
    "%RH": (NumberDeviceClass.HUMIDITY, PERCENTAGE),
    "A": (NumberDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE),
    "mA": (NumberDeviceClass.CURRENT, UnitOfElectricCurrent.MILLIAMPERE),
    "V": (NumberDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
}

# Context-specific device_class overrides for % unit
_PERCENTAGE_DEVICE_CLASS_KEYWORDS: dict[str, NumberDeviceClass] = {
    "humidity": NumberDeviceClass.HUMIDITY,
    "battery": NumberDeviceClass.BATTERY,
}


def _infer_device_class(
    entity_def: EntityDefinition,
) -> NumberDeviceClass | None:
    """Infer device class from MRA unit and entity name."""
    unit = entity_def.unit
    if not unit:
        return None

    unit_info = _MRA_UNIT_TO_HA.get(unit)
    if not unit_info:
        return None

    device_class, _ = unit_info

    if unit == "%" and device_class is None:
        name_lower = entity_def.name_en.lower()
        for keyword, device_class in _PERCENTAGE_DEVICE_CLASS_KEYWORDS.items():
            if keyword in name_lower:
                return device_class

    return device_class


def _infer_ha_unit(entity_def: EntityDefinition) -> str | None:
    """Infer HA unit from MRA unit."""
    unit = entity_def.unit
    if not unit:
        return None

    unit_info = _MRA_UNIT_TO_HA.get(unit)
    if unit_info:
        return unit_info[1]

    return unit


@dataclass(frozen=True, kw_only=True)
class EchonetLiteNumberEntityDescription(
    NumberEntityDescription, EchonetLiteEntityDescription
):
    """Entity description with EPC metadata for number entities."""

    decoder: Callable[[bytes], float | int | None]
    encoder: Callable[[float | int], bytes]
    mra_format: str
    scale: float


def _create_number_description(
    class_code: int,
    entity_def: EntityDefinition,
) -> EchonetLiteNumberEntityDescription:
    """Create a number entity description from an EntityDefinition."""
    assert entity_def.format is not None
    return EchonetLiteNumberEntityDescription(
        key=f"{entity_def.epc:02x}_{entity_def.byte_offset}",
        translation_key=entity_def.id,
        class_code=class_code,
        epc=entity_def.epc,
        device_class=_infer_device_class(entity_def),
        native_unit_of_measurement=_infer_ha_unit(entity_def),
        native_min_value=(
            entity_def.minimum * entity_def.multiple_of
            if entity_def.minimum is not None
            else None
        ),
        native_max_value=(
            entity_def.maximum * entity_def.multiple_of
            if entity_def.maximum is not None
            else None
        ),
        native_step=entity_def.multiple_of if entity_def.multiple_of != 1.0 else None,
        decoder=create_numeric_decoder(
            mra_format=entity_def.format,
            minimum=entity_def.minimum,
            maximum=entity_def.maximum,
            scale=entity_def.multiple_of,
            byte_offset=entity_def.byte_offset,
        ),
        encoder=create_numeric_encoder(
            mra_format=entity_def.format,
            scale=entity_def.multiple_of,
        ),
        mra_format=entity_def.format,
        scale=entity_def.multiple_of,
        manufacturer_code=entity_def.manufacturer_code,
        fallback_name=entity_def.name_en or None,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite number entities from a config entry."""
    setup_echonet_lite_platform(
        entry,
        async_add_entities,
        "number",
        _create_number_description,
        EchonetLiteNumber,
        "number",
    )


class EchonetLiteNumber(
    EchonetLiteDescribedEntity[EchonetLiteNumberEntityDescription], NumberEntity
):
    """Representation of a writable ECHONET Lite numeric property."""

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteNumberEntityDescription,
    ) -> None:
        """Initialize the ECHONET Lite number entity."""
        super().__init__(coordinator, node, description)

    @property
    def native_value(self) -> float | int | None:
        """Return the current value."""
        state = self._node.properties.get(self._epc)
        return self.description.decoder(state) if state is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the value by sending an ECHONET Lite command."""
        encoded = self.description.encoder(value)
        await self._async_send_property(self._epc, encoded)


__all__ = ["EchonetLiteNumber", "EchonetLiteNumberEntityDescription"]
