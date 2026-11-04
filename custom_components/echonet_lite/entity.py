"""Base entity classes for the HEMS Echonet Lite integration."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Self, override

from pyhems import EntityDefinition, NodeState, Property

from homeassistant.const import EVENT_CORE_CONFIG_UPDATE, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .catalog import CATALOG
from .const import DOMAIN, get_entity_category
from .coordinator import EchonetLiteCoordinator
from .prop import Prop
from .runtime import EchonetLiteConfigEntry

_LOGGER = logging.getLogger(__name__)


def _get_or_build_device_info(
    coordinator: EchonetLiteCoordinator, node: NodeState
) -> DeviceInfo:
    """Return the cached ``DeviceInfo`` for ``node``, building it on first use."""
    cache = coordinator.device_info_cache
    if (cached := cache.get(node.device_key)) is not None:
        return cached

    # Use the class name (e.g. "Home air conditioner") rather than the
    # product code so that multi-class nodes (PV + battery + controller …)
    # get distinguishable names in the device list.
    #
    # The installation location is kept out of the name: ``DeviceInfo.name``
    # is a snapshot at setup time and would not track later EPC 0x81 changes.
    # The live value is available through the installation_location_code /
    # installation_location_number config select entities (Z-Wave JS pattern).
    suggested_area: str | None = None
    if (location := node.installation_location) is not None:
        # Only applied at first device registration; never overwrites
        # a user's chosen area.
        suggested_area = location.name

    # Prefer a translation_key so the device name is localized via
    # ``strings.json`` (``device.class_XXXX.name``) rather than hard-coding
    # the English MRA name. Every discovered class is set up, so class codes
    # missing from the pyhems registry fall back to the shared
    # ``unknown_class`` key with the hex class code rendered through
    # ``translation_placeholders``.
    if node.class_name_en is not None:
        translation_key = f"class_{node.eoj.class_code:04x}"
        translation_placeholders: dict[str, str] | None = None
    else:
        translation_key = "unknown_class"
        translation_placeholders = {
            "class_code": f"0x{node.eoj.class_code:04X}",
        }

    device_info = DeviceInfo(
        identifiers={(DOMAIN, node.device_key)},
        manufacturer=node.manufacturer_name,
        model=node.product_code,
        serial_number=node.serial_number,
        suggested_area=suggested_area,
        translation_key=translation_key,
        translation_placeholders=translation_placeholders,
    )
    cache[node.device_key] = device_info
    return device_info


class EchonetLiteEntity(CoordinatorEntity[EchonetLiteCoordinator]):
    """Base entity bound to an ECHONET Lite node."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
    ) -> None:
        """Initialize the base entity for the given device key."""

        super().__init__(coordinator)
        self._node = node
        self._attr_device_info = _get_or_build_device_info(coordinator, node)
        # EPCs this entity is interested in, used to (un)subscribe with
        # DeviceManager so that a disabled entity's EPCs stop being polled.
        # Subclasses that manage more than one EPC (dedicated-platform
        # entities: climate/fan/cover/light/lock/water_heater, and the
        # Installation Location select entities) must set this explicitly
        # after calling super().__init__(); ``EchonetLiteDescribedEntity``
        # sets it automatically from its single ``description.epc``.
        self._subscribed_epcs: frozenset[int] = frozenset()
        self._unsub_epc_subscription: Callable[[], None] | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe this entity's EPCs for polling and updates."""
        await super().async_added_to_hass()
        self._unsub_epc_subscription = self.coordinator.device_manager.subscribe_epcs(
            self._node.device_key, self._subscribed_epcs
        )
        self.async_on_remove(
            self.coordinator.async_add_device_listener(
                self._node.device_key,
                self._subscribed_epcs,
                self.async_write_ha_state,
            )
        )

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe this entity's EPCs so they stop being polled."""
        if self._unsub_epc_subscription is not None:
            self._unsub_epc_subscription()
            self._unsub_epc_subscription = None
        await super().async_will_remove_from_hass()

    @property
    @override
    def available(self) -> bool:
        """Return True if the underlying device is responding to polling.

        Falls back to ``CoordinatorEntity.available`` first so disabled
        coordinators still mark entities unavailable. Devices without a
        GET-capable liveness EPC remain available because their liveness
        cannot be determined by the polling client.
        """
        if not super().available:
            return False
        return (
            self.coordinator.device_manager.is_device_polling_available(
                self._node.device_key
            )
            is not False
        )

    async def _send_property(self, epc: int, value: bytes) -> None:
        """Send a SetC request for a single EPC/value pair.

        Args:
            epc: ECHONET Property Code
            value: Property Data Content (EDT)

        Raises:
            HomeAssistantError: If the EPC is not writable by the device.
        """
        await self._send_properties(properties=[Property(epc=epc, edt=value)])

    async def _send_properties(self, properties: list[Property]) -> None:
        """Send a SetC request for multiple EPC/value pairs.

        Args:
            properties: List of Property objects to send

        Raises:
            HomeAssistantError: If any EPC is not writable by the device.
        """
        node = self._node
        not_writable = [
            prop.epc for prop in properties if prop.epc not in node.set_epcs
        ]
        if not_writable:
            hex_list = ", ".join(f"0x{epc:02X}" for epc in not_writable)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="epc_not_writable",
                translation_placeholders={"epc_list": hex_list},
            )
        result = await self.coordinator.device_manager.set_properties(
            node_id=node.node_id,
            deoj=node.eoj,
            properties=properties,
            refresh=True,
        )
        if not result.sent:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="target_node_unknown",
            )
        if result.rejected_epcs or result.unanswered_epcs:
            rejected_epcs = (
                ", ".join(f"0x{epc:02X}" for epc in sorted(result.rejected_epcs))
                or "none"
            )
            unanswered_epcs = (
                ", ".join(f"0x{epc:02X}" for epc in sorted(result.unanswered_epcs))
                or "none"
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_failed",
                translation_placeholders={
                    "rejected_epcs": rejected_epcs,
                    "unanswered_epcs": unanswered_epcs,
                },
            )

    async def _send_prop[ValueT](self, prop: Prop[ValueT], value: ValueT) -> None:
        """Encode value via prop and send as a SetC request for this EPC.

        Raises:
            HomeAssistantError: If the EPC is not writable by the device.
        """
        try:
            property_value = prop.make_property(value)
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_property_value",
                translation_placeholders={"error": str(err)},
            ) from err
        await self._send_properties([property_value])


@dataclass(frozen=True, kw_only=True)
class EchonetLiteEntityDescription(EntityDescription):
    """Base entity description for ECHONET Lite entities with common fields.

    This class provides common ECHONET-related fields and methods for all
    entity descriptions. Platform-specific descriptions should inherit from both
    this class and the appropriate platform EntityDescription using diamond
    inheritance:

        class EchonetLiteSensorEntityDescription(SensorEntityDescription, EchonetLiteEntityDescription):
            ...

    The diamond inheritance pattern works correctly because both this class and
    platform-specific EntityDescriptions inherit from EntityDescription, and
    Python's MRO resolves the inheritance properly with kw_only=True.
    """

    epc: int
    """ECHONET Property Code."""
    manufacturer_code: int | None = None
    """Required manufacturer code for vendor-specific entities (None = all)."""
    coefficient_epcs: tuple[int, ...] = ()
    """Sibling EPCs (MRA ``coefficient``) this property's value depends on.

    Must also be polled (see :class:`EchonetLiteDescribedEntity`'s
    ``_subscribed_epcs``) even though they are never exposed as their own
    entity, or :class:`~pyhems.NumericCodec`'s coefficient resolution would
    never see a value for them.
    """

    def should_create(self, node: NodeState) -> bool:
        """Check if entity should be created for this node.

        Args:
            node: The node state to check against.

        Returns:
            True if the entity should be created for this node.
        """
        # Check if EPC is available in either GET or SET property map
        # (write-only button entities are only in set_epcs)
        if self.epc not in node.get_epcs and self.epc not in node.set_epcs:
            return False
        if self.manufacturer_code is not None:
            return node.manufacturer_code == self.manufacturer_code
        return True

    @classmethod
    def _common_kwargs(
        cls,
        entity_def: EntityDefinition,
    ) -> dict:
        """Return the common kwargs shared by every platform description.

        Use as ``**cls._common_kwargs(entity_def)`` inside a subclass
        :meth:`build_from_entity_def` override to inject the fields that are
        identical across all platforms, while spelling out the
        platform-specific fields as named arguments for type-checker
        visibility.
        """
        return {
            "translation_key": entity_def.id,
            "epc": entity_def.epc,
            "entity_category": get_entity_category(entity_def),
            "manufacturer_code": entity_def.manufacturer_code,
            "coefficient_epcs": entity_def.coefficient_epcs or (),
        }

    @classmethod
    def build_from_entity_def(
        cls,
        class_code: int,
        entity_def: EntityDefinition,
    ) -> Self:
        """Construct an instance from a pyhems EntityDefinition of ``class_code``.

        Subclasses must override this classmethod.  The override should call
        ``cls(key=…, prop=…, **cls._common_kwargs(entity_def))`` so that
        platform-specific fields are spelled out as named arguments
        (enabling type-checker validation) while the common fields are
        injected from :meth:`_common_kwargs`.
        """
        raise NotImplementedError  # pragma: no cover


class EchonetLiteDescribedEntity[DescriptionT: EchonetLiteEntityDescription](
    EchonetLiteEntity
):
    """Base class for ECHONET Lite entities with EntityDescription.

    This intermediate class handles the common initialization pattern shared by
    binary_sensor, button, number, select, sensor, and switch platforms. It
    extracts the repetitive __init__ logic that sets up unique_id,
    translation_key/name, and epc from the entity description.

    Entities that manage multiple EPCs (climate, fan, cover, light, lock,
    water_heater) inherit from EchonetLiteEntity directly and handle their own
    initialization.

    The `description` attribute provides type-safe access to the entity
    description with the correct generic type, avoiding mypy conflicts with
    platform base classes that define `entity_description`.
    """

    description: DescriptionT

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: DescriptionT,
    ) -> None:
        """Initialize a described ECHONET Lite entity.

        The caller has already checked ``description.should_create(node)``.

        Args:
            coordinator: The data update coordinator.
            node: The node state for this entity.
            description: The entity description with EPC metadata.
        """
        super().__init__(coordinator, node)
        self.description = description
        self.entity_description = description  # HA standard attribute
        self._attr_unique_id = f"{node.device_key}-{description.key}"
        # ``translation_key`` is guaranteed to be set by all platform
        # description factories (always ``entity_def.id``), and the
        # ``scripts/generate_strings.py`` generator produces a matching entry
        # in ``strings.json`` for every entity definition the platforms
        # consume. See ``tests/components/echonet_lite/test_generate_strings
        # ::TestDefinitionsStringsConsistency`` for the regression guard.
        self._attr_translation_key = description.translation_key
        # Coefficient EPCs (e.g. EPC 0xC2) are never exposed as their own
        # entity, so this entity must subscribe to them directly or they
        # would never actually be polled; see coefficient_epcs' docstring.
        self._subscribed_epcs = frozenset({description.epc}) | frozenset(
            description.coefficient_epcs
        )

    @callback
    def _async_listen_timezone_updates(self) -> None:
        """Reinterpret cached local datetimes when HA's timezone changes."""

        @callback
        def timezone_updated(event: Event) -> None:
            if "time_zone" in event.data:
                self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, timezone_updated)
        )


def build_platform_descriptions[DescriptionT: EchonetLiteEntityDescription](
    platform_type: Platform,
    description_cls: type[DescriptionT],
) -> dict[int, list[DescriptionT]]:
    """Build a class-code-keyed description dict for a platform at module load time.

    This is called once per platform at import time to produce static constants
    from the pyhems REGISTRY, avoiding repeated construction on every config
    entry setup.

    Each ``description_cls`` must override :meth:`EchonetLiteEntityDescription.
    build_from_entity_def` to construct an instance with named platform-specific
    arguments plus ``**cls._common_kwargs(entity_def)``.

    Args:
        platform_type: Target platform (e.g. Platform.SENSOR).
        description_cls: The description dataclass to instantiate; must override
            :meth:`EchonetLiteEntityDescription.build_from_entity_def`.

    Returns:
        Dict mapping class_code → list of entity descriptions for that platform.
    """
    descriptions: dict[int, list[DescriptionT]] = {}
    for class_code, entries in CATALOG.items():
        descriptions[class_code] = []
        for entry in entries:
            if entry.platform == platform_type:
                descriptions[class_code].append(
                    description_cls.build_from_entity_def(class_code, entry.entity_def)
                )
    return descriptions


def setup_common_platform[DescriptionT: EchonetLiteEntityDescription](
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform_domain: str,
    descriptions_by_class_code: dict[int, list[DescriptionT]],
    entity_factory: Callable[[EchonetLiteCoordinator, NodeState, DescriptionT], Entity],
) -> None:
    """Set up common entity platform setup pattern for ECHONET Lite.

    This helper handles:
    - Creating entities for existing devices
    - Subscribing to device-added notifications for new device discovery
    - Logging skipped entities for debugging

    The ``descriptions_by_class_code`` dict should be built at module load time
    using :func:`build_platform_descriptions` so that description construction
    happens only once rather than on every config entry setup.

    Args:
        entry: The config entry
        async_add_entities: Callback to add entities
        descriptions_by_class_code: Pre-built mapping of class_code → descriptions,
            typically a module-level constant produced by build_platform_descriptions.
        entity_factory: Factory function to create entity instances

    """

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        entities: list[Entity] = []
        for description in descriptions_by_class_code.get(node.eoj.class_code, []):
            if not description.should_create(node):
                _LOGGER.debug(
                    "Skipping %s for %s: EPC 0x%02X not meeting criteria",
                    description.key,
                    node.device_key,
                    description.epc,
                )
                continue
            entities.append(entity_factory(coordinator, node, description))
        return entities

    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        platform_domain=platform_domain,
        entity_factory=_entity_factory,
    )


def setup_dedicated_platform[DescriptionT](
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform_domain: str,
    descriptions: dict[int, DescriptionT],
    entity_factory: Callable[[EchonetLiteCoordinator, NodeState, DescriptionT], Entity],
) -> None:
    """Set up a dedicated entity platform with one entity per matching class code.

    This is the counterpart of :func:`setup_echonet_lite_platform` for platforms
    that maintain a static ``dict[int, DescriptionT]`` constant (one description
    per class code) rather than a list of descriptions per class code.

    The pattern is used by platforms whose entities are not driven by the MRA
    definitions registry (climate, cover, fan, light, lock, water_heater).

    Args:
        entry: The config entry.
        async_add_entities: Callback to add entities.
        descriptions: Module-level constant mapping class_code → single description.
        entity_factory: Called with (coordinator, node, description) to create one entity.
    """

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        if (description := descriptions.get(node.eoj.class_code)) is None:
            return []
        return [entity_factory(coordinator, node, description)]

    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        platform_domain=platform_domain,
        entity_factory=_entity_factory,
    )


def _has_enabled_entity_candidate(
    hass: HomeAssistant,
    platform_domain: str,
    entities: list[Entity],
) -> bool:
    """Return True if any entity is enabled or not yet represented in the registry."""
    entity_registry = er.async_get(hass)
    for entity in entities:
        if (unique_id := entity.unique_id) is None:
            return True
        if (
            entity_id := entity_registry.async_get_entity_id(
                platform_domain, DOMAIN, unique_id
            )
        ) is None:
            return True
        if (entry := entity_registry.async_get(entity_id)) is None:
            return True
        if entry.disabled_by is None:
            return True
    return False


def setup_echonet_lite_device_platform(
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    *,
    platform_domain: str,
    entity_factory: Callable[[EchonetLiteCoordinator, NodeState], list[Entity]],
) -> None:
    """Set up a platform that emits zero or more entities per discovered device.

    The ``entity_factory`` receives a node and returns the list of entities
    to create for it (empty list if the node is not handled by this platform).
    This is the generic building block used both by description-driven
    platforms (via ``setup_echonet_lite_platform``) and by platforms that
    create one dedicated entity per matching class code (climate, fan).

    Args:
        entry: The config entry
        async_add_entities: Callback to add entities
        entity_factory: Callable building entities for a given node.
    """
    coordinator = entry.runtime_data.coordinator
    known_device_keys: set[str] = set()

    @callback
    def _async_check_new_devices() -> None:
        """Create entities for any device that became known since last call.

        Uses the standard ``DataUpdateCoordinator.async_add_listener`` hook:
        every ``async_set_updated_data`` call for a ``DeviceAdded`` event triggers
        this listener; we then diff ``coordinator.data`` against the keys we
        have already processed to find new devices.
        """
        new_keys = coordinator.data.keys() - known_device_keys
        if not new_keys:
            return
        known_device_keys.update(new_keys)
        new_entities: list[Entity] = []
        for device_key in new_keys:
            node = coordinator.data.get(device_key)
            if node is None:
                continue
            device_entities = entity_factory(coordinator, node)
            if not device_entities or not _has_enabled_entity_candidate(
                coordinator.hass, platform_domain, device_entities
            ):
                # An empty subscription marks the device's interest as
                # settled, so EPCs nobody subscribes to stop being polled.
                entry.async_on_unload(
                    coordinator.device_manager.subscribe_epcs(device_key, frozenset())
                )
            new_entities.extend(device_entities)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_check_new_devices))
    # Initial setup: create entities for all already-known devices.
    _async_check_new_devices()
