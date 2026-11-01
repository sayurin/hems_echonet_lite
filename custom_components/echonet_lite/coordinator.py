"""Data coordinator for the HEMS Echonet Lite integration."""

from collections.abc import Callable
import logging
from typing import TYPE_CHECKING, Any

from pyhems import (
    AvailabilityChanged,
    CollectionPage,
    DeviceAdded,
    DeviceManager,
    DeviceManagerEvent,
    NodeState,
    PropertiesChanged,
)

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN

if TYPE_CHECKING:
    from .runtime import EchonetLiteConfigEntry

_LOGGER = logging.getLogger(__name__)


class EchonetLiteCoordinator(DataUpdateCoordinator[dict[str, NodeState]]):
    """Coordinator that tracks state for detected SEOJ nodes.

    Delegates device management (discovery, frame processing, property tracking)
    to pyhems DeviceManager. The coordinator data (the device snapshot) is
    republished when a device is added; property and availability changes
    are delivered only to the entities of the affected device.
    """

    config_entry: EchonetLiteConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: EchonetLiteConfigEntry,
        device_manager: DeviceManager,
    ) -> None:
        """Initialize the coordinator for a specific config entry."""

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_coordinator",
            update_interval=None,
            config_entry=config_entry,
        )
        # ``async_set_updated_data`` is the canonical way to publish data on
        # ``DataUpdateCoordinator``. ``async_setup_entry`` seeds the empty
        # snapshot right after startup; this default keeps ``.data`` iterable
        # for callers (and tests) that inspect it before the first update.
        self.data: dict[str, NodeState] = {}
        self.device_manager = device_manager
        # Per-node ``DeviceInfo`` cache keyed by ``node.device_key``, shared by
        # every entity platform bound to that node.
        self.device_info_cache: dict[str, DeviceInfo] = {}
        # Decoded collection pages keyed by (device_key, result EPC); each
        # value also stores the EDTs it was decoded from. Shared by every
        # channel entity of a list so a page is decoded once per change.
        self.collection_pages: dict[
            tuple[str, int], tuple[tuple[Any, ...], CollectionPage | None]
        ] = {}
        # device_key -> [(relevant EPCs, callback)]; see async_add_device_listener.
        self._device_listeners: dict[
            str, list[tuple[frozenset[int], CALLBACK_TYPE]]
        ] = {}

    @callback
    def async_handle_device_manager_event(self, event: DeviceManagerEvent) -> None:
        """Publish device manager events to listeners."""
        if isinstance(event, DeviceAdded):
            # Platforms detect newly added devices by diffing coordinator.data.
            self.async_set_updated_data(dict(self.device_manager.data))
        elif isinstance(event, PropertiesChanged):
            self._async_notify_device(event.device_key, event.epcs)
        elif isinstance(event, AvailabilityChanged):
            self._async_notify_device(event.device_key, None)

    @callback
    def async_add_device_listener(
        self,
        device_key: str,
        epcs: frozenset[int],
        update_callback: CALLBACK_TYPE,
    ) -> Callable[[], None]:
        """Call ``update_callback`` when the device changes.

        The callback runs when any of ``epcs`` changes or when the device's
        availability changes.

        Returns:
            Function that removes the listener.
        """
        entry = (epcs, update_callback)
        listeners = self._device_listeners.setdefault(device_key, [])
        listeners.append(entry)

        @callback
        def remove_listener() -> None:
            listeners.remove(entry)
            if not listeners:
                self._device_listeners.pop(device_key, None)

        return remove_listener

    @callback
    def _async_notify_device(
        self, device_key: str, epcs: frozenset[int] | None
    ) -> None:
        """Run the listeners of a device; ``epcs`` None runs every listener."""
        for listener_epcs, update_callback in list(
            self._device_listeners.get(device_key, ())
        ):
            if epcs is None or listener_epcs & epcs:
                update_callback()
