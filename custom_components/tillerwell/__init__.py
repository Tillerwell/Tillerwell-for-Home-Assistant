"""Tillerwell Hearth: local control of an Autoterm heater through a Hearth box over Wi-Fi."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HearthClient
from .coordinator import HearthConfigEntry, HearthCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: HearthConfigEntry) -> bool:
    """Set up a Hearth box from a config entry."""
    client = HearthClient(async_get_clientsession(hass), entry.data[CONF_HOST])
    coordinator = HearthCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HearthConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_entry_updated(hass: HomeAssistant, entry: HearthConfigEntry) -> None:
    """Reload only when the options change.

    A new address (the box came back on a new lease) is taken up in place and tried at once: a reload would put
    the entry through setup again while the box may still be starting, and a failed setup backs off for minutes.
    A new title (the box was renamed in the app) needs nothing.
    """
    coordinator = entry.runtime_data
    if dict(entry.options) != coordinator.setup_options:
        await hass.config_entries.async_reload(entry.entry_id)
    elif entry.data[CONF_HOST] != coordinator.client.host:
        coordinator.client.set_host(entry.data[CONF_HOST])
        await coordinator.async_request_refresh()
