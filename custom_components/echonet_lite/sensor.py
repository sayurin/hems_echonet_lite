"""Sensor platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import override

from pyhems import EntityDefinition, EnumCodec, get_codec

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import DEGREE, Platform, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import infer_device_classes, infer_ha_unit
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntityDescription,
    build_platform_descriptions,
    setup_common_platform,
)
from .prop import EnumProp, NumericProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0

_NO_STATE_CLASS_NAME_KEYWORDS = ("capacity", "rated", "number of effective digits")
_MEASUREMENT_NAME_KEYWORDS = ("maximum electric power demand",)
_TOTAL_STATE_CLASS_UNIT_NAME_KEYWORDS: tuple[tuple[str, str], ...] = (
    (UnitOfEnergy.WATT_HOUR, "electric energy"),
    (UnitOfEnergy.KILO_WATT_HOUR, "electric energy"),
    (UnitOfEnergy.MEGA_JOULE, "electric energy"),
    (UnitOfEnergy.WATT_HOUR, "heating value"),
    (UnitOfEnergy.KILO_WATT_HOUR, "heating value"),
    (UnitOfEnergy.MEGA_JOULE, "heating value"),
    (UnitOfVolume.CUBIC_METERS, "gas consumption"),
    (UnitOfVolume.CUBIC_METERS, "water consumption"),
    (UnitOfVolume.CUBIC_METERS, "flowing water"),
)


def _infer_state_class(
    entity_def: EntityDefinition,
    native_unit_of_measurement: str | None,
) -> SensorStateClass | None:
    """Infer sensor state class.

    Args:
        entity_def: Entity definition with name.
        native_unit_of_measurement: Native unit after mapping MRA units to HA units.

    Returns:
        Inferred state class.
    """
    name_lower = entity_def.name_en.lower()
    if any(keyword in name_lower for keyword in _NO_STATE_CLASS_NAME_KEYWORDS):
        return None
    if any(keyword in name_lower for keyword in _MEASUREMENT_NAME_KEYWORDS):
        return SensorStateClass.MEASUREMENT
    if native_unit_of_measurement == DEGREE:
        return SensorStateClass.MEASUREMENT_ANGLE
    if "cumulative" in name_lower:
        return SensorStateClass.TOTAL_INCREASING
    for unit, keyword in _TOTAL_STATE_CLASS_UNIT_NAME_KEYWORDS:
        if native_unit_of_measurement == unit and keyword in name_lower:
            return SensorStateClass.TOTAL
    return SensorStateClass.MEASUREMENT


@dataclass(frozen=True, kw_only=True)
class EchonetLiteSensorEntityDescription(
    SensorEntityDescription, EchonetLiteEntityDescription
):
    """Entity description with EPC metadata."""

    prop: EnumProp | NumericProp

    @classmethod
    @override
    def build_from_entity_def(
        cls, entity_def: EntityDefinition
    ) -> EchonetLiteSensorEntityDescription:
        """Construct a sensor description from an EntityDefinition."""
        codec = get_codec(entity_def)

        # Read-only multi-value enum → ENUM sensor
        if isinstance(codec, EnumCodec):
            enum_prop = EnumProp.from_entity_def(entity_def)
            return cls(
                key=f"{entity_def.epc:02x}",
                device_class=SensorDeviceClass.ENUM,
                options=enum_prop.options,
                prop=enum_prop,
                **cls._common_kwargs(entity_def),
            )

        # Numeric sensor
        native_unit_of_measurement = infer_ha_unit(entity_def)
        state_class = _infer_state_class(entity_def, native_unit_of_measurement)
        return cls(
            key=f"{entity_def.epc:02x}_{entity_def.byte_offset}",
            device_class=infer_device_classes(entity_def)[0],
            native_unit_of_measurement=native_unit_of_measurement,
            state_class=state_class,
            prop=NumericProp.from_entity_def(entity_def),
            **cls._common_kwargs(entity_def),
        )


_DESCRIPTIONS: dict[int, list[EchonetLiteSensorEntityDescription]] = (
    build_platform_descriptions(Platform.SENSOR, EchonetLiteSensorEntityDescription)
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite sensors from a config entry."""
    setup_common_platform(
        entry,
        async_add_entities,
        Platform.SENSOR.value,
        _DESCRIPTIONS,
        EchonetLiteSensor,
    )


class EchonetLiteSensor(
    EchonetLiteDescribedEntity[EchonetLiteSensorEntityDescription], SensorEntity
):
    """Representation of an ECHONET Lite sensor property."""

    @property
    @override
    def native_value(self) -> float | int | str | None:
        """Return the state of the sensor."""
        return self.description.prop.get(self._node)
