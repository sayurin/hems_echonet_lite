"""Select platform for the HEMS Echonet Lite integration."""

from dataclasses import dataclass

from pyhems import (
    INSTALLATION_LOCATIONS,
    EntityDefinition,
    InstallationLocation,
    InstallationLocationCodec,
    NodeState,
    decode_installation_location,
)

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    EPC_INSTALLATION_LOCATION,
    INSTALLATION_LOCATION_NUMBER_OPTIONS,
    INSTALLATION_LOCATION_UNSET,
    infer_entity_category,
    infer_entity_registry_enabled_default,
)
from .coordinator import EchonetLiteCoordinator
from .entity import (
    EchonetLiteDescribedEntity,
    EchonetLiteEntity,
    EchonetLiteEntityDescription,
    EnumProp,
    setup_echonet_lite_device_platform,
    setup_echonet_lite_platform,
)
from .types import EchonetLiteConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class EchonetLiteSelectEntityDescription(
    SelectEntityDescription, EchonetLiteEntityDescription
):
    """Entity description that stores EPC metadata and value mapping."""

    prop: EnumProp


def _create_select_description(
    class_code: int,
    entity_def: EntityDefinition,
) -> EchonetLiteSelectEntityDescription:
    """Create a select entity description from an EntityDefinition.

    All select entities in definitions.json are validated to have enum_values,
    so this function always returns a valid description.
    """
    prop = EnumProp.from_entity_def(entity_def)

    if (
        not prop.options
    ):  # pragma: no cover - validated upstream in pyhems._validate_entity
        raise ValueError(
            f"Select entity EPC 0x{entity_def.epc:02X} for class 0x{class_code:04X} "
            "has no valid enum values - this should be caught during generation"
        )

    return EchonetLiteSelectEntityDescription(
        key=f"{entity_def.epc:02x}",
        translation_key=entity_def.id,
        class_code=class_code,
        epc=entity_def.epc,
        entity_category=infer_entity_category(entity_def),
        entity_registry_enabled_default=infer_entity_registry_enabled_default(
            entity_def
        ),
        prop=prop,
        manufacturer_code=entity_def.manufacturer_code,
    )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EchonetLiteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ECHONET Lite select entities from a config entry."""
    setup_echonet_lite_platform(
        entry,
        async_add_entities,
        "select",
        _create_select_description,
        EchonetLiteSelect,
        "select",
    )
    setup_echonet_lite_device_platform(
        entry,
        async_add_entities,
        entity_factory=_build_installation_location_entities,
    )


class EchonetLiteSelect(
    EchonetLiteDescribedEntity[EchonetLiteSelectEntityDescription], SelectEntity
):
    """Representation of a writable ECHONET Lite select property."""

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
        description: EchonetLiteSelectEntityDescription,
    ) -> None:
        """Initialize the ECHONET Lite select entity."""
        super().__init__(coordinator, node, description)
        self._attr_options = description.prop.options

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option or None if unset.

        The raw property value is decoded and mapped to the option name.
        """
        return self.description.prop.get(self._node)

    async def async_select_option(self, option: str) -> None:
        """Select the given option by sending the corresponding payload."""
        try:
            await self._async_send_prop(self.description.prop, option)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_value",
                translation_placeholders={"value": option},
            ) from err


# ============================================================================
# Installation Location (EPC 0x81) — hardcoded, not definition-driven
# ============================================================================
# EPC 0x81 is a 1-byte mandatory super-class property shared by all ECHONET
# Lite devices. The spec-defined LLLL codes and English labels live in
# :mod:`pyhems.installation_location`; the integration adds the
# ``unset`` option (byte 0x00) to expose "clear location" in the UI.
# Two select entities expose the location code and number independently;
# writes merge the changed field with the current value of the other field.

# Mapping from LLLL code (0..15) to the integration's translation key.
# 0 → ``unset`` is integration-specific; 1..15 come from the ECHONET Lite spec.
_LOCATION_CODE_TO_KEY: dict[int, str] = {0: INSTALLATION_LOCATION_UNSET} | {
    code: key for code, (key, _name, _name_ja) in INSTALLATION_LOCATIONS.items()
}
_LOCATION_KEY_TO_CODE: dict[str, int] = {
    key: code for code, key in _LOCATION_CODE_TO_KEY.items()
}


def _decode_location_fields(node: NodeState) -> tuple[int, int] | None:
    """Return ``(llll, nnn)`` for the node's current 0x81 byte, or ``None``.

    ``None`` matches the cases :func:`decode_installation_location` rejects
    (unset, indefinite, position-info, free-format, unknown code). The unset
    case is still surfaced as the ``unset`` option via the explicit 0x00
    byte check in :meth:`InstallationLocationCodeSelect.current_option`.
    """
    loc = decode_installation_location(node.properties.get(EPC_INSTALLATION_LOCATION))
    if loc is None:
        return None
    return loc.code, loc.instance


def _build_installation_location_entities(
    coordinator: EchonetLiteCoordinator,
    node: NodeState,
) -> list[Entity]:
    """Return the two Installation Location select entities for a node.

    Returns an empty list when the node does not expose EPC 0x81.
    """
    if EPC_INSTALLATION_LOCATION not in node.get_epcs:
        return []
    return [
        InstallationLocationCodeSelect(coordinator, node),
        InstallationLocationNumberSelect(coordinator, node),
    ]


class InstallationLocationCodeSelect(EchonetLiteEntity, SelectEntity):
    """Select entity for the Installation Location code (LLLL, bits 6-3)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_translation_key = "installation_location_code"
    _attr_options = list(_LOCATION_CODE_TO_KEY.values())

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, node)
        self._attr_unique_id = f"{node.device_key}_81_code"

    @property
    def current_option(self) -> str | None:
        """Return the currently selected location code option."""
        raw = self._node.properties.get(EPC_INSTALLATION_LOCATION)
        if raw == b"\x00":
            return INSTALLATION_LOCATION_UNSET
        fields = _decode_location_fields(self._node)
        if fields is None:
            return None
        llll, _ = fields
        return _LOCATION_CODE_TO_KEY.get(llll)

    async def async_select_option(self, option: str) -> None:
        """Send updated location code while preserving the current NNN."""
        new_llll = _LOCATION_KEY_TO_CODE[option]
        if new_llll == 0:
            # "unset" selected — write 0x00; forcing NNN=0 avoids generating
            # the prohibited 0x01-0x07 range (17-byte format indicators).
            await self._async_send_property(EPC_INSTALLATION_LOCATION, b"\x00")
            return
        fields = _decode_location_fields(self._node)
        nnn = fields[1] if fields is not None else 0
        edt = InstallationLocationCodec().encode(
            InstallationLocation.from_code(new_llll, nnn)
        )
        await self._async_send_property(EPC_INSTALLATION_LOCATION, edt)


class InstallationLocationNumberSelect(EchonetLiteEntity, SelectEntity):
    """Select entity for the Installation Location number (NNN, bits 2-0)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_translation_key = "installation_location_number"
    _attr_options = list(INSTALLATION_LOCATION_NUMBER_OPTIONS)

    def __init__(
        self,
        coordinator: EchonetLiteCoordinator,
        node: NodeState,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, node)
        self._attr_unique_id = f"{node.device_key}_81_number"

    @property
    def available(self) -> bool:
        """Return True only when a location code (LLLL≠0) is set.

        Writing the number when LLLL=0 would produce byte 0x01-0x07 which are
        the 17-byte format indicator values — disallow until a code is chosen.
        """
        if not super().available:
            return False
        fields = _decode_location_fields(self._node)
        return fields is not None and fields[0] != 0

    @property
    def current_option(self) -> str | None:
        """Return the current location number as a string."""
        fields = _decode_location_fields(self._node)
        if fields is None or fields[0] == 0:
            return None
        _, nnn = fields
        return str(nnn)

    async def async_select_option(self, option: str) -> None:
        """Send updated location number while preserving the current LLLL."""
        fields = _decode_location_fields(self._node)
        if fields is None or fields[0] == 0:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="installation_location_number_unset",
            )
        llll = fields[0]
        new_nnn = int(option)
        edt = InstallationLocationCodec().encode(
            InstallationLocation.from_code(llll, new_nnn)
        )
        await self._async_send_property(EPC_INSTALLATION_LOCATION, edt)
