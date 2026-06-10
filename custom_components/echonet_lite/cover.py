"""Cover platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass
from typing import Any

from pyhems import NodeState

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityDescription,
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
from .prop import EnumProp, NumericProp
from .runtime import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class EchonetLiteCoverEntityDescription(CoverEntityDescription):
    """Description for an ECHONET Lite cover entity."""

    open_close_prop: EnumProp
    position_prop: NumericProp
    angle_prop: NumericProp
    status_prop: EnumProp


def _create_cover_description(
    class_code: int,
    device_class: CoverDeviceClass,
) -> EchonetLiteCoverEntityDescription:
    """Build a cover description from pyhems definitions."""
    return EchonetLiteCoverEntityDescription(
        key="cover",
        device_class=device_class,
        open_close_prop=EnumProp.from_registry(class_code, EPC_COVER_OPEN_CLOSE),
        position_prop=NumericProp.from_registry(class_code, EPC_COVER_POSITION),
        angle_prop=NumericProp.from_registry(class_code, EPC_COVER_ANGLE),
        status_prop=EnumProp.from_registry(class_code, EPC_COVER_OPEN_CLOSED_STATUS),
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite cover entities from a config entry."""
    descriptions: dict[int, EchonetLiteCoverEntityDescription] = {
        CLASS_CODE_ELECTRICALLY_OPERATED_BLIND: _create_cover_description(
            CLASS_CODE_ELECTRICALLY_OPERATED_BLIND, CoverDeviceClass.BLIND
        ),
        CLASS_CODE_ELECTRICALLY_OPERATED_SHUTTER: _create_cover_description(
            CLASS_CODE_ELECTRICALLY_OPERATED_SHUTTER,
            CoverDeviceClass.SHUTTER,
        ),
    }

    @callback
    def _entity_factory(
        coordinator: EchonetLiteCoordinator, node: NodeState
    ) -> list[Entity]:
        if (description := descriptions.get(node.eoj.class_code)) is None:
            return []
        return [EchonetLiteCover(coordinator, node, description)]

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
    entity_description: EchonetLiteCoverEntityDescription

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteCoverEntityDescription,
    ) -> None:
        """Initialize the cover entity."""
        super().__init__(coordinator, node)
        self.entity_description = description
        self._attr_unique_id = f"{node.device_key}-{description.key}"

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

    @property
    def current_cover_position(self) -> int | None:
        """Return the current degree-of-opening (0 = closed, 100 = open).

        ECHONET Lite reports the value as a percentage in EPC 0xE1, which
        matches Home Assistant's convention directly.
        """
        return self.entity_description.position_prop.get(self._node)  # type: ignore[return-value]

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return the current tilt position (0-100) from EPC 0xE2 (0-180 deg)."""
        deg = self.entity_description.angle_prop.get(self._node)
        return None if deg is None else _tilt_deg_to_ha(int(deg))

    @property
    def is_closed(self) -> bool | None:
        """Return True if the cover is fully closed.

        Prefers the explicit open/closed status (EPC 0xEA). If the device
        doesn't report it, fall back to the position percentage so
        positionable covers still report ``closed`` accurately.
        """
        if (status := self.entity_description.status_prop.get(self._node)) is not None:
            return status == "fully_closed"
        # Fallback: use position percentage when 0xEA isn't supported.
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    @property
    def is_opening(self) -> bool | None:
        """Return True if the cover is currently moving towards open."""
        status = self.entity_description.status_prop.get(self._node)
        return None if status is None else status == "opening"

    @property
    def is_closing(self) -> bool | None:
        """Return True if the cover is currently moving towards closed."""
        status = self.entity_description.status_prop.get(self._node)
        return None if status is None else status == "closing"

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Send the open command (EPC 0xE0 = 0x41)."""
        await self._async_send_prop(self.entity_description.open_close_prop, "open")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Send the close command (EPC 0xE0 = 0x42)."""
        await self._async_send_prop(self.entity_description.open_close_prop, "close")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Send the stop command (EPC 0xE0 = 0x43)."""
        await self._async_send_prop(self.entity_description.open_close_prop, "stop")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set the degree-of-opening as a 0-100 percentage (EPC 0xE1)."""
        position = max(0, min(100, int(kwargs[ATTR_POSITION])))
        await self._async_send_prop(
            self.entity_description.position_prop, float(position)
        )

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the slats fully (HA tilt 100 -> 180 deg)."""
        await self._async_send_prop(self.entity_description.angle_prop, 180.0)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the slats fully (HA tilt 0 -> 0 deg)."""
        await self._async_send_prop(self.entity_description.angle_prop, 0.0)

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the slat angle (EPC 0xE2 0-180 deg) from HA tilt 0-100."""
        deg = _tilt_ha_to_deg(int(kwargs[ATTR_TILT_POSITION]))
        await self._async_send_prop(self.entity_description.angle_prop, float(deg))
