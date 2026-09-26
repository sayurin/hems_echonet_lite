"""Shared ceiling-fan (class 0x013A) write helpers.

Every control change is one ``ceiling_fan_set_properties`` SetC. The helper
always inserts operation status and the buzzer; a light change also inserts
Wi-Fi control source, melody ``none``, and (when the lamp is not being turned
off) a lighting mode. Those companion EPCs are allowed through even when
absent from ``node.set_epcs``.
"""

from __future__ import annotations

from typing import Any

from pyhems import Property, ceiling_fan_set_properties

from .const import (
    EPC_CEILING_FAN_BUZZER,
    EPC_CEILING_FAN_CONTROL_SOURCE,
    EPC_CEILING_FAN_LIGHT_MODE,
    EPC_CEILING_FAN_MELODY,
    EPC_OPERATION_STATUS,
)

# Companion bytes that ceiling_fan_set_properties always (or conditionally)
# inserts alongside the caller's requested fields.
CEILING_FAN_COMPANION_EPCS: frozenset[int] = frozenset(
    {
        EPC_OPERATION_STATUS,
        EPC_CEILING_FAN_BUZZER,
        EPC_CEILING_FAN_CONTROL_SOURCE,
        EPC_CEILING_FAN_MELODY,
        EPC_CEILING_FAN_LIGHT_MODE,
    }
)


def build_ceiling_fan_properties(**kwargs: Any) -> list[Property]:
    """Build one silent SetC property list for a ceiling-fan control write."""
    return ceiling_fan_set_properties(silent=True, **kwargs)
