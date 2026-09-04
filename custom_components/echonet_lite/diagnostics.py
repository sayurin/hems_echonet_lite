"""Diagnostics support for the HEMS Echonet Lite integration."""

from collections.abc import Mapping
from typing import Any

from pyhems import DeviceManager, NodeState, decode_collection_page

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_UNIQUE_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import (
    COLLECTION_SENSOR_PROJECTIONS,
    CONF_INTERFACE,
    DOMAIN,
    EXCLUDED_EPCS_BY_CLASS,
)
from .runtime import EchonetLiteConfigEntry

TO_REDACT = {
    CONF_INTERFACE,
    CONF_UNIQUE_ID,
    "device_key",
    "node_id",
    "serial_number",
}

# EPC values whose EDT contents identify the individual device
# (Identification number / Production number). Their hex payload appears in
# the ``properties`` block of the diagnostics output and must be redacted
# alongside ``serial_number``.
TO_REDACT_PROPERTY_EPCS = frozenset({0x83, 0x8D})


def _format_epcs(epcs: frozenset[int]) -> str:
    """Return sorted EPCs as hexadecimal strings."""
    return " ".join([f"{epc:02X}" for epc in sorted(epcs)])


def _format_properties(properties: Mapping[int, bytes]) -> dict[str, str]:
    """Return properties as EPC -> EDT hex mapping."""
    return {
        f"0x{epc:02X}": REDACTED if epc in TO_REDACT_PROPERTY_EPCS else edt.hex(" ")
        for epc, edt in sorted(properties.items())
    }


def _collection_pages(node: NodeState) -> dict[str, Any]:
    """Return minimal decode status for this node's collection (list) properties.

    Reports ``start``/``count``/``ok`` per curated projection (see
    const.py's ``COLLECTION_SENSOR_PROJECTIONS``), not the decoded item
    values themselves — full item lists and history are intentionally kept
    out of diagnostics.
    """
    excluded = EXCLUDED_EPCS_BY_CLASS.get(node.eoj.class_code, frozenset())
    result: dict[str, Any] = {}
    if excluded:
        result["suppressed_epcs"] = _format_epcs(excluded)
    pages: dict[str, Any] = {}
    for projection in COLLECTION_SENSOR_PROJECTIONS:
        if projection.class_code != node.eoj.class_code:
            continue
        key = f"0x{projection.result_epc:02X}"
        edt = node.properties.get(projection.result_epc)
        if edt is None:
            pages[key] = {"ok": False, "start": None, "count": None}
            continue
        page = decode_collection_page(
            node.eoj.class_code, projection.result_epc, edt, node
        )
        pages[key] = (
            {"ok": True, "start": page.start, "count": page.count}
            if page is not None
            else {"ok": False, "start": None, "count": None}
        )
    if pages:
        result["pages"] = pages
    return result


def _node_to_dict(node: NodeState, device_manager: DeviceManager) -> dict[str, Any]:
    """Serialize ``NodeState`` into a diagnostics-friendly dictionary."""
    eoj = node.eoj

    node_dict = {
        "device_key": node.device_key,
        "eoj": f"0x{eoj:06X}",
        "class_code": f"0x{eoj.class_code:04X}",
        "instance": eoj.instance_number,
        "node_id": node.node_id,
        "manufacturer_code": f"0x{node.manufacturer_code:06X}",
        "manufacturer_name_en": node.manufacturer_name_en,
        "manufacturer_name_ja": node.manufacturer_name_ja,
        "product_code": node.product_code,
        "serial_number": node.serial_number,
        "get_epcs": _format_epcs(node.get_epcs),
        "set_epcs": _format_epcs(node.set_epcs),
        "inf_epcs": _format_epcs(node.inf_epcs),
        # INF_REQ (0x63) subscription outcome: attempted is the candidate
        # set (monitored & inf_epcs); confirmed is the subset acknowledged
        # via a 0x73 response ("subscribed successfully"); failed is the
        # subset rejected (0x53) or unanswered, which falls back to polling.
        "attempted_inf_epcs": _format_epcs(node.attempted_inf_epcs),
        "confirmed_inf_epcs": _format_epcs(node.confirmed_inf_epcs),
        "failed_inf_epcs": _format_epcs(node.failed_inf_epcs),
        "poll_epcs": _format_epcs(node.poll_epcs),
        "fast_poll_epcs": _format_epcs(node.fast_poll_epcs),
        # Narrowed by disabled-entity EPC subscriptions (Step 6). May equal
        # the corresponding candidate set above if no entity has been
        # disabled, or if subscriptions haven't been confirmed yet.
        "effective_poll_epcs": _format_epcs(
            device_manager.effective_poll_epcs(node.device_key)
        ),
        "effective_fast_poll_epcs": _format_epcs(
            device_manager.effective_fast_poll_epcs(node.device_key)
        ),
        "observed_batch_capacity": node.observed_batch_capacity,
        "properties": _format_properties(node.properties),
    }
    if collection := _collection_pages(node):
        node_dict["collection"] = collection
    return node_dict


def _add_poller_stats(
    node_dict: dict[str, Any],
    *,
    device_key: str,
    entry: EchonetLiteConfigEntry,
) -> None:
    """Attach adaptive poller runtime stats for one device."""
    stats = entry.runtime_data.property_poller.get_device_stats(device_key)
    node_dict["poller"] = {
        "normal_interval": round(stats.normal_interval, 3),
        "fast_interval": (
            None if stats.fast_interval is None else round(stats.fast_interval, 3)
        ),
        "latency_ewma": (
            None if stats.latency_ewma is None else round(stats.latency_ewma, 3)
        ),
        "consecutive_failures": stats.consecutive_failures,
    }


def _get_device_key(device: DeviceEntry) -> str | None:
    """Extract the ECHONET Lite ``device_key`` from a device entry."""
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            return identifier
    return None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    controller = entry.runtime_data
    coordinator = controller.coordinator
    health = controller.health

    devices: list[dict[str, Any]] = []
    for _, node in sorted(coordinator.data.items()):
        node_dict = _node_to_dict(node, coordinator.device_manager)
        _add_poller_stats(node_dict, device_key=node.device_key, entry=entry)
        devices.append(node_dict)

    data = {
        "config_entry": entry.as_dict(),
        "runtime": {
            "device_count": len(coordinator.data),
            "last_runtime_activity_seen": coordinator.last_runtime_activity_at
            is not None,
            "last_frame_received": coordinator.device_manager.last_frame_received_at
            is not None,
            "health": {
                "last_client_error": health.last_client_error,
                "last_client_error_recorded": health.last_client_error_at is not None,
                "last_restart_recorded": health.last_restart_at is not None,
                "restart_attempts": health.restart_attempts,
            },
            "tasks": {
                "event_consumer_task_done": (
                    coordinator.device_manager.event_consumer_task_done
                ),
            },
        },
        "devices": devices,
    }
    return async_redact_data(data, TO_REDACT)


async def async_get_device_diagnostics(
    hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    device: DeviceEntry,
) -> dict[str, Any]:
    """Return diagnostics for a device."""
    device_key = _get_device_key(device)
    if device_key is None:
        return {"error": "device_not_found", "reason": "missing_identifier"}

    coordinator = entry.runtime_data.coordinator
    node = coordinator.data.get(device_key)
    if node is None:
        return async_redact_data(
            {"device_key": device_key, "node_known": False}, TO_REDACT
        )

    node_dict = _node_to_dict(node, coordinator.device_manager)
    _add_poller_stats(node_dict, device_key=node.device_key, entry=entry)

    return async_redact_data(node_dict, TO_REDACT)
