"""Runtime lifecycle management for the HEMS Echonet Lite integration."""

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time

from pyhems import (
    HemsClient,
    HemsErrorEvent,
    PropertyPoller,
    RuntimeEvent,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, ISSUE_RUNTIME_CLIENT_ERROR, ISSUE_RUNTIME_INACTIVE
from .coordinator import EchonetLiteCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeHealth:
    """Health metadata tracked for the runtime client.

    The single canonical store for runtime health telemetry.
    ``EchonetLiteCoordinator`` delegates its ``last_runtime_activity_at``
    property to this object rather than keeping its own copy, so entities
    (which only have direct access to the coordinator) and diagnostics
    (which reaches this object via ``RuntimeController.health``) always see
    the same value.
    """

    last_client_error: str | None = None
    last_client_error_at: float | None = None
    last_restart_at: float | None = None
    restart_attempts: int = 0
    last_runtime_activity_at: float | None = None


class RuntimeIssueMonitor:
    """Monitor runtime activity and surface repair issues."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EchonetLiteCoordinator,
        *,
        threshold: float,
        interval: timedelta,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialise the monitor with inactivity threshold and check interval."""
        self._hass = hass
        self._coordinator = coordinator
        self._threshold = threshold
        self._interval = interval
        self._monotonic = monotonic
        self._cancel_interval: Callable[[], None] | None = None
        self._inactivity_issue_active = False
        self._client_issue_active = False

    def start(self) -> None:
        """Begin checking for runtime inactivity.

        Seeds ``last_runtime_activity_at`` with the current monotonic time so
        that a total absence of incoming frames (never a single activity
        observed) still trips the threshold. Without this baseline the
        inactivity check silently skips every tick while
        ``last_runtime_activity_at is None``.
        """
        if self._cancel_interval is not None:
            return
        self.record_activity(self._monotonic())
        self._cancel_interval = async_track_time_interval(
            self._hass, self._async_check_runtime, self._interval
        )

    def stop(self) -> None:
        """Stop monitoring and clear any active issue."""
        if self._cancel_interval:
            self._cancel_interval()
            self._cancel_interval = None
        self._clear_inactivity_issue_if_needed()
        self.clear_client_error()

    @callback
    def record_activity(self, timestamp: float) -> None:
        """Note that activity was observed and clear issues if present."""
        self._coordinator.record_runtime_activity(timestamp)
        self._clear_inactivity_issue_if_needed()

    @callback
    def _async_check_runtime(self, _now: datetime) -> None:
        last_activity_at = self._coordinator.last_runtime_activity_at
        if last_activity_at is None:
            return
        if self._monotonic() - last_activity_at < self._threshold:
            self._clear_inactivity_issue_if_needed()
            return
        if self._inactivity_issue_active:
            return
        minutes = max(int(self._threshold // 60), 1)
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            ISSUE_RUNTIME_INACTIVE,
            issue_domain=DOMAIN,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="runtime_inactive",
            translation_placeholders={"minutes": str(minutes)},
        )
        _LOGGER.warning(
            "No ECHONET Lite frames received for %d minutes; devices may be offline",
            minutes,
        )
        self._inactivity_issue_active = True
        # Entity ``available`` depends on the same silence threshold. Push a
        # listener update so entities re-evaluate availability right away
        # instead of waiting for the next frame (which, by definition, is
        # not arriving).
        self._coordinator.async_update_listeners()

    @callback
    def _clear_inactivity_issue_if_needed(self) -> None:
        if self._inactivity_issue_active:
            ir.async_delete_issue(self._hass, DOMAIN, ISSUE_RUNTIME_INACTIVE)
            self._inactivity_issue_active = False
            _LOGGER.info("ECHONET Lite communication restored")
            self._coordinator.async_update_listeners()

    @callback
    def record_client_error(self, message: str) -> None:
        """Create a repair issue describing the runtime client failure."""
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            ISSUE_RUNTIME_CLIENT_ERROR,
            issue_domain=DOMAIN,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="runtime_client_error",
            translation_placeholders={"error": message},
        )
        self._client_issue_active = True

    @callback
    def clear_client_error(self) -> None:
        """Clear any existing runtime client error issue."""
        if self._client_issue_active:
            ir.async_delete_issue(self._hass, DOMAIN, ISSUE_RUNTIME_CLIENT_ERROR)
            self._client_issue_active = False


class RuntimeController:
    """Own the pyhems runtime lifecycle for a config entry.

    Stored directly as ``entry.runtime_data`` (see ``EchonetLiteConfigEntry``
    below): it already exposes every object other modules need
    (``client``, ``coordinator``, ``property_poller``, ``issue_monitor``,
    ``health``), so a separate wrapper dataclass would only add a level of
    indirection without owning any state of its own.

    Encapsulates the restart lock and adaptive property poller so that
    ``async_setup_entry``/``async_unload_entry`` can stay focused on
    dependency wiring: :meth:`async_start` and :meth:`async_stop` are the
    single symmetric entry points for starting and tearing down everything
    this class owns.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EchonetLiteConfigEntry,
        *,
        client: HemsClient,
        coordinator: EchonetLiteCoordinator,
        property_poller: PropertyPoller,
        issue_monitor: RuntimeIssueMonitor,
        health: RuntimeHealth,
    ) -> None:
        """Initialise the controller with all runtime dependencies."""
        self._hass = hass
        self._entry = entry
        self.client = client
        self.coordinator = coordinator
        self.property_poller = property_poller
        self.issue_monitor = issue_monitor
        self.health = health
        self._restart_lock = asyncio.Lock()
        self._error_tasks: set[asyncio.Task[None]] = set()
        # Populated by ``async_start``; safe to access directly from
        # ``async_setup_entry`` because callers only read these after
        # ``async_start`` has completed without raising.
        self.unsubscribe_runtime: Callable[[], None] = lambda: None

    async def async_start(self) -> None:
        """Subscribe, start the client and spawn background tasks."""
        await self.coordinator.device_manager.async_start()
        unsubscribe = self.client.subscribe(self._handle_runtime_event)
        try:
            await self.client.start()
        except OSError as err:
            unsubscribe()
            await self.coordinator.device_manager.async_stop()
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="runtime_start_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        self.unsubscribe_runtime = unsubscribe

        # Initialize with empty state; nodes are discovered through runtime events
        self.coordinator.async_set_updated_data({})

        # ``RuntimeIssueMonitor.start`` seeds the inactivity baseline with
        # the current monotonic time so that a cold start with zero frames
        # still trips the threshold.
        self.issue_monitor.start()

        self.property_poller.start()

    async def async_stop(self) -> None:
        """Undo everything started by :meth:`async_start`.

        Mirrors ``async_setup_entry``/``async_unload_entry``'s previous
        manual teardown sequence so ``async_unload_entry`` can call this as
        a single step instead of reaching into the controller's tasks and
        sub-objects directly.
        """
        self.unsubscribe_runtime()
        self.issue_monitor.stop()
        self.property_poller.stop()
        for task in tuple(self._error_tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._error_tasks.clear()
        await self.coordinator.device_manager.async_stop()
        await self.client.stop()

    def _handle_runtime_event(self, event: RuntimeEvent) -> None:
        """Schedule handling for runtime errors."""
        if not isinstance(event, HemsErrorEvent):
            return
        task = self._entry.async_create_background_task(
            self._hass,
            self._async_handle_runtime_error(event),
            name="echonet_lite_runtime_error",
        )
        self._error_tasks.add(task)
        task.add_done_callback(self._error_tasks.discard)

    async def _async_handle_runtime_error(self, event: HemsErrorEvent) -> None:
        """Handle a runtime error and restart the client."""
        self.health.last_client_error = str(event.error)
        self.health.last_client_error_at = event.received_at
        _LOGGER.warning(
            "ECHONET Lite runtime client encountered an error: %s",
            event.error,
        )
        self.issue_monitor.record_client_error(str(event.error))
        await self._async_restart_runtime()

    async def _async_restart_runtime(self) -> None:
        """Restart the pyhems runtime client, debouncing concurrent callers."""
        if self._restart_lock.locked():
            return
        async with self._restart_lock:
            self.health.restart_attempts += 1
            try:
                await self.client.stop()
            except (
                OSError,
                RuntimeError,
            ) as err:  # pragma: no cover - best effort cleanup
                _LOGGER.debug("Failed to stop ECHONET Lite runtime client: %s", err)
            try:
                await self.client.start()
            except OSError as err:
                _LOGGER.error("Failed to restart ECHONET Lite runtime client: %s", err)
                self.health.last_client_error = str(err)
                self.health.last_client_error_at = time.monotonic()
                self.issue_monitor.record_client_error(str(err))
                return
            self.health.last_restart_at = time.monotonic()
            self.issue_monitor.clear_client_error()
            # Treat a successful restart as activity so the inactivity issue
            # (if any) is cleared immediately instead of waiting for the
            # next incoming frame.
            self.issue_monitor.record_activity(time.monotonic())
            # Re-publish the current DeviceManager state so entities for
            # already-known devices stay available after the restart.
            # DeviceManager retains its ``data`` across client stop/start,
            # so clearing the coordinator here would make those entities
            # disappear silently until each device is re-announced.
            # A shallow copy is sufficient because NodeState values are owned
            # by DeviceManager and only mutated by its event consumer.
            self.coordinator.async_set_updated_data(
                dict(self.coordinator.device_manager.data)
            )


EchonetLiteConfigEntry = ConfigEntry[RuntimeController]
