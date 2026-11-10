"""Config flow: scan for a box (or type its address), check it answers, remember it."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import HearthAuthError, HearthClient, HearthError
from .const import CONF_EXTERNAL_TEMP_ENTITY, DOMAIN
from .coordinator import HearthConfigEntry
from .discovery import async_discover_hosts, box_name

CONF_DEVICE = "device"
MANUAL = "manual"


class TillerwellConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Hearth box."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._info: dict[str, Any] | None = None
        self._found: dict[str, dict[str, Any]] | None = None

    async def _async_fetch_info(self, host: str) -> dict[str, Any]:
        return await HearthClient(async_get_clientsession(self.hass), host).get_info()

    async def _async_find_boxes(self) -> dict[str, dict[str, Any]]:
        """Boxes on the network that are not set up yet: {host: /api/info}."""
        found: dict[str, dict[str, Any]] = {}
        configured = {entry.unique_id for entry in self._async_current_entries()}
        for host in await async_discover_hosts(self.hass):
            try:
                info = await self._async_fetch_info(host)
            except HearthError:
                continue
            if info["device_name"] not in configured:
                found[host] = info
        return found

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Look for boxes on the network and offer them; fall back to typing an address."""
        if self._found is None:
            self._found = await self._async_find_boxes()
        if user_input is not None:
            choice = user_input[CONF_DEVICE]
            if choice != MANUAL and choice in self._found:
                return await self._async_create_for(choice, self._found[choice])
            return await self.async_step_manual()
        if not self._found:
            return await self.async_step_manual()
        options = [
            SelectOptionDict(value=host, label=f"{box_name(info)} ({host})") for host, info in self._found.items()
        ]
        options.append(SelectOptionDict(value=MANUAL, label="Enter an address instead"))
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE, default=options[0]["value"]): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST, translation_key="device")
                    )
                }
            ),
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Type the box's address (its name ends in .local, or use its IP)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                info = await self._async_fetch_info(host)
            except HearthAuthError:
                errors["base"] = "web_password_set"
            except HearthError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_create_for(host, info)
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str}),
            errors=errors,
        )

    async def _async_create_for(self, host: str, info: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(info["device_name"])
        self._abort_if_unique_id_configured(updates={CONF_HOST: host}, reload_on_update=False)
        return self.async_create_entry(title=box_name(info), data={CONF_HOST: host})

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """A box announced itself as _hearth-ha._tcp (Home Assistant switched on)."""
        # Prefer an IPv4 address: the box's HTTP server listens on IPv4.
        addresses = [ip_address(str(ip)) for ip in discovery_info.ip_addresses]
        host = next((str(ip) for ip in addresses if ip.version == 4 and not ip.is_link_local), discovery_info.host)
        try:
            info = await self._async_fetch_info(host)
        except HearthAuthError:
            return self.async_abort(reason="web_password_set")
        except HearthError:
            return self.async_abort(reason="cannot_connect")
        self._host = host
        self._info = info
        await self.async_set_unique_id(info["device_name"])
        # A new address is taken up without a reload (see __init__.py); a box whose setup is waiting to retry is
        # reloaded by Home Assistant now that it has been seen.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host}, reload_on_update=False)
        self.context["title_placeholders"] = {"name": box_name(info)}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm adding a discovered box."""
        assert self._host and self._info
        if user_input is not None:
            return self.async_create_entry(title=box_name(self._info), data={CONF_HOST: self._host})
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": box_name(self._info), "host": self._host},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the address of an existing box."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                info = await self._async_fetch_info(host)
            except HearthAuthError:
                errors["base"] = "web_password_set"
            except HearthError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info["device_name"])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_and_abort(entry, data_updates={CONF_HOST: host})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: HearthConfigEntry) -> TillerwellOptionsFlow:
        """Options: which Home Assistant sensor feeds the box's thermostat."""
        return TillerwellOptionsFlow()


class TillerwellOptionsFlow(OptionsFlow):
    """Pick a room temperature sensor for the box's thermostat to use."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the external temperature sensor, or none."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Optional(CONF_EXTERNAL_TEMP_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain=["sensor", "input_number"])
                )
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
        )
