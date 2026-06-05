"""Base entity classes for the HEMS Echonet Lite integration."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
import time

from pyhems import EntityDefinition, NodeState, Property

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_EPC,
    DEDICATED_PLATFORM_EPCS,
    DOMAIN,
    RUNTIME_MONITOR_MAX_SILENCE,
)
from .coordinator import EchonetLiteCoordinator
from .prop import Prop
from .types import EchonetLiteConfigEntry, EchonetLiteRuntimeData

_LOGGER = logging.getLogger(__name__)


def can_process_enum_values(entity: EntityDefinition) -> bool:
    """Check if entity's enum_values can be automatically processed.

    Enum values must have unique keys for automatic processing.
    Entities with duplicate keys cannot be reliably mapped and are
    excluded from platform creation and string generation.

    Args:
        entity: Entity definition to check.

    Returns:
        True if enum_values can be processed, False otherwise.
    """
    if not entity.enum_values:
        return True  # Numeric entities are always processable

    keys = set()
    for enum_val in entity.enum_values:
        if enum_val.key in keys:
            return False
        keys.add(enum_val.key)

    return True


def infer_platform(entity: EntityDefinition) -> Platform | None:
    """Infer the platform type from entity definition using MRA get/set info.

    Decision matrix:
        | Data shape    | readable + writable | readable only | write-only         |
        |---------------|---------------------|---------------|-----------         |
        | 2 enum values | switch              | binary_sensor | None (skip)        |
        | 3+ enum vals  | select              | sensor (ENUM) | None (skip)        |
        | 1 enum value  | None (skip)         | None (skip)   | button             |
        | numeric       | number              | sensor        | None (skip)        |

    Args:
        entity: Entity definition to analyze.

    Returns:
        Platform type string, or None if entity should be skipped.
    """
    # Readable property (get != notApplicable)
    if entity.get != "notApplicable":
        writable = entity.set != "notApplicable"
        if entity.enum_values:
            if len(entity.enum_values) == 1:
                # Single enum value on readable property is skipped
                return None
            if len(entity.enum_values) == 2:
                return Platform.SWITCH if writable else Platform.BINARY_SENSOR
            return Platform.SELECT if writable else Platform.SENSOR
        return Platform.NUMBER if writable else Platform.SENSOR

    # Write-only properties (get == notApplicable)
    if entity.set != "notApplicable":
        # Button: write-only with exactly 1 enum value (action command)
        if entity.enum_values and len(entity.enum_values) == 1:
            return Platform.BUTTON
    return None


def _get_or_build_device_info(
    runtime_data: EchonetLiteRuntimeData, node: NodeState
) -> DeviceInfo:
    """Return the cached ``DeviceInfo`` for ``node``, building it on first use."""
    cache = runtime_data.device_info_cache
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
    # the English MRA name. Unknown class codes (only possible with the
    # experimental flag) fall back to the shared ``unknown_class`` key with
    # the hex class code rendered through ``translation_placeholders``.
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

    # Threshold after which a lack of runtime activity marks the entity
    # as unavailable. Matches the inactivity repair issue threshold so
    # UI availability and the repair issue rise/fall together.
    _runtime_silence_threshold: float = RUNTIME_MONITOR_MAX_SILENCE.total_seconds()

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
    ) -> None:
        """Initialize the base entity for the given device key."""

        super().__init__(coordinator)
        self._node = node
        self._attr_device_info = _get_or_build_device_info(
            coordinator.config_entry.runtime_data, node
        )

    @property
    def available(self) -> bool:
        """Return True if the underlying runtime is still receiving activity.

        Falls back to ``CoordinatorEntity.available`` first so disabled
        coordinators still mark entities unavailable. Additionally, if the
        runtime has been silent for longer than ``RUNTIME_MONITOR_MAX_SILENCE``,
        the entity is reported as unavailable even while the coordinator
        itself is considered healthy.
        """
        if not super().available:
            return False
        last_activity_at = self.coordinator.last_runtime_activity_at
        if last_activity_at is None:
            # No baseline yet: rely on the coordinator's own availability.
            return True
        return time.monotonic() - last_activity_at < self._runtime_silence_threshold

    async def _async_send_property(self, epc: int, value: bytes) -> None:
        """Send a SetC request for a single EPC/value pair.

        Args:
            epc: ECHONET Property Code
            value: Property Data Content (EDT)

        Raises:
            HomeAssistantError: If the EPC is not writable by the device.
        """
        await self._async_send_properties(properties=[Property(epc=epc, edt=value)])

    async def _async_send_properties(self, properties: list[Property]) -> None:
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
        sent = await self.coordinator.config_entry.runtime_data.client.set_properties(
            node_id=node.node_id,
            deoj=node.eoj,
            properties=properties,
        )
        if not sent:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="target_node_unknown",
            )

        # After a Set operation, schedule an earlier poll so the UI reflects the
        # updated device state sooner.
        self.coordinator.config_entry.runtime_data.property_poller.schedule_immediate_poll(
            node.device_key
        )

    async def _async_send_prop[ValueT](self, prop: Prop[ValueT], value: ValueT) -> None:
        """Encode value via prop and send as a SetC request for this EPC.

        Raises:
            HomeAssistantError: If the EPC is not writable by the device.
        """
        await self._async_send_properties([prop.make_property(value)])


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

    class_code: int
    """ECHONET Lite class code (class group + class code)."""
    epc: int
    """ECHONET Property Code."""
    manufacturer_code: int | None = None
    """Required manufacturer code for vendor-specific entities (None = all)."""

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
    _epc: int

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: DescriptionT,
    ) -> None:
        """Initialize a described ECHONET Lite entity.

        Args:
            coordinator: The data update coordinator.
            node: The node state for this entity.
            description: The entity description with EPC metadata.

        Raises:
            ValueError: If description.should_create(node) returns False.
        """
        if not description.should_create(node):
            raise ValueError(
                f"Entity created for EPC 0x{description.epc:02X} "
                "that doesn't meet creation criteria"
            )
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
        self._epc = description.epc

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return extra state attributes exposing the ECHONET Property Code."""
        return {ATTR_EPC: f"0x{self._epc:02X}"}


def setup_echonet_lite_platform[DescriptionT: EchonetLiteEntityDescription](
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform_type: Platform,
    description_factory: Callable[[int, EntityDefinition], DescriptionT],
    entity_factory: Callable[[EchonetLiteCoordinator, NodeState, DescriptionT], Entity],
) -> None:
    """Set up common entity platform setup pattern for ECHONET Lite.

    This helper handles:
    - Retrieving entity definitions from the definitions registry
    - Building entity descriptions from definitions (filtered by platform_type)
    - Filtering out dedicated platform EPCs (from DEDICATED_PLATFORM_EPCS)
    - Creating entities for existing devices
    - Subscribing to device-added notifications for new device discovery
    - Logging skipped entities for debugging

    Args:
        entry: The config entry
        async_add_entities: Callback to add entities
        platform_type: Type of platform (e.g. Platform.SENSOR, Platform.SWITCH)
        description_factory: Factory function to create descriptions from definitions.
            Args: (class_code, entity_def)
        entity_factory: Factory function to create entity instances

    """
    runtime_data = entry.runtime_data
    definitions = runtime_data.definitions

    # Build descriptions from entity definitions, filtering by platform and dedicated platform EPCs
    descriptions_by_class_code: dict[int, list[DescriptionT]] = {}
    for class_code, entity_defs in definitions.entities.items():
        excluded = DEDICATED_PLATFORM_EPCS.get(class_code, frozenset())
        descriptions_by_class_code[class_code] = [
            description_factory(class_code, entity_def)
            for entity_def in entity_defs
            if infer_platform(entity_def) == platform_type
            and entity_def.epc not in excluded
            and can_process_enum_values(entity_def)
            # Skip writable bit/byte-packed sub-properties (set != notApplicable
            # and byte_offset > 0). A single EPC payload often carries multiple
            # sub-properties; only the leader (byte_offset == 0) owns the write
            # path. Allowing followers to be set independently would clobber the
            # sibling bits since pyhems writes the full EPC value at once.
            and not (entity_def.set != "notApplicable" and entity_def.byte_offset > 0)
        ]

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        entities: list[Entity] = []
        for description in descriptions_by_class_code.get(node.eoj.class_code, []):
            if not description.should_create(node):
                _LOGGER.debug(
                    "Skipping %s %s for %s: EPC 0x%02X not meeting criteria",
                    platform_type,
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
        entity_factory=_entity_factory,
    )


def setup_echonet_lite_device_platform(
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    *,
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
        every ``async_set_updated_data`` call in ``_on_device_added`` triggers
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
            new_entities.extend(entity_factory(coordinator, node))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_check_new_devices))
    # Initial setup: create entities for all already-known devices.
    _async_check_new_devices()
