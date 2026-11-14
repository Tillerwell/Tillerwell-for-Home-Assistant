"""Polling coordinator for one Hearth box."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .api import HearthClient, HearthConnectionError, HearthError, HearthRequestError
from .const import (
    CLOCK_SYNC_RETRY,
    CONF_EXTERNAL_TEMP_ENTITY,
    DOMAIN,
    EXTERNAL_TEMP_REFRESH,
    EXTERNAL_TEMP_SILENT,
    FUEL_DAY_FIRMWARE,
    FUEL_DAY_INTERVAL,
    HEATER_CARE_FIELD,
    INFO_INTERVAL,
    MANUFACTURER,
    MODEL,
    REDISCOVER_INTERVAL,
    SCAN_INTERVAL,
    SOURCE,
    STATE_IDLE,
    TIMERS_INTERVAL,
)
from .discovery import async_discover_hosts, box_name, title_for
from .reasons import (
    ACTION_PREHEAT,
    ACTION_PUMP,
    ACTION_THERMOSTAT_ON,
    RESULT_DONE,
    RESULT_FAILED,
    RESULT_REFUSED,
    RESULT_WAITING,
    explain_preheat,
    explain_pump,
    explain_refusal,
    explain_stop,
    explain_thermostat_on,
)

_LOGGER = logging.getLogger(__name__)

type HearthConfigEntry = ConfigEntry[HearthCoordinator]


def roamos_number(version: Any) -> int | None:
    """RoamOS "1.0064" as 10064, None for anything else."""
    if not isinstance(version, str) or len(version) != 6 or version[1] != ".":
        return None
    if not version[0].isdigit() or not version[2:].isdigit():
        return None
    return int(version[0]) * 10000 + int(version[2:])


@dataclass
class HearthData:
    """Everything the entities read."""

    status: dict[str, Any]
    info: dict[str, Any]
    timers: list[dict[str, Any]]
    # Today's fuel by power level (Hearth 1.64), None on older firmware or until the box has one.
    fuel_day: dict[str, Any] | None = None


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
        self._fuel_day: dict[str, Any] | None = None
        self._fuel_day_at = 0.0
        self._clock_try_at = 0.0
        self._ext_sent_at = 0.0
        self._ext_sent_value: float | None = None
        self._ext_silent_logged = False
        self._rediscover_at = float("-inf")
        self._name: str | None = None
        # Options the entry was set up with: only a change to these needs a reload.
        self.setup_options = dict(entry.options)
        # The last start or stop asked for from Home Assistant, and why it did or did not happen (reasons.py).
        self.last_action: dict[str, Any] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """The device every entity of this box hangs off."""
        return DeviceInfo(
            identifiers={self._device_id},
            name=box_name(self._info) if self._info else self.config_entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=self._info.get("version"),
            configuration_url=self.client.host_url,
        )

    @property
    def _device_id(self) -> tuple[str, str]:
        return (DOMAIN, self.config_entry.unique_id or self.config_entry.entry_id)

    async def _async_update_data(self) -> HearthData:
        try:
            status = await self.client.get_status()
        except HearthConnectionError as err:
            if not await self._async_find_moved_box():
                raise UpdateFailed(str(err)) from err
            try:
                status = await self.client.get_status()
            except HearthError as err:
                raise UpdateFailed(str(err)) from err
        except HearthError as err:
            raise UpdateFailed(str(err)) from err

        now = monotonic()
        if not self._info or now - self._info_at >= INFO_INTERVAL.total_seconds():
            await self._refresh_info(now)
        if not self._timers_at or now - self._timers_at >= TIMERS_INTERVAL.total_seconds():
            await self._refresh_timers(now)
        if not self._fuel_day_at or now - self._fuel_day_at >= FUEL_DAY_INTERVAL.total_seconds():
            await self._refresh_fuel_day(now)

        # Best effort: neither may fail the update.
        await self._sync_clock(status, now)
        await self._push_external_temperature(now)

        return HearthData(status=status, info=self._info, timers=self._timers, fuel_day=self._fuel_day)

    async def _refresh_info(self, now: float) -> None:
        try:
            self._info = await self.client.get_info()
        except HearthError as err:
            if not self._info:
                raise UpdateFailed(str(err)) from err
            _LOGGER.debug("Could not refresh info: %s", err)
        else:
            self._info_at = now
            self._apply_name()

    async def _async_find_moved_box(self) -> bool:
        """Look for this box at another address; True if it is there and the client now points at it.

        A restart can bring the box back on a new DHCP lease, or under a new .local name after the owner renamed
        it, while the app (which browses mDNS) finds it at once. Waiting for Home Assistant's own discovery to
        notice can take minutes.
        """
        now = monotonic()
        if now - self._rediscover_at < REDISCOVER_INTERVAL.total_seconds():
            return False
        self._rediscover_at = now
        session = async_get_clientsession(self.hass)
        for host in await async_discover_hosts(self.hass):
            if host == self.client.host:
                continue
            try:
                info = await HearthClient(session, host).get_info()
            except HearthError:
                continue
            if info["device_name"] == self.config_entry.unique_id:
                _LOGGER.info("%s has moved from %s to %s", self.config_entry.title, self.client.host, host)
                self.client.set_host(host)
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data={**self.config_entry.data, CONF_HOST: host}
                )
                return True
        return False

    def _apply_name(self) -> None:
        """Follow the name the owner gives the box in the app, unless they renamed it in Home Assistant."""
        name = box_name(self._info)
        if name == self._name:
            return
        previous, self._name = self._name, name
        entry = self.config_entry
        if entry.title != name and entry.title in (previous, title_for(self._info["device_name"])):
            self.hass.config_entries.async_update_entry(entry, title=name)
        # Sets the device's own name; a name the user typed in Home Assistant (name_by_user) still wins.
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier(self._device_id, entry.entry_id)
        if device and device.name != name:
            registry.async_update_device(device.id, name=name)

    async def _refresh_fuel_day(self, now: float) -> None:
        self._fuel_day_at = now
        if (roamos_number(self._info.get("version")) or 0) < FUEL_DAY_FIRMWARE:
            self._fuel_day = None
            return
        try:
            self._fuel_day = await self.client.get_fuel_day(dt_util.now().date().isoformat())
        except HearthError as err:
            _LOGGER.debug("Could not read today's fuel by power level: %s", err)

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
        # last_reported moves on every report, even one that repeats the value (HA 2024.3+).
        reported = getattr(state, "last_reported", None) or state.last_updated
        silent = datetime.now(UTC) - reported
        if silent > EXTERNAL_TEMP_SILENT:
            if not self._ext_silent_logged:
                _LOGGER.warning(
                    "%s has not reported for %d minutes; no longer sending it to %s, which will use the controller's "
                    "own reading until it reports again",
                    entity_id,
                    silent.total_seconds() // 60,
                    self.config_entry.title,
                )
                self._ext_silent_logged = True
            return None
        if self._ext_silent_logged:
            _LOGGER.info("%s is reporting again; sending it to %s", entity_id, self.config_entry.title)
            self._ext_silent_logged = False
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
        except HearthRequestError as err:
            # the box said no and why (Heater Care, firmware 1.61: a stop inside the minimum run or a start inside
            # the rest after a run). Home Assistant can never override that; only the Tillerwell app can.
            raise HomeAssistantError(str(err)) from err
        except HearthError as err:
            raise HomeAssistantError(f"Could not reach {self.config_entry.title}: {err}") from err
        if not accepted:
            raise HomeAssistantError(
                f"{self.config_entry.title} did not accept '{cmd}'. Bypass may be on, or the command queue may be full."
            )
        await self.async_request_refresh()

    async def async_act(self, action: str, run: Callable[[], Awaitable[Any]], **context: Any) -> None:
        """Run a start or stop and record why it did, or did not, happen for the Last start or stop sensor.

        `run` is the command sequence; it raises HomeAssistantError when the box refuses or cannot be reached, which
        is recorded and raised again so the user still sees the error. The stop returns True when the box is letting
        a run finish its minimum run first.
        """
        before = dict(self.data.status) if self.data else {}
        try:
            outcome = await run()
        except HomeAssistantError as err:
            cause = err.__cause__
            if isinstance(cause, HearthRequestError):
                self._record(action, RESULT_REFUSED, explain_refusal(action, str(cause), cause.care), cause.care)
            else:
                self._record(action, RESULT_FAILED, str(err), None)
            raise
        await self.async_refresh()
        after = self.data.status if self.data else {}
        if action == ACTION_PREHEAT:
            reason = explain_preheat(after, int(context.get("minutes") or 30))
        elif action == ACTION_THERMOSTAT_ON:
            reason = explain_thermostat_on(after)
        elif action == ACTION_PUMP:
            reason = explain_pump(after)
        else:
            reason = explain_stop(before, after, outcome is True)
        self._record(action, RESULT_WAITING if outcome is True else RESULT_DONE, reason, None)

    def _record(self, action: str, result: str, reason: str, care: str | None) -> None:
        self.last_action = {
            "action": action,
            "result": result,
            "reason": reason,
            "care": care,
            "at": datetime.now(UTC),
        }
        _LOGGER.debug("%s %s: %s", action, result, reason)
        self.async_update_listeners()

    async def async_stop(self) -> bool:
        """Stop the heater. Auto must be switched off first or the box starts it again.

        Heater Care (firmware 1.61) does not let Home Assistant stop a run inside its minimum run. Thermostat off is
        still accepted: Auto goes off and the box stops the run once its minimum run is up, so that is not an error.
        Any other stop the box refuses (a preheat) raises, with the box's reason. Only the Tillerwell app can override.
        Returns True when the box is letting the run finish its minimum run before it stops.
        """
        status = self.data.status if self.data else {}
        care = HEATER_CARE_FIELD in status
        auto_was_on = bool(status.get("auto_enabled"))
        if auto_was_on:
            if care:
                await self.async_command("auto", enabled=False, source=SOURCE)
            else:
                await self.async_command("auto", enabled=False)
        if status.get("state") not in (None, STATE_IDLE):
            if not care:
                await self.async_command("stop")
                return False
            waiting = False
            try:
                await self.client.command("stop", source=SOURCE)
            except HearthRequestError as err:
                if not (auto_was_on and err.care == "min_run"):
                    raise HomeAssistantError(str(err)) from err
                waiting = True
            except HearthError as err:
                raise HomeAssistantError(f"Could not reach {self.config_entry.title}: {err}") from err
            await self.async_request_refresh()
            return waiting
        return False

    async def async_set_timers(self, timers: list[dict[str, Any]]) -> None:
        """Replace the box's timer list. The box checks every rule and says why if it refuses."""
        try:
            await self.client.put_timers(timers)
        except HearthError as err:
            raise HomeAssistantError(str(err)) from err
        self._timers_at = 0.0
        await self.async_request_refresh()
