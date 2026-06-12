"""Sensor platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass

from pyhems import EntityDefinition, EnumCodec, get_codec

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import Platform
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


def _infer_device_class(
    entity_def: EntityDefinition,
) -> SensorDeviceClass | None:
    """Infer the sensor device class from MRA unit and entity name."""
    return infer_device_classes(entity_def)[0]


def _infer_state_class(entity_def: EntityDefinition) -> SensorStateClass:
    """Infer state class from entity name.

    Args:
        entity_def: Entity definition with name.

    Returns:
        SensorStateClass (measurement or total_increasing).
    """
    name_lower = entity_def.name_en.lower()
    if "cumulative" in name_lower:
        return SensorStateClass.TOTAL_INCREASING
    return SensorStateClass.MEASUREMENT


@dataclass(frozen=True, kw_only=True)
class EchonetLiteSensorEntityDescription(
    SensorEntityDescription, EchonetLiteEntityDescription
):
    """Entity description with EPC metadata."""

    prop: EnumProp | NumericProp

    @classmethod
    def build_from_entity_def(
        cls, class_code: int, entity_def: EntityDefinition
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
                **cls._common_kwargs(class_code, entity_def),
            )

        # Numeric sensor
        return cls(
            key=f"{entity_def.epc:02x}_{entity_def.byte_offset}",
            device_class=_infer_device_class(entity_def),
            native_unit_of_measurement=infer_ha_unit(entity_def),
            state_class=_infer_state_class(entity_def),
            prop=NumericProp.from_entity_def(entity_def),
            **cls._common_kwargs(class_code, entity_def),
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
    setup_common_platform(entry, async_add_entities, _DESCRIPTIONS, EchonetLiteSensor)


class EchonetLiteSensor(
    EchonetLiteDescribedEntity[EchonetLiteSensorEntityDescription], SensorEntity
):
    """Representation of an ECHONET Lite sensor property."""

    @property
    def native_value(self) -> float | int | str | None:
        """Return the state of the sensor."""
        return self.description.prop.get(self._node)
