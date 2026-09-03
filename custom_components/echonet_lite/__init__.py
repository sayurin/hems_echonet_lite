"""The HEMS Echonet Lite integration."""

import logging
from typing import Final

from pyhems import (
    REGISTRY,
    DeviceManager,
    HemsClient,
    PropertyPoller,
    PropertyRole,
    get_collection_binding,
)

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    COLLECTION_SENSOR_PROJECTIONS,
    CONF_ENABLE_EXPERIMENTAL,
    CONF_INTERFACE,
    DEDICATED_PLATFORM_EPCS,
    DEFAULT_FAST_POLL_INTERVAL,
    DEFAULT_INTERFACE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EPC_INSTALLATION_LOCATION,
    EXCLUDED_EPCS_BY_CLASS,
    RUNTIME_MONITOR_INTERVAL,
    RUNTIME_MONITOR_MAX_SILENCE,
    STABLE_CLASS_CODES,
)
from .coordinator import EchonetLiteCoordinator
from .runtime import (
    EchonetLiteConfigEntry,
    RuntimeController,
    RuntimeHealth,
    RuntimeIssueMonitor,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.FAN,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]


# EPCs to monitor (poll/notify) per device class code, built once at import time.
#
# Four layers are merged/removed:
#  1. Definition-driven EPCs from the pyhems REGISTRY (sensor/switch/select …)
#  2. Dedicated-platform EPCs from DEDICATED_PLATFORM_EPCS (climate/fan/cover …)
#  3. EPC 0x81 (Installation Location) — mandatory super-class property that
#     must be monitored for every known class even if absent from the registry.
#  4. EXCLUDED_EPCS_BY_CLASS EPCs are removed (e.g. class 0x0287's legacy
#     0xD0-0xEF), then COLLECTION_SENSOR_PROJECTIONS' EPCs are added back —
#     these are not in the pyhems REGISTRY (paged list properties have no
#     scalar EntityDefinition) but must still be polled for the dynamic,
#     per-node collection sensor entities built in sensor.py.
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


def _build_monitored_epcs() -> dict[int, frozenset[int]]:
    result: dict[int, frozenset[int]] = {
        class_code: frozenset(entity_def.epc for entity_def in entity_defs)
        for class_code, entity_defs in REGISTRY.entities.items()
    }
    for class_code, epcs in DEDICATED_PLATFORM_EPCS.items():
        result[class_code] = result.get(class_code, frozenset()) | epcs
    for class_code in list(result):
        result[class_code] = result[class_code] | {EPC_INSTALLATION_LOCATION}
    for class_code, excluded in EXCLUDED_EPCS_BY_CLASS.items():
        result[class_code] = result.get(class_code, frozenset()) - excluded
    for class_code in {p.class_code for p in COLLECTION_SENSOR_PROJECTIONS}:
        result[class_code] = result.get(class_code, frozenset()) | (
            _collection_projection_epcs(class_code)
        )
    return result


_MONITORED_EPCS: Final[dict[int, frozenset[int]]] = _build_monitored_epcs()


# EPCs that should be polled at a higher frequency (e.g. instantaneous power),
# per device class code, built once at import time.
#
# The candidate set is the curated PropertyRole.INSTANTANEOUS role (see
# pyhems.definitions.PropertyRole and scripts/property_roles.xlsx), not a
# name-based heuristic: whether a property is worth fast-polling is a
# protocol-level fact about the property, not something this integration
# should re-derive from its English name.
#
# Only EPCs already present in _MONITORED_EPCS are kept: a fast-poll
# candidate that isn't monitored/polled at all (e.g. belongs only to a
# disabled experimental class) should not be introduced by this table alone.
#
# COLLECTION_SENSOR_PROJECTIONS entries marked ``fast_poll=True`` (e.g. class
# 0x0287's instantaneous power lists) are added the same way, since they have
# no scalar EntityDefinition/PropertyRole to derive a role from.
def _build_fast_poll_epcs() -> dict[int, frozenset[int]]:
    result: dict[int, frozenset[int]] = {}
    for class_code, entity_defs in REGISTRY.entities.items():
        candidates = frozenset(
            entity_def.epc
            for entity_def in entity_defs
            if entity_def.role is PropertyRole.INSTANTANEOUS
        )
        candidates &= _MONITORED_EPCS.get(class_code, frozenset())
        if candidates:
            result[class_code] = candidates
    for projection in COLLECTION_SENSOR_PROJECTIONS:
        if not projection.fast_poll:
            continue
        candidates = frozenset({projection.result_epc}) & _MONITORED_EPCS.get(
            projection.class_code, frozenset()
        )
        if candidates:
            result[projection.class_code] = (
                result.get(projection.class_code, frozenset()) | candidates
            )
    return result


_FAST_POLL_EPCS: Final[dict[int, frozenset[int]]] = _build_fast_poll_epcs()


async def async_migrate_entry(
    hass: HomeAssistant, entry: EchonetLiteConfigEntry
) -> bool:
    """Migrate old config entry to new format."""
    if entry.version == 1 and entry.minor_version < 1:
        # Version 1.0 → 1.1: Move CONF_INTERFACE from options to data
        new_data = dict(entry.data)
        new_options = dict(entry.options)
        if CONF_INTERFACE in new_options:
            new_data[CONF_INTERFACE] = new_options.pop(CONF_INTERFACE)
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options, minor_version=1
        )
        _LOGGER.debug("Migrated config entry to version 1.1")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EchonetLiteConfigEntry) -> bool:
    """Set up HEMS Echonet Lite from a config entry."""

    interface = entry.data.get(CONF_INTERFACE, DEFAULT_INTERFACE)
    enable_experimental = entry.options.get(CONF_ENABLE_EXPERIMENTAL, False)

    _LOGGER.debug("Setting up ECHONET Lite with interface %s", interface)

    _LOGGER.debug(
        "Monitored EPCs (polling/notification) per device class: %s",
        {
            hex(class_code): " ".join(f"{epc:02x}" for epc in epcs)
            for class_code, epcs in _MONITORED_EPCS.items()
        },
    )
    _LOGGER.debug(
        "Fast-poll EPCs per device class: %s",
        {
            hex(class_code): " ".join(f"{epc:02x}" for epc in epcs)
            for class_code, epcs in _FAST_POLL_EPCS.items()
        },
    )

    client = HemsClient(
        interface=interface,
    )

    # Determine which device class codes to accept
    class_code_filter: frozenset[int] | None = (
        None if enable_experimental else STABLE_CLASS_CODES
    )

    device_manager = DeviceManager(
        client=client,
        monitored_epcs=_MONITORED_EPCS,
        class_code_filter=class_code_filter,
        fast_epcs=_FAST_POLL_EPCS,
    )

    runtime_health = RuntimeHealth()

    coordinator = EchonetLiteCoordinator(
        hass,
        config_entry=entry,
        device_manager=device_manager,
        health=runtime_health,
    )

    issue_monitor = RuntimeIssueMonitor(
        hass,
        coordinator,
        threshold=RUNTIME_MONITOR_MAX_SILENCE.total_seconds(),
        interval=RUNTIME_MONITOR_INTERVAL,
    )

    property_poller = PropertyPoller(
        device_manager,
        poll_interval=DEFAULT_POLL_INTERVAL,
        fast_poll_interval=DEFAULT_FAST_POLL_INTERVAL,
    )

    controller = RuntimeController(
        hass,
        entry,
        client=client,
        coordinator=coordinator,
        property_poller=property_poller,
        issue_monitor=issue_monitor,
        health=runtime_health,
    )

    await controller.async_start()

    entry.runtime_data = controller

    # Reload entry when options change
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: EchonetLiteConfigEntry
) -> None:
    """Handle options update by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: EchonetLiteConfigEntry,
    device_entry: dr.AnyDeviceEntry,
) -> bool:
    """Remove a config entry from a device.

    Removal is permitted only when the device is no longer actively
    discovered on the local network (i.e. not present in coordinator data).
    """
    coordinator = config_entry.runtime_data.coordinator
    return not device_entry.identifiers.intersection(
        (DOMAIN, device_key) for device_key in coordinator.data
    )


async def async_unload_entry(
    hass: HomeAssistant, entry: EchonetLiteConfigEntry
) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    runtime = entry.runtime_data
    if runtime:
        await runtime.async_stop()

    return True
