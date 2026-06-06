"""Shared types and data models for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyhems import DefinitionsRegistry, PropertyPoller

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

if TYPE_CHECKING:
    from .runtime import RuntimeController


@dataclass(slots=True)
class RuntimeHealth:
    """Health metadata tracked for the runtime client."""

    last_client_error: str | None = None
    last_client_error_at: float | None = None
    last_restart_at: float | None = None
    restart_attempts: int = 0


@dataclass(slots=True)
class EchonetLiteRuntimeData:
    """Runtime data stored on the config entry."""

    definitions: DefinitionsRegistry
    controller: RuntimeController
    property_poller: PropertyPoller
    # Per-node ``DeviceInfo`` cache keyed by ``node.device_key``. Built once
    # on first entity instantiation for a node and shared by every entity
    # platform bound to that node.
    device_info_cache: dict[str, DeviceInfo]


EchonetLiteConfigEntry = ConfigEntry[EchonetLiteRuntimeData]
