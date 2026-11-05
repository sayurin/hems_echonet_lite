"""The HEMS Echonet Lite integration."""

import logging
from typing import Final

from pyhems import DeviceManager, HemsClient, PropertyPoller

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .catalog import CLASS_EPCS, FAST_POLL_EPCS, VENDOR_EPCS, monitored_epcs_for
from .const import (
    CONF_INTERFACE,
    DEFAULT_FAST_POLL_INTERVAL,
    DEFAULT_INTERFACE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    RUNTIME_MONITOR_INTERVAL,
    RUNTIME_MONITOR_MAX_SILENCE,
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
    Platform.DATE,
    Platform.DATETIME,
    Platform.FAN,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
    Platform.WATER_HEATER,
]


async def async_migrate_entry(
    hass: HomeAssistant, entry: EchonetLiteConfigEntry
) -> None:
    """Migrate old config entry to new format."""
    if entry.version == 1 and entry.minor_version < 1:
        # Version 1.0 → 1.1: Move CONF_INTERFACE from options to data
        new_data = dict(entry.data)
        new_options = dict(entry.options)
        if CONF_INTERFACE in new_options:
            new_data[CONF_INTERFACE] = new_options.pop(CONF_INTERFACE)
        new_options.clear()
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options, minor_version=1
        )
        _LOGGER.debug("Migrated config entry to version 1.1")


async def async_setup_entry(hass: HomeAssistant, entry: EchonetLiteConfigEntry) -> None:
    """Set up HEMS Echonet Lite from a config entry."""

    interface = entry.data.get(CONF_INTERFACE, DEFAULT_INTERFACE)
    _LOGGER.debug("Setting up ECHONET Lite with interface %s", interface)
    _LOGGER.debug(
        "Monitored EPC candidates (polling/notification) per device class: %s; "
        "per class and manufacturer: %s",
        {
            hex(class_code): " ".join(f"{epc:02x}" for epc in sorted(epcs))
            for class_code, epcs in CLASS_EPCS.items()
        },
        {
            f"{class_code:#06x}/{manufacturer_code:#08x}": " ".join(
                f"{epc:02x}" for epc in sorted(epcs)
            )
            for (class_code, manufacturer_code), epcs in VENDOR_EPCS.items()
        },
    )
    _LOGGER.debug(
        "Fast-poll EPCs per device class: %s",
        {
            hex(class_code): " ".join(f"{epc:02x}" for epc in sorted(epcs))
            for class_code, epcs in FAST_POLL_EPCS.items()
        },
    )

    client = HemsClient(interface=interface)

    device_manager = DeviceManager(
        client=client,
        monitored_epcs=monitored_epcs_for,
        fast_epcs=FAST_POLL_EPCS,
    )

    runtime_health = RuntimeHealth()

    coordinator = EchonetLiteCoordinator(
        hass,
        config_entry=entry,
        device_manager=device_manager,
    )
    entry.async_on_unload(
        device_manager.subscribe(coordinator.async_handle_device_manager_event)
    )

    issue_monitor = RuntimeIssueMonitor(
        hass,
        coordinator,
        threshold=RUNTIME_MONITOR_MAX_SILENCE.total_seconds(),
        interval=RUNTIME_MONITOR_INTERVAL,
    )
    entry.async_on_unload(
        device_manager.subscribe(issue_monitor.async_handle_device_manager_event)
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: EchonetLiteConfigEntry,
    device_entry: dr.AnyDeviceEntry,
) -> bool:
    """Remove a config entry from a device.

    Removal is permitted when the device is unknown or its liveness polling
    has failed.
    """
    coordinator = config_entry.runtime_data.coordinator
    device_keys = {
        identifier
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    }
    if not device_keys:
        return True
    return any(
        (node := coordinator.data.get(device_key)) is None
        or node.polling_available is False
        for device_key in device_keys
    )


async def async_unload_entry(
    hass: HomeAssistant, entry: EchonetLiteConfigEntry
) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)

    runtime = entry.runtime_data
    if runtime:
        await runtime.async_stop()
