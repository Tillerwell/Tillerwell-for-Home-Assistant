"""Polling coordinator for one Hearth box."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.unit_conversion import TemperatureConverter

from .api import HearthClient, HearthError
from .const import (
    CLOCK_SYNC_RETRY,
    CONF_EXTERNAL_TEMP_ENTITY,
    DOMAIN,
    EXTERNAL_TEMP_REFRESH,
    INFO_INTERVAL,
    MANUFACTURER,
    MODEL,
    SCAN_INTERVAL,
    STATE_IDLE,
    TIMERS_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type HearthConfigEntry = ConfigEntry[HearthCoordinator]


@dataclass
class HearthData:
    """Everything the entities read."""

    status: dict[str, Any]
    info: dict[str, Any]
    timers: list[dict[str, Any]]


class HearthCoordinator(DataUpdateCoordinator[HearthData]):
    """Reads /api/status every few seconds, and /api/info and /api/timers more slowly."""

    config_entry: HearthConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HearthConfigEntry, client: HearthClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self._info: dict[str, Any] = {}
        self._timers: list[dict[str, Any]] = []
        self._info_at = 0.0
        self._timers_at = 0.0
        self._clock_try_at = 0.0
        self._ext_sent_at = 0.0
        self._ext_sent_value: float | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """The device every entity of this box hangs off."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.unique_id or self.config_entry.entry_id)},
            name=self.config_entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=self._info.get("version"),
            configuration_url=self.client.host_url,
        )

    async def _async_update_data(self) -> HearthData:
        try:
            status = await self.client.get_status()
        except HearthError as err:
            raise UpdateFailed(str(err)) from err

        now = monotonic()
        if not self._info or now - self._info_at >= INFO_INTERVAL.total_seconds():
            await self._refresh_info(now)
        if not self._timers_at or now - self._timers_at >= TIMERS_INTERVAL.total_seconds():
            await self._refresh_timers(now)

        # Best effort: neither may fail the update.
        await self._sync_clock(status, now)
        await self._push_external_temperature(now)

        return HearthData(status=status, info=self._info, timers=self._timers)

    async def _refresh_info(self, now: float) -> None:
        try:
            self._info = await self.client.get_info()
        except HearthError as err:
            if not self._info:
                raise UpdateFailed(str(err)) from err
            _LOGGER.debug("Could not refresh info: %s", err)
        else:
            self._info_at = now

    async def _refresh_timers(self, now: float) -> None:
        try:
            self._timers = await self.client.get_timers()
        except HearthError as err:
            _LOGGER.debug("Could not refresh timers: %s", err)
        else:
            self._timers_at = now

    async def _sync_clock(self, status: dict[str, Any], now: float) -> None:
        """Give a box with no internet the time and zone so its timers can run.

        The box has no battery clock, and a boat may have no network time. Home Assistant
        knows both, so offer them whenever the box says its clock is not trusted. The box
        refuses the time once it has network time, so this never overrides NTP.
        """
        if status.get("clock_ok") is not False or now - self._clock_try_at < CLOCK_SYNC_RETRY.total_seconds():
            return
        self._clock_try_at = now
        try:
            if not status.get("timezone"):
                await self.client.command("timezone", value=self.hass.config.time_zone)
            await self.client.command("set_time", epoch=int(datetime.now(UTC).timestamp()))
        except HearthError as err:
            _LOGGER.debug("Could not set the box's clock: %s", err)

    async def _push_external_temperature(self, now: float) -> None:
        """Feed a Home Assistant temperature sensor to the box's thermostat, if one is chosen."""
        entity_id = self.config_entry.options.get(CONF_EXTERNAL_TEMP_ENTITY)
        if not entity_id:
            return
        value = self._read_celsius(entity_id)
        if value is None:
            return
        due = now - self._ext_sent_at >= EXTERNAL_TEMP_REFRESH.total_seconds()
        if value == self._ext_sent_value and not due:
            return
        try:
            if await self.client.command("external_temp", value=value):
                self._ext_sent_at = now
                self._ext_sent_value = value
        except HearthError as err:
            _LOGGER.debug("Could not send the external temperature: %s", err)

    def _read_celsius(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        unit = state.attributes.get("unit_of_measurement")
        if unit in (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN):
            value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        return round(value, 1)

    async def async_command(self, cmd: str, **params: Any) -> None:
        """Send a command, tell the user if the box refused it, and refresh."""
        try:
            accepted = await self.client.command(cmd, **params)
        except HearthError as err:
            raise HomeAssistantError(f"Could not reach {self.config_entry.title}: {err}") from err
        if not accepted:
            raise HomeAssistantError(
                f"{self.config_entry.title} did not accept '{cmd}'. Bypass may be on, or the command queue may be full."
            )
        await self.async_request_refresh()

    async def async_stop(self) -> None:
        """Stop the heater. Auto must be switched off first or the box starts it again."""
        status = self.data.status if self.data else {}
        if status.get("auto_enabled"):
            await self.async_command("auto", enabled=False)
        if status.get("state") not in (None, STATE_IDLE):
            await self.async_command("stop")

    async def async_set_timers(self, timers: list[dict[str, Any]]) -> None:
        """Replace the box's timer list. The box checks every rule and says why if it refuses."""
        try:
            await self.client.put_timers(timers)
        except HearthError as err:
            raise HomeAssistantError(str(err)) from err
        self._timers_at = 0.0
        await self.async_request_refresh()
