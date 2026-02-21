"""Constants for the HEMS echonet lite integration."""

from __future__ import annotations

from datetime import timedelta
import re

DOMAIN = "echonet_lite"
CONF_INTERFACE = "interface"
CONF_POLL_INTERVAL = "poll_interval"
CONF_ENABLE_EXPERIMENTAL = "enable_experimental"
DEFAULT_INTERFACE = "0.0.0.0"
DEFAULT_POLL_INTERVAL = 60
MIN_POLL_INTERVAL = 10
MAX_POLL_INTERVAL = 3600
UNIQUE_ID = "echonet_lite_singleton"
ISSUE_RUNTIME_CLIENT_ERROR = "runtime_client_error"
ISSUE_RUNTIME_INACTIVE = "runtime_inactive"
RUNTIME_MONITOR_INTERVAL = timedelta(minutes=1)
RUNTIME_MONITOR_MAX_SILENCE = timedelta(minutes=5)
DISCOVERY_INTERVAL = 60.0 * 60.0  # 1 hour

# Device identification EPCs
EPC_MANUFACTURER_CODE = 0x8A
EPC_PRODUCT_CODE = 0x8C
EPC_SERIAL_NUMBER = 0x8D

# Property map EPCs
EPC_INF_PROPERTY_MAP = 0x9D
EPC_SET_PROPERTY_MAP = 0x9E
EPC_GET_PROPERTY_MAP = 0x9F

# Stable (non-experimental) device class codes
# These device classes have been verified with real hardware.
# Other device classes are considered experimental.
STABLE_CLASS_CODES: frozenset[int] = frozenset(
    {
        0x0130,  # Home air conditioner
        0x0135,  # Air cleaner
        0x0279,  # Fuel cell (residential solar power generation)
        0x027D,  # In-house power generation (storage battery)
        0x05FF,  # Controller
    }
)

# Climate class code
CLASS_CODE_HOME_AIR_CONDITIONER = 0x0130

# Fan class code
CLASS_CODE_AIR_CLEANER = 0x0135

# EPCs managed by dedicated platform entities (climate, fan)
# - Excluded from other platforms (sensor/binary_sensor/select/switch) to avoid duplicates
# - Used for polling/notification to keep entity state up-to-date
DEDICATED_PLATFORM_EPCS: dict[int, frozenset[int]] = {
    CLASS_CODE_HOME_AIR_CONDITIONER: frozenset(
        {
            0x80,  # Operation status (on/off)
            0xA0,  # Fan speed
            0xA3,  # Swing mode
            0xB0,  # HVAC mode
            0xB3,  # Target temperature
        }
    ),
    CLASS_CODE_AIR_CLEANER: frozenset(
        {
            0x80,  # Operation status (on/off)
            0xA0,  # Air flow rate setting
        }
    ),
}


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case.

    MRA enum names use camelCase (e.g., 'automaticAirFlowDirection').
    HA uses snake_case for state keys (e.g., 'automatic_air_flow_direction').
    """
    s1 = re.sub(r"(?<!^)(?=[A-Z])", "_", name)
    return s1.lower()


__all__ = [
    "CLASS_CODE_AIR_CLEANER",
    "CLASS_CODE_HOME_AIR_CONDITIONER",
    "CONF_ENABLE_EXPERIMENTAL",
    "CONF_INTERFACE",
    "CONF_POLL_INTERVAL",
    "DEDICATED_PLATFORM_EPCS",
    "DEFAULT_INTERFACE",
    "DEFAULT_POLL_INTERVAL",
    "DISCOVERY_INTERVAL",
    "DOMAIN",
    "EPC_GET_PROPERTY_MAP",
    "EPC_INF_PROPERTY_MAP",
    "EPC_MANUFACTURER_CODE",
    "EPC_PRODUCT_CODE",
    "EPC_SERIAL_NUMBER",
    "EPC_SET_PROPERTY_MAP",
    "ISSUE_RUNTIME_CLIENT_ERROR",
    "ISSUE_RUNTIME_INACTIVE",
    "MAX_POLL_INTERVAL",
    "MIN_POLL_INTERVAL",
    "RUNTIME_MONITOR_INTERVAL",
    "RUNTIME_MONITOR_MAX_SILENCE",
    "STABLE_CLASS_CODES",
    "UNIQUE_ID",
    "camel_to_snake",
]
