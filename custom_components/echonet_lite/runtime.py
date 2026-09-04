"""Runtime lifecycle management for the HEMS Echonet Lite integration."""

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from pyhems import (
    HemsClient,
    HemsErrorEvent,
    HemsFrameEvent,
    HemsInstanceListEvent,
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
_INITIAL_DISCOVERY_TIMEOUT = 30.0


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

    Encapsulates the restart lock, event queue, event consumer task,
    discovery task and the adaptive property poller so that
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
        self._event_queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self._initial_discovery_complete = asyncio.Event()
        self._periodic_discovery_started = False
        # Populated by ``async_start``; safe to access directly from
        # ``async_setup_entry`` because callers only read these after
        # ``async_start`` has completed without raising.
        self.unsubscribe_runtime: Callable[[], None] = lambda: None
        self.discovery_task: asyncio.Task[Any]
        self.event_consumer_task: asyncio.Task[None]

    async def async_start(self) -> None:
        """Subscribe, start the client and spawn background tasks."""
        unsubscribe = self.client.subscribe(self._handle_runtime_event)
        try:
            await self.client.start()
        except OSError as err:
            unsubscribe()
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

        self.event_consumer_task = self._entry.async_create_background_task(
            self._hass,
            self._consume_runtime_events(),
            name="echonet_lite_event_consumer",
        )
        self.discovery_task = self._entry.async_create_background_task(
            self._hass,
            self._run_initial_discovery(),
            name="echonet_lite_discovery",
        )
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
        self.discovery_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.discovery_task
        self.event_consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.event_consumer_task
        await self.client.stop()

    async def _run_initial_discovery(self) -> None:
        """Run the initial probe and start recurring discovery."""
        if not self.client.probe_initial_nodes():
            _LOGGER.warning("Initial ECHONET Lite node discovery could not be sent")
            self._start_periodic_discovery()
            return

        try:
            await asyncio.wait_for(
                self._initial_discovery_complete.wait(),
                timeout=_INITIAL_DISCOVERY_TIMEOUT,
            )
        except TimeoutError:
            _LOGGER.debug(
                "No ECHONET Lite node registration received during initial discovery"
            )

        self._start_periodic_discovery()

    def _start_periodic_discovery(self) -> None:
        """Start recurring discovery once for the current client lifecycle."""
        if self._periodic_discovery_started:
            return
        self._periodic_discovery_started = True
        self.client.start_periodic_discovery()

    @callback
    def _handle_runtime_event(self, event: RuntimeEvent) -> None:
        """Enqueue runtime events for the single consumer task.

        Kept synchronous and non-blocking so that pyhems' receiver loop
        continues immediately.
        """
        self._event_queue.put_nowait(event)

    async def _consume_runtime_events(self) -> None:
        """Serialize runtime event processing.

        Using a single consumer preserves the arrival order of
        ``HemsInstanceListEvent`` (device registration) and
        ``HemsFrameEvent`` (property updates) so that frames for a newly
        announced device are never applied before the device itself is
        registered in ``DeviceManager``.
        """
        while True:
            event = await self._event_queue.get()
            try:
                if isinstance(event, HemsFrameEvent):
                    self.coordinator.process_frame_event(event)
                    self.issue_monitor.record_activity(event.received_at)
                elif isinstance(event, HemsInstanceListEvent):
                    _LOGGER.debug(
                        "Runtime event: HemsInstanceListEvent from %s with %d instances",
                        event.node_id,
                        len(event.instances),
                    )
                    await self.coordinator.async_process_instance_list_event(event)
                    self.issue_monitor.record_activity(event.received_at)
                    self._initial_discovery_complete.set()
                    self._start_periodic_discovery()
                elif isinstance(event, HemsErrorEvent):
                    self.health.last_client_error = str(event.error)
                    self.health.last_client_error_at = event.received_at
                    _LOGGER.warning(
                        "ECHONET Lite runtime client encountered an error: %s",
                        event.error,
                    )
                    self.issue_monitor.record_client_error(str(event.error))
                    await self._async_restart_runtime()
            # Python 3.14+ multi-except syntax (PEP 758): a parenthesis-less
            # tuple of exception classes. Equivalent to ``except (A, B, C):``
            # on older versions.
            except OSError, LookupError, TypeError, ValueError:
                # Narrow to the fault classes realistic for frame parsing
                # and dispatch (I/O, missing keys, malformed payloads).
                # Programmer errors (RuntimeError, AssertionError, ...) are
                # intentionally allowed to propagate so the task fails
                # loudly instead of silently swallowing bugs.
                _LOGGER.exception(
                    "Failed to process ECHONET Lite runtime event: %r", event
                )
            finally:
                self._event_queue.task_done()

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
            self._periodic_discovery_started = False
            try:
                await self.client.start()
            except OSError as err:
                _LOGGER.error("Failed to restart ECHONET Lite runtime client: %s", err)
                self.health.last_client_error = str(err)
                self.health.last_client_error_at = time.monotonic()
                self.issue_monitor.record_client_error(str(err))
                return
            if self._initial_discovery_complete.is_set():
                self._start_periodic_discovery()
            else:
                self.client.probe_initial_nodes()
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
            # A shallow copy is sufficient: ``NodeState`` values are owned
            # by ``DeviceManager`` and only mutated from the single event
            # consumer task, so concurrent readers see a consistent snapshot.
            self.coordinator.async_set_updated_data(
                dict(self.coordinator.device_manager.data)
            )


EchonetLiteConfigEntry = ConfigEntry[RuntimeController]
