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
    ACTIVE_STATES,
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
    MODEL_DIAL,
    REDISCOVER_INTERVAL,
    RUN_ENDS,
    RUN_MODE_FIRMWARE,
    RUN_PHASES,
    RUN_SOURCES,
    RUNS_FIRMWARE,
    RUNS_INTERVAL,
    SCAN_INTERVAL,
    SOURCE,
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
    # Hearth 1.82: the newest run, and the newest with the heater's own readings (run history), as parse_run gives them.
    last_run: dict[str, Any] | None = None
    last_health_run: dict[str, Any] | None = None
    # Hearth 1.84: GET /api/health (each part, use since the last service, when it last ran).
    health: dict[str, Any] | None = None
    # Hearth 1.85: GET /api/service (each item's state, the log).
    service: dict[str, Any] | None = None


def parse_run(row: Any) -> dict[str, Any] | None:
    """One run row of GET /api/runs (Hearth 1.82, spec §9.15) as named values, None if it is not one."""
    if not isinstance(row, list) or len(row) < 24 or not isinstance(row[23], str):
        return None
    v = row[:23]
    if any(x is not None and not isinstance(x, int | float) for x in v):
        return None
    flags = int(v[7] or 0)
    phases: dict[str, int] = {}
    segs = []
    for part in row[23].split(","):
        p, _, t = part.partition(":")
        if p.isdigit() and t.isdigit() and 1 <= int(p) <= len(RUN_PHASES):
            segs.append((RUN_PHASES[int(p) - 1], int(t)))
    dur = int(v[1] or 0)
    for i, (phase, at) in enumerate(segs):
        end = segs[i + 1][1] if i + 1 < len(segs) else dur
        if end > at:
            phases[phase] = phases.get(phase, 0) + end - at

    def tenth(x: Any) -> float | None:
        return None if x is None else round(x / 10, 1)

    def pos(x: Any) -> Any:
        return x if x else None

    return {
        "start": int(v[0]) if flags & 4 and v[0] else None,
        "duration_s": dur,
        "litres": round((v[2] or 0) / 10000, 3),
        "cost": round((v[3] or 0) / 1000, 3),
        "started_by": RUN_SOURCES[int(v[4] or 0)] if int(v[4] or 0) < len(RUN_SOURCES) else "unknown",
        "ended": RUN_ENDS[int(v[5] or 0)] if int(v[5] or 0) < len(RUN_ENDS) else "none",
        "fault": int(v[6] or 0),
        "fuel_from_pump": bool(flags & 1),
        "health": bool(flags & 2),
        "room_start": tenth(v[8]),
        "room_end": tenth(v[9]),
        "flame_high": v[10],
        "flame_peak": v[11],
        "ignite_s": pos(v[12]),
        "fan_gap": round((v[13] or 0) / 10, 1),
        "volts_rest": tenth(pos(v[15])),
        "volts_glow": tenth(pos(v[16])),
        "phases_min": {k: round(s / 60, 1) for k, s in phases.items()},
    }


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
        self._runs_at = 0.0
        self._last_run: dict[str, Any] | None = None
        self._last_health_run: dict[str, Any] | None = None
        self._health: dict[str, Any] | None = None
        self._service: dict[str, Any] | None = None
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
            model=MODEL_DIAL if self._info and self._info.get("dial") else MODEL,
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
        if not self._runs_at or now - self._runs_at >= RUNS_INTERVAL.total_seconds():
            await self._refresh_runs(now)

        # Best effort: neither may fail the update.
        await self._sync_clock(status, now)
        await self._push_external_temperature(now)

        return HearthData(
            status=status,
            info=self._info,
            timers=self._timers,
            fuel_day=self._fuel_day,
            last_run=self._last_run,
            last_health_run=self._last_health_run,
            health=self._health,
            service=self._service,
        )

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

    async def _refresh_runs(self, now: float) -> None:
        self._runs_at = now
        if (roamos_number(self._info.get("version")) or 0) < RUNS_FIRMWARE:
            self._last_run = self._last_health_run = None
            return
        try:
            page = await self.client.get_runs(10)
        except HearthError as err:
            _LOGGER.debug("Could not read the run history: %s", err)
            return
        runs = [r for r in (parse_run(row) for row in (page or {}).get("runs") or []) if r]
        self._last_run = runs[0] if runs else None
        self._last_health_run = next((r for r in runs if r["health"]), self._last_health_run)
        try:
            self._health = await self.client.get_health()
        except HearthError as err:
            _LOGGER.debug("Could not read heater health: %s", err)
        try:
            self._service = await self.client.get_service()
        except HearthError as err:
            _LOGGER.debug("Could not read servicing: %s", err)

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

    async def async_stop(self, always: bool = False) -> bool:
        """Stop the heater. Auto must be switched off first or the box starts it again.

        Heater Care (firmware 1.61) does not let Home Assistant stop a run inside its minimum run. Thermostat off is
        still accepted: Auto goes off and the box stops the run once its minimum run is up, so that is not an error.
        Any other stop the box refuses (a preheat) raises, with the box's reason. Only the Tillerwell app can override.
        A heater already cooling down (or idle) gets no stop: once Auto is off there is nothing left to stop, and a
        refused stop must not turn a thermostat off that worked into an error. `always` (the Stop button) sends the
        stop in any state, since the heater still reads idle while it primes or tests its fan.
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
        if always or status.get("state") in ACTIVE_STATES:
            if not care:
                await self.async_command("stop")
                return False
            waiting = False
            try:
                await self.client.command("stop", source=SOURCE)
            except HearthRequestError as err:
                if not auto_was_on:
                    raise HomeAssistantError(str(err)) from err
                # thermostat off was taken; the box decides when the run stops
                waiting = err.care == "min_run"
                if not waiting:
                    _LOGGER.warning("Thermostat off, but %s did not take the stop: %s", self.config_entry.title, err)
            except HearthError as err:
                raise HomeAssistantError(f"Could not reach {self.config_entry.title}: {err}") from err
            await self.async_request_refresh()
            return waiting
        return False

    @property
    def run_mode_supported(self) -> bool:
        """Hearth 1.68: a running heater can be switched between preheat and thermostat (`run_mode`)."""
        status = self.data.status if self.data else {}
        version = status.get("os_version") or self._info.get("version")
        return (roamos_number(version) or 0) >= RUN_MODE_FIRMWARE

    async def async_set_timers(self, timers: list[dict[str, Any]]) -> None:
        """Replace the box's timer list. The box checks every rule and says why if it refuses."""
        try:
            await self.client.put_timers(timers)
        except HearthError as err:
            raise HomeAssistantError(str(err)) from err
        self._timers_at = 0.0
        await self.async_request_refresh()
