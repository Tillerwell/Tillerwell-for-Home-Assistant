"""Config flow: find a box (or type its address), check it answers, remember it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import HearthAuthError, HearthClient, HearthError
from .const import CONF_EXTERNAL_TEMP_ENTITY, CONF_PASSWORD, DOMAIN
from .coordinator import HearthConfigEntry

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))


def title_for(device_name: str) -> str:
    """`hearth-a1b2c3` becomes `Hearth a1b2c3`."""
    prefix, sep, rest = device_name.partition("-")
    return f"{prefix.capitalize()} {rest}" if sep and rest else device_name


class TillerwellConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Hearth box."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._device_name: str | None = None
        self._needs_password = False

    async def _async_fetch_info(self, host: str, password: str | None) -> dict[str, Any]:
        client = HearthClient(async_get_clientsession(self.hass), host, password or None)
        return await client.get_info()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Type the box's address (its name ends in .local, or use its IP)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            password = user_input.get(CONF_PASSWORD) or None
            try:
                info = await self._async_fetch_info(host, password)
            except HearthAuthError:
                errors["base"] = "invalid_auth"
            except HearthError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info["device_name"])
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=title_for(info["device_name"]),
                    data={CONF_HOST: host, CONF_PASSWORD: password},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                    vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """A box announced itself as _hearth._tcp."""
        host = discovery_info.host
        try:
            info = await self._async_fetch_info(host, None)
        except HearthAuthError:
            # The box has a web password: it is a Hearth box, but we cannot read its name yet.
            return self.async_abort(reason="auth_required_use_manual")
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
            return self.async_create_entry(
                title=title_for(self._device_name),
                data={CONF_HOST: self._host, CONF_PASSWORD: None},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": title_for(self._device_name), "host": self._host},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The box's web password changed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the new web password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_fetch_info(entry.data[CONF_HOST], user_input[CONF_PASSWORD])
            except HearthAuthError:
                errors["base"] = "invalid_auth"
            except HearthError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the address or password of an existing box."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            password = user_input.get(CONF_PASSWORD) or None
            try:
                info = await self._async_fetch_info(host, password)
            except HearthAuthError:
                errors["base"] = "invalid_auth"
            except HearthError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info["device_name"])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host, CONF_PASSWORD: password}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                    vol.Optional(CONF_PASSWORD, default=entry.data.get(CONF_PASSWORD) or ""): PASSWORD_SELECTOR,
                }
            ),
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
