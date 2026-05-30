"""Cover platform for the HEMS Echonet Lite integration.

Supports the ECHONET Lite "electrically operated blind / sunshade" class
(0x0260) and "electrically operated shutter" class (0x0263). Other 0x026x
classes (rain sliding door 0x0261, curtain 0x0262, gate 0x0264, window 0x0265,
automatic entrance door 0x0266) are not currently represented in the MRA
data shipped with pyhems and are therefore not exposed by this platform.

The cover entity aggregates the open/close trigger (EPC 0xE0), the
degree-of-opening setting (EPC 0xE1), the blind angle setting (EPC 0xE2)
and the open/close status (EPC 0xEA) into a single Home Assistant
``CoverEntity``.

Slat / blind angle is exposed through Home Assistant's tilt API. ECHONET
Lite expresses the angle as 0-180 degrees while HA tilt is 0-100; the
two scales are bridged via ``round(deg * 100 / 180)`` and
``round(pos * 180 / 100)``.
"""

from __future__ import annotations

from typing import Any, Final

from pyhems import NodeState

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CLASS_CODE_ELECTRICALLY_OPERATED_BLIND,
    CLASS_CODE_ELECTRICALLY_OPERATED_SHUTTER,
    EPC_COVER_ANGLE,
    EPC_COVER_OPEN_CLOSE,
    EPC_COVER_OPEN_CLOSED_STATUS,
    EPC_COVER_POSITION,
)
from .coordinator import EchonetLiteCoordinator
from .entity import EchonetLiteEntity, setup_echonet_lite_device_platform
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1

# EPC 0xE0 raw command values.
_CMD_OPEN = 0x41
_CMD_CLOSE = 0x42
_CMD_STOP = 0x43

# EPC 0xEA (Open/closed status) raw values.
_STATUS_FULLY_OPEN = 0x41
_STATUS_FULLY_CLOSED = 0x42
_STATUS_OPENING = 0x43
_STATUS_CLOSING = 0x44
_STATUS_STOPPED = 0x45

_CLASS_CODE_TO_DEVICE_CLASS: Final[dict[int, CoverDeviceClass]] = {
    CLASS_CODE_ELECTRICALLY_OPERATED_BLIND: CoverDeviceClass.BLIND,
    CLASS_CODE_ELECTRICALLY_OPERATED_SHUTTER: CoverDeviceClass.SHUTTER,
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite cover entities from a config entry."""

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        if node.eoj.class_code not in _CLASS_CODE_TO_DEVICE_CLASS:
            return []
        return [EchonetLiteCover(coordinator, node)]

    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        entity_factory=_entity_factory,
    )


def _tilt_deg_to_ha(deg: int) -> int:
    """Convert ECHONET blind angle (0-180 deg) to HA tilt position (0-100)."""
    return max(0, min(100, round(deg * 100 / 180)))


def _tilt_ha_to_deg(pos: int) -> int:
    """Convert HA tilt position (0-100) to ECHONET blind angle (0-180 deg)."""
    return max(0, min(180, round(pos * 180 / 100)))


class EchonetLiteCover(EchonetLiteEntity, CoverEntity):
    """Representation of an ECHONET Lite electric blind/shutter cover."""

    _attr_name = None

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
    ) -> None:
        """Initialize the cover entity."""
        super().__init__(coordinator, node)
        class_code = node.eoj.class_code
        self._attr_unique_id = f"{node.device_key}-cover"
        self._attr_device_class = _CLASS_CODE_TO_DEVICE_CLASS[class_code]

        # Build supported_features dynamically from the device's advertised
        # property map so devices that omit optional EPCs (position, tilt)
        # don't expose unsupported controls in the UI.
        features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )
        if EPC_COVER_POSITION in node.set_epcs:
            features |= CoverEntityFeature.SET_POSITION
        if EPC_COVER_ANGLE in node.set_epcs:
            features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
        self._attr_supported_features = features

    def _raw(self, epc: int) -> int | None:
        """Return the single-byte raw value for ``epc`` if known."""
        if edt := self._node.properties.get(epc):
            return edt[0]
        return None

    @property
    def current_cover_position(self) -> int | None:
        """Return the current degree-of-opening (0 = closed, 100 = open).

        ECHONET Lite reports the value as a percentage in EPC 0xE1, which
        matches Home Assistant's convention directly.
        """
        return self._raw(EPC_COVER_POSITION)

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return the current tilt position (0-100) from EPC 0xE2 (0-180 deg)."""
        deg = self._raw(EPC_COVER_ANGLE)
        if deg is None:
            return None
        return _tilt_deg_to_ha(deg)

    @property
    def is_closed(self) -> bool | None:
        """Return True if the cover is fully closed.

        Prefers the explicit open/closed status (EPC 0xEA). If the device
        doesn't report it, fall back to the position percentage so
        positionable covers still report ``closed`` accurately.
        """
        status = self._raw(EPC_COVER_OPEN_CLOSED_STATUS)
        if status is not None:
            return status == _STATUS_FULLY_CLOSED
        # Fallback: use position percentage when 0xEA isn't supported.
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    @property
    def is_opening(self) -> bool | None:
        """Return True if the cover is currently moving towards open."""
        status = self._raw(EPC_COVER_OPEN_CLOSED_STATUS)
        if status is None:
            return None
        return status == _STATUS_OPENING

    @property
    def is_closing(self) -> bool | None:
        """Return True if the cover is currently moving towards closed."""
        status = self._raw(EPC_COVER_OPEN_CLOSED_STATUS)
        if status is None:
            return None
        return status == _STATUS_CLOSING

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Send the open command (EPC 0xE0 = 0x41)."""
        await self._async_send_property(EPC_COVER_OPEN_CLOSE, bytes([_CMD_OPEN]))

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Send the close command (EPC 0xE0 = 0x42)."""
        await self._async_send_property(EPC_COVER_OPEN_CLOSE, bytes([_CMD_CLOSE]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Send the stop command (EPC 0xE0 = 0x43)."""
        await self._async_send_property(EPC_COVER_OPEN_CLOSE, bytes([_CMD_STOP]))

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set the degree-of-opening as a 0-100 percentage (EPC 0xE1)."""
        position = int(kwargs[ATTR_POSITION])
        position = max(0, min(100, position))
        await self._async_send_property(EPC_COVER_POSITION, bytes([position]))

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the slats fully (HA tilt 100 -> 180 deg)."""
        await self._async_send_property(EPC_COVER_ANGLE, bytes([180]))

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the slats fully (HA tilt 0 -> 0 deg)."""
        await self._async_send_property(EPC_COVER_ANGLE, bytes([0]))

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the slat angle (EPC 0xE2 0-180 deg) from HA tilt 0-100."""
        deg = _tilt_ha_to_deg(int(kwargs[ATTR_TILT_POSITION]))
        await self._async_send_property(EPC_COVER_ANGLE, bytes([deg]))


__all__ = ["EchonetLiteCover"]
