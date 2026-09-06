"""Config flow for the HEMS Echonet Lite integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

from pyhems import create_multicast_socket
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import network
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_INTERFACE, DEFAULT_INTERFACE, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EchonetLiteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ECHONET Lite.

    ConfigFlow handles network interface selection only.
    """

    VERSION = 1
    MINOR_VERSION = 1

    @override
    async def async_step_user(
        self, user_input: Mapping[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step (UI setup)."""
        return await self._async_handle_interface_step("user", user_input)

    async def async_step_reconfigure(
        self, user_input: Mapping[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of the network interface."""
        return await self._async_handle_interface_step("reconfigure", user_input)

    async def _async_handle_interface_step(
        self, step_id: str, user_input: Mapping[str, Any] | None
    ) -> config_entries.ConfigFlowResult:
        """Handle interface selection for both user and reconfigure steps."""
        entry = self._get_reconfigure_entry() if step_id == "reconfigure" else None
        current_interface = (
            entry.data.get(CONF_INTERFACE, DEFAULT_INTERFACE)
            if entry
            else DEFAULT_INTERFACE
        )

        interface_options = await _async_get_interface_options(self.hass)
        # Ensure the currently configured interface is always present in the
        # dropdown so reconfigure cannot lose or invalidate the existing value.
        if not any(opt["value"] == current_interface for opt in interface_options):
            interface_options.append(
                SelectOptionDict(
                    value=current_interface,
                    label=f"Configured ({current_interface})",
                )
            )
        errors: dict[str, str] = {}

        if user_input is not None:
            # ``SelectSelector(mode=DROPDOWN)`` already restricts ``interface``
            # to one of ``interface_options``, so no further validation of the
            # value itself is needed; we only verify the multicast socket can
            # be opened on the selected interface.
            interface = user_input.get(CONF_INTERFACE, DEFAULT_INTERFACE)

            if error := await self._async_test_multicast(interface):
                errors["base"] = error
            else:
                return self._async_finish_interface_step(entry, interface)

        schema = vol.Schema(
            {
                vol.Optional(CONF_INTERFACE, default=current_interface): (
                    SelectSelector(
                        SelectSelectorConfig(
                            options=interface_options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                ),
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    def _async_finish_interface_step(
        self,
        entry: config_entries.ConfigEntry | None,
        interface: str,
    ) -> config_entries.ConfigFlowResult:
        """Finish interface step with create or update."""
        if entry is None:
            return self.async_create_entry(
                title="HEMS", data={CONF_INTERFACE: interface}
            )
        # Update interface in data; preserve existing options
        return self.async_update_reload_and_abort(
            entry, data={CONF_INTERFACE: interface}
        )

    async def _async_test_multicast(self, interface: str) -> str | None:
        """Test multicast socket can be created. Returns error key or None."""
        try:
            protocol = await create_multicast_socket(interface, lambda *_: None)
        except OSError:
            return "cannot_connect"
        try:
            return None
        finally:
            protocol.close()


async def _async_get_interface_options(hass: HomeAssistant) -> list[SelectOptionDict]:
    """Build interface select options from network adapters."""
    options: list[SelectOptionDict] = [
        {"value": DEFAULT_INTERFACE, "label": f"Auto ({DEFAULT_INTERFACE})"}
    ]

    try:
        adapters = await network.async_get_adapters(hass)
        for adapter in adapters:
            if not adapter["enabled"]:
                continue
            name = adapter.get("name", "unknown")
            options.extend(
                {"value": address, "label": f"{name} ({address})"}
                for ipv4 in adapter.get("ipv4", [])
                if (address := ipv4.get("address")) and address != "127.0.0.1"
            )
    except OSError:
        _LOGGER.debug("Failed to enumerate network adapters")

    return options
