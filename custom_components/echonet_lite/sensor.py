"""Sensor platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import override

from pyhems import (
    CollectionPage,
    EntityDefinition,
    EnumCodec,
    NodeState,
    ValueKind,
    decode_collection_page,
    get_codec,
    get_codec_for_epc,
    get_collection_binding,
)

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import DEGREE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import COLLECTION_SENSOR_PROJECTIONS, infer_device_classes, infer_ha_unit
from .coordinator import EchonetLiteCoordinator
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntity,
    EchonetLiteEntityDescription,
    build_platform_descriptions,
    setup_common_platform,
    setup_echonet_lite_device_platform,
)
from .prop import EnumProp, NumericProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 0

_VALUE_KIND_TO_STATE_CLASS: dict[ValueKind, SensorStateClass] = {
    ValueKind.MEASUREMENT: SensorStateClass.MEASUREMENT,
    ValueKind.TOTAL: SensorStateClass.TOTAL,
    ValueKind.CUMULATIVE: SensorStateClass.TOTAL_INCREASING,
}


def _state_class(
    entity_def: EntityDefinition,
    native_unit_of_measurement: str | None,
) -> SensorStateClass | None:
    """Return the sensor state class for the pyhems value kind."""
    if entity_def.value_kind is None:
        return None
    if (
        entity_def.value_kind is ValueKind.MEASUREMENT
        and native_unit_of_measurement == DEGREE
    ):
        return SensorStateClass.MEASUREMENT_ANGLE
    return _VALUE_KIND_TO_STATE_CLASS[entity_def.value_kind]


@dataclass(frozen=True, kw_only=True)
class EchonetLiteSensorEntityDescription(
    SensorEntityDescription, EchonetLiteEntityDescription
):
    """Entity description with EPC metadata."""

    prop: EnumProp | NumericProp

    @classmethod
    @override
    def build_from_entity_def(
        cls,
        class_code: int,
        entity_def: EntityDefinition,
    ) -> EchonetLiteSensorEntityDescription:
        """Construct a sensor description from an EntityDefinition."""
        codec = get_codec(entity_def, class_code=class_code)

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
        state_class = _state_class(entity_def, native_unit_of_measurement)
        return cls(
            key=f"{entity_def.epc:02x}_{entity_def.byte_offset}",
            device_class=infer_device_classes(entity_def)[0],
            native_unit_of_measurement=native_unit_of_measurement,
            state_class=state_class,
            prop=NumericProp.from_entity_def(entity_def, class_code=class_code),
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
    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        platform_domain=Platform.SENSOR.value,
        entity_factory=_build_collection_sensors,
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


# ============================================================================
# Collection (paged list) channel sensors
#
# Generic over any COLLECTION_SENSOR_PROJECTIONS entry (see const.py); no
# class-specific code lives here. One entity is created per (channel, field)
# combination, up to ``min(count, max_exposed_items)`` channels, where
# ``count`` is decoded from the projection's curated CollectionBinding count
# EPC (e.g. class 0x0287 EPC 0xB1/0xB8) at initial entity-setup time. Unlike
# EchonetLiteDescribedEntity's static per-platform descriptions, these are
# built dynamically per node since the channel count is device-specific.
# ============================================================================


@dataclass(frozen=True, kw_only=True)
class EchonetLiteCollectionSensorEntityDescription(SensorEntityDescription):
    """Entity description for one field of a paged list property.

    Shared by every channel entity for that field (see
    :func:`_build_collection_sensors`): all fields here are constant across
    channels. The channel number itself is passed separately to
    :class:`EchonetLiteCollectionSensor`, not stored on the description.
    """

    result_epc: int
    item_field: str | None
    unique_id_prefix: str
    unique_id_suffix: str
    coefficient_epcs: tuple[int, ...] = ()


class EchonetLiteCollectionSensor(EchonetLiteEntity, SensorEntity):
    """Representation of one channel of an ECHONET Lite paged list property."""

    description: EchonetLiteCollectionSensorEntityDescription

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteCollectionSensorEntityDescription,
        channel: int,
    ) -> None:
        """Initialize a collection channel sensor.

        ``description`` is shared across every channel of the same field;
        ``channel`` is this entity's own position within it.
        """
        super().__init__(coordinator, node)
        self.description = description
        self.entity_description = description
        self._channel = channel
        self._attr_translation_key = description.translation_key
        self._attr_translation_placeholders = {"channel": str(channel)}
        self._attr_unique_id = (
            f"{node.device_key}-{description.unique_id_prefix}_"
            f"{channel}_{description.unique_id_suffix}"
        )
        self._subscribed_epcs = frozenset({description.result_epc}) | frozenset(
            description.coefficient_epcs
        )

    @property
    @override
    def native_value(self) -> float | int | None:
        """Return this channel's value from the current page, or None if unavailable."""
        description = self.description
        page = _collection_page(
            self.coordinator,
            self._node,
            description.result_epc,
            description.coefficient_epcs,
        )
        if page is None:
            return None
        offset = self._channel - page.start
        if not 0 <= offset < page.count:
            return None
        item = page.items[offset]
        if description.item_field is not None:
            item = item.get(description.item_field) if isinstance(item, dict) else None
        return item if isinstance(item, int | float) else None


def _collection_page(
    coordinator: EchonetLiteCoordinator,
    node: NodeState,
    result_epc: int,
    coefficient_epcs: tuple[int, ...],
) -> CollectionPage | None:
    """Return the decoded page of a list, decoding it once per value change."""
    edt = node.properties.get(result_epc)
    if edt is None:
        return None
    source = (edt, *(node.properties.get(epc) for epc in coefficient_epcs))
    cache_key = (node.device_key, result_epc)
    if (cached := coordinator.collection_pages.get(cache_key)) is not None and (
        cached[0] == source
    ):
        return cached[1]
    page = decode_collection_page(node.eoj.class_code, result_epc, edt, node.properties)
    coordinator.collection_pages[cache_key] = (source, page)
    return page


def _build_collection_sensors(
    coordinator: EchonetLiteCoordinator, node: NodeState
) -> list[Entity]:
    """Build this node's collection channel sensors from curated projections.

    Channel count is decoded once, at initial device setup, from the
    projection's curated ``CollectionBinding`` count EPC. Later count changes
    are not tracked; the entity set only changes on integration reload.
    """
    entities: list[Entity] = []
    for projection in COLLECTION_SENSOR_PROJECTIONS:
        if projection.class_code != node.eoj.class_code:
            continue
        if projection.result_epc not in node.get_epcs:
            continue
        binding = get_collection_binding(projection.class_code, projection.result_epc)
        if binding is None or binding.count_epc is None:
            continue
        count_edt = node.properties.get(binding.count_epc)
        if count_edt is None:
            continue
        count = get_codec_for_epc(node.eoj.class_code, binding.count_epc).decode(
            count_edt
        )
        if not isinstance(count, int):
            continue
        channel_count = min(count, projection.max_exposed_items)
        for field in projection.fields:
            # One description instance is shared by every channel of this
            # field: only the channel number differs between them, and that
            # is passed to the entity separately (see EchonetLiteCollectionSensor).
            description = EchonetLiteCollectionSensorEntityDescription(
                key=f"{projection.result_epc:02x}_{field.unique_id_suffix}",
                result_epc=projection.result_epc,
                item_field=field.item_field,
                unique_id_prefix=projection.unique_id_prefix,
                unique_id_suffix=field.unique_id_suffix,
                coefficient_epcs=projection.coefficient_epcs,
                translation_key=field.translation_key,
                device_class=field.device_class,
                state_class=field.state_class,
                native_unit_of_measurement=field.unit,
            )
            entities.extend(
                EchonetLiteCollectionSensor(coordinator, node, description, channel)
                for channel in range(1, channel_count + 1)
            )
    return entities
