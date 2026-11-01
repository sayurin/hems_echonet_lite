"""Catalog of the entities and EPCs derived from pyhems definitions.

This module is the single place that decides which pyhems definitions become
generic-platform entities. The same decision drives the entity descriptions
of each platform, the EPCs monitored per device, and the strings generator.
"""

from dataclasses import dataclass
from typing import Final

from pyhems import (
    REGISTRY,
    DefinitionsRegistry,
    EntityDefinition,
    PropertyRole,
    get_collection_binding,
)

from homeassistant.const import Platform

from .const import (
    COLLECTION_SENSOR_PROJECTIONS,
    DEDICATED_PLATFORM_EPCS,
    DEDICATED_PLATFORM_REQUIRED_EPCS,
    EPC_INSTALLATION_LOCATION,
    EXCLUDED_EPCS_BY_CLASS,
)


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
        | true/false enum | switch            | binary_sensor | None (skip)        |
        | other enum values | select          | sensor (ENUM) | None (skip)        |
        | 1 enum value  | None (skip)         | None (skip)   | button             |
        | numeric       | number              | sensor        | None (skip)        |
        | numericValue  | None (skip)         | None (skip)   | None (skip)        |

    Args:
        entity: Entity definition to analyze.

    Returns:
        Platform type string, or None if entity should be skipped.
    """
    if entity.readable:
        writable = entity.writable
        if entity.enum_values:
            if len(entity.enum_values) == 1:
                # Single enum value on readable property is skipped
                return None
            if entity.is_binary:
                return Platform.SWITCH if writable else Platform.BINARY_SENSOR
            return Platform.SELECT if writable else Platform.SENSOR
        if entity.format is None:
            # MRA "numericValue" properties (e.g. EPC 0xC2 unit/coefficient
            # tables) are not exposed as HA entities themselves; they are
            # only consumed internally via other entities' coefficient_epcs.
            return None
        return Platform.NUMBER if writable else Platform.SENSOR

    # Write-only properties
    if entity.writable:
        # Button: write-only with exactly 1 enum value (action command)
        if entity.enum_values and len(entity.enum_values) == 1:
            return Platform.BUTTON
    return None


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """A pyhems definition exposed as a generic-platform entity."""

    platform: Platform
    entity_def: EntityDefinition


def _entity_platform(class_code: int, entity_def: EntityDefinition) -> Platform | None:
    """Return the generic platform of ``entity_def``, or None when not exposed."""
    if entity_def.epc in DEDICATED_PLATFORM_EPCS.get(class_code, frozenset()):
        return None
    if entity_def.epc in EXCLUDED_EPCS_BY_CLASS.get(class_code, frozenset()):
        return None
    if not can_process_enum_values(entity_def):
        return None
    # A single EPC payload often carries multiple sub-properties; only the
    # leader (byte_offset == 0) owns the write path. Allowing followers to be
    # set independently would clobber the sibling bytes since the whole EPC
    # value is written at once.
    if entity_def.writable and entity_def.byte_offset > 0:
        return None
    return infer_platform(entity_def)


def build_catalog(
    registry: DefinitionsRegistry,
) -> dict[int, tuple[CatalogEntry, ...]]:
    """Return the generic-platform entities of every device class."""
    return {
        class_code: tuple(
            CatalogEntry(platform, entity_def)
            for entity_def in entity_defs
            if (platform := _entity_platform(class_code, entity_def)) is not None
        )
        for class_code, entity_defs in registry.entities.items()
    }


CATALOG: Final[dict[int, tuple[CatalogEntry, ...]]] = build_catalog(REGISTRY)


def _entry_epcs(entry: CatalogEntry) -> frozenset[int]:
    """Return the EPCs read by the entity of ``entry``."""
    return frozenset({entry.entity_def.epc}) | frozenset(
        entry.entity_def.coefficient_epcs or ()
    )


def _collection_projection_epcs(class_code: int) -> frozenset[int]:
    """Return the EPCs needed by class_code's curated collection projections."""
    epcs: set[int] = set()
    for projection in COLLECTION_SENSOR_PROJECTIONS:
        if projection.class_code != class_code:
            continue
        epcs.add(projection.result_epc)
        epcs.update(projection.coefficient_epcs)
        binding = get_collection_binding(class_code, projection.result_epc)
        if binding is not None and binding.count_epc is not None:
            epcs.add(binding.count_epc)
    return frozenset(epcs)


# EPCs monitored (polled or notified) for every device of a class,
# regardless of its manufacturer: the EPCs of the catalog entities that do
# not depend on the manufacturer, the dedicated-platform EPCs, the
# installation location (EPC 0x81, a mandatory super-class property), and
# the EPCs of the curated collection projections (paged list properties
# have no scalar definition but back the per-channel collection sensors).
def _build_class_epcs() -> dict[int, frozenset[int]]:
    class_codes = (
        set(CATALOG)
        | set(DEDICATED_PLATFORM_REQUIRED_EPCS)
        | {projection.class_code for projection in COLLECTION_SENSOR_PROJECTIONS}
    )
    return {
        class_code: frozenset().union(
            *(
                _entry_epcs(entry)
                for entry in CATALOG.get(class_code, ())
                if entry.entity_def.manufacturer_code is None
            )
        )
        | DEDICATED_PLATFORM_REQUIRED_EPCS.get(class_code, frozenset())
        | {EPC_INSTALLATION_LOCATION}
        | _collection_projection_epcs(class_code)
        for class_code in class_codes
    }


CLASS_EPCS: Final[dict[int, frozenset[int]]] = _build_class_epcs()


# EPCs monitored only for devices of one manufacturer, per
# (class_code, manufacturer_code): the EPCs of manufacturer-specific catalog
# entities. The manufacturer code is read before the other EPCs during setup.
def _build_vendor_epcs() -> dict[tuple[int, int], frozenset[int]]:
    result: dict[tuple[int, int], set[int]] = {}
    for class_code, entries in CATALOG.items():
        for entry in entries:
            manufacturer_code = entry.entity_def.manufacturer_code
            if manufacturer_code is None:
                continue
            result.setdefault((class_code, manufacturer_code), set()).update(
                _entry_epcs(entry) - CLASS_EPCS[class_code]
            )
    return {key: frozenset(epcs) for key, epcs in result.items() if epcs}


VENDOR_EPCS: Final[dict[tuple[int, int], frozenset[int]]] = _build_vendor_epcs()


def monitored_epcs_for(class_code: int, manufacturer_code: int) -> frozenset[int]:
    """Return the EPCs to monitor for a device class and manufacturer."""
    return CLASS_EPCS.get(class_code, frozenset()) | VENDOR_EPCS.get(
        (class_code, manufacturer_code), frozenset()
    )


# EPCs that should be polled at a higher frequency (e.g. instantaneous power),
# per device class code.
#
# The candidates are the curated PropertyRole.INSTANTANEOUS role (see
# pyhems.definitions.PropertyRole), not a name-based heuristic, plus the
# COLLECTION_SENSOR_PROJECTIONS entries marked ``fast_poll=True`` (e.g. class
# 0x0287's instantaneous power lists), which have no scalar definition to
# derive a role from. Only monitored EPCs are kept.
def _build_fast_poll_epcs() -> dict[int, frozenset[int]]:
    result: dict[int, frozenset[int]] = {}
    for class_code, entity_defs in REGISTRY.entities.items():
        monitored = CLASS_EPCS.get(class_code, frozenset()).union(
            *(
                epcs
                for (vendor_class, _), epcs in VENDOR_EPCS.items()
                if vendor_class == class_code
            )
        )
        candidates = (
            frozenset(
                entity_def.epc
                for entity_def in entity_defs
                if entity_def.role is PropertyRole.INSTANTANEOUS
            )
            & monitored
        )
        if candidates:
            result[class_code] = candidates
    for projection in COLLECTION_SENSOR_PROJECTIONS:
        if not projection.fast_poll:
            continue
        candidates = frozenset({projection.result_epc}) & CLASS_EPCS.get(
            projection.class_code, frozenset()
        )
        if candidates:
            result[projection.class_code] = (
                result.get(projection.class_code, frozenset()) | candidates
            )
    return result


FAST_POLL_EPCS: Final[dict[int, frozenset[int]]] = _build_fast_poll_epcs()
