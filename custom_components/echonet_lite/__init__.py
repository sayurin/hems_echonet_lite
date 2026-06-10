"""The HEMS Echonet Lite integration."""

import asyncio
from contextlib import suppress
import logging
from typing import Final

from pyhems import REGISTRY, DeviceManager, HemsClient, PropertyPoller

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    CONF_ENABLE_EXPERIMENTAL,
    CONF_INTERFACE,
    DEDICATED_PLATFORM_EPCS,
    DEFAULT_INTERFACE,
    DEFAULT_POLL_INTERVAL,
    DISCOVERY_INTERVAL,
    DOMAIN,
    EPC_INSTALLATION_LOCATION,
    EPC_MANUFACTURER_CODE,
    EPC_PRODUCT_CODE,
    EPC_SERIAL_NUMBER,
    RUNTIME_MONITOR_INTERVAL,
    RUNTIME_MONITOR_MAX_SILENCE,
    STABLE_CLASS_CODES,
)
from .coordinator import EchonetLiteCoordinator
from .runtime import (
    EchonetLiteConfigEntry,
    EchonetLiteRuntimeData,
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

    # Build device-specific EPC sets for polling/notification
    # Start with definitions-based EPCs (MRA + vendor)
    monitored_epcs: dict[int, frozenset[int]] = {
        class_code: frozenset(entity_def.epc for entity_def in entity_defs)
        for class_code, entity_defs in REGISTRY.entities.items()
    }

    # Add dedicated platform EPCs (used for both exclusion and polling)
    for class_code, epcs in DEDICATED_PLATFORM_EPCS.items():
        monitored_epcs[class_code] = monitored_epcs.get(class_code, frozenset()) | epcs

    # EPC 0x81 (Installation Location) is a mandatory super-class property.
    # Ensure it is monitored for every known device class so the entity is
    # always populated regardless of whether the definitions include it.
    for class_code in list(monitored_epcs):
        monitored_epcs[class_code] = monitored_epcs[class_code] | {
            EPC_INSTALLATION_LOCATION
        }

    _LOGGER.debug(
        "Monitored EPCs (polling/notification) per device class: %s",
        {
            hex(class_code): " ".join(f"{epc:02x}" for epc in epcs)
            for class_code, epcs in monitored_epcs.items()
        },
    )

    # EPCs to request during node discovery (in addition to identification and instance list)
    discovery_epcs = [EPC_MANUFACTURER_CODE, EPC_PRODUCT_CODE, EPC_SERIAL_NUMBER]

    client = HemsClient(
        interface=interface,
        poll_interval=DISCOVERY_INTERVAL,
        extra_epcs=discovery_epcs,
    )

    # Determine which device class codes to accept
    class_code_filter: frozenset[int] | None = None
    if not enable_experimental:
        class_code_filter = STABLE_CLASS_CODES

    device_manager = DeviceManager(
        client=client,
        monitored_epcs=monitored_epcs,
        class_code_filter=class_code_filter,
    )
    coordinator = EchonetLiteCoordinator(
        hass,
        config_entry=entry,
        device_manager=device_manager,
    )

    runtime_health = RuntimeHealth()

    issue_monitor = RuntimeIssueMonitor(
        hass,
        coordinator,
        threshold=RUNTIME_MONITOR_MAX_SILENCE.total_seconds(),
        interval=RUNTIME_MONITOR_INTERVAL,
    )

    controller = RuntimeController(
        hass,
        entry,
        client=client,
        device_manager=device_manager,
        coordinator=coordinator,
        issue_monitor=issue_monitor,
        health=runtime_health,
    )

    await controller.async_start()

    property_poller = PropertyPoller(
        device_manager, poll_interval=DEFAULT_POLL_INTERVAL
    )
    property_poller.start()

    entry.runtime_data = EchonetLiteRuntimeData(
        controller=controller,
        property_poller=property_poller,
        device_info_cache={},
    )

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
    device_entry: dr.DeviceEntry,
) -> bool:
    """Remove a config entry from a device.

    Removal is permitted only when the device is no longer actively
    discovered on the local network (i.e. not present in coordinator data).
    """
    coordinator = config_entry.runtime_data.controller.coordinator
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
        runtime.controller.unsubscribe_runtime()
        runtime.controller.issue_monitor.stop()
        runtime.property_poller.stop()
        runtime.controller.discovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await runtime.controller.discovery_task
        runtime.controller.event_consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await runtime.controller.event_consumer_task
        await runtime.controller.client.stop()

    return True
