"""Config flow: scan for a box (or type its address), check it answers, remember it."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.components import zeroconf
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
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
from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from .api import HearthAuthError, HearthClient, HearthError
from .const import CONF_EXTERNAL_TEMP_ENTITY, DOMAIN
from .coordinator import HearthConfigEntry

SERVICE_TYPE = "_hearth-ha._tcp.local."
SCAN_SECONDS = 3
CONF_DEVICE = "device"
MANUAL = "manual"


def title_for(device_name: str) -> str:
    """`hearth-a1b2c3` becomes `Hearth a1b2c3`."""
    prefix, sep, rest = device_name.partition("-")
    return f"{prefix.capitalize()} {rest}" if sep and rest else device_name


async def async_discover_hosts(hass: HomeAssistant) -> list[str]:
    """Browse the network for Hearth boxes for a few seconds and return their IPv4 addresses."""
    aiozc = await zeroconf.async_get_async_instance(hass)
    names: set[str] = set()

    def on_change(zeroconf: Any, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is not ServiceStateChange.Removed:
            names.add(name)

    browser = AsyncServiceBrowser(aiozc.zeroconf, SERVICE_TYPE, handlers=[on_change])
    try:
        await asyncio.sleep(SCAN_SECONDS)
    finally:
        await browser.async_cancel()

    hosts: list[str] = []
    for name in sorted(names):
        info = AsyncServiceInfo(SERVICE_TYPE, name)
        if await info.async_request(aiozc.zeroconf, 2000):
            hosts.extend(addr for addr in info.parsed_addresses(IPVersion.V4Only) if addr not in hosts)
    return hosts


class TillerwellConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Hearth box."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._device_name: str | None = None
        self._found: dict[str, str] | None = None

    async def _async_fetch_info(self, host: str) -> dict[str, Any]:
        return await HearthClient(async_get_clientsession(self.hass), host).get_info()

    async def _async_find_boxes(self) -> dict[str, str]:
        """Boxes on the network that are not set up yet: {host: device name}."""
        found: dict[str, str] = {}
        configured = {entry.unique_id for entry in self._async_current_entries()}
        for host in await async_discover_hosts(self.hass):
            try:
                info = await self._async_fetch_info(host)
            except HearthError:
                continue
            if info["device_name"] not in configured:
                found[host] = info["device_name"]
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
            SelectOptionDict(value=host, label=f"{title_for(name)} ({host})") for host, name in self._found.items()
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
                return await self._async_create_for(host, info["device_name"])
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str}),
            errors=errors,
        )

    async def _async_create_for(self, host: str, device_name: str) -> ConfigFlowResult:
        await self.async_set_unique_id(device_name)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return self.async_create_entry(title=title_for(device_name), data={CONF_HOST: host})

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """A box announced itself as _hearth-ha._tcp (Home Assistant switched on)."""
        host = discovery_info.host
        try:
            info = await self._async_fetch_info(host)
        except HearthAuthError:
            return self.async_abort(reason="web_password_set")
        except HearthError:
            return self.async_abort(reason="cannot_connect")
        self._host = host
        self._device_name = info["device_name"]
        await self.async_set_unique_id(self._device_name)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self.context["title_placeholders"] = {"name": title_for(self._device_name)}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm adding a discovered box."""
        assert self._host and self._device_name
        if user_input is not None:
            return self.async_create_entry(title=title_for(self._device_name), data={CONF_HOST: self._host})
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": title_for(self._device_name), "host": self._host},
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
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host})
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
