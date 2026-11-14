"""Entities, commands and services against a mocked box."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE,
)
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.number import DOMAIN as NUMBER
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.switch import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse

from custom_components.tillerwell.const import DOMAIN
from custom_components.tillerwell.number import _rates_with

from .conftest import BASE, INFO, STATUS, TIMERS

CLIMATE_ID = "climate.hearth_a1b2c3"


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def sent(mock: AiohttpClientMocker) -> list[dict]:
    """Bodies POSTed to /api/cmd, in order."""
    return [call[2] for call in mock.mock_calls if call[0] == "POST" and str(call[1]).endswith("/api/cmd")]


async def test_entities(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await setup(hass, entry)

    climate = hass.states.get(CLIMATE_ID)
    assert climate.state == "off"
    assert climate.attributes["current_temperature"] == 17
    assert climate.attributes["temperature"] == 21
    assert climate.attributes["hvac_action"] == "off"

    assert hass.states.get("sensor.hearth_a1b2c3_state").state == "idle"
    assert hass.states.get("sensor.hearth_a1b2c3_coolant_temperature").state == "21.5"
    assert hass.states.get("sensor.hearth_a1b2c3_heater_temperature").state == "17"
    assert hass.states.get("sensor.hearth_a1b2c3_wi_fi_signal").state == "-61"
    assert hass.states.get("sensor.hearth_a1b2c3_timers").state == "1"
    assert hass.states.get("sensor.hearth_a1b2c3_next_timer").state.startswith("2026-")
    assert hass.states.get("binary_sensor.hearth_a1b2c3_fault").state == "off"
    assert hass.states.get("binary_sensor.hearth_a1b2c3_heater_link").state == "on"
    assert hass.states.get("switch.hearth_a1b2c3_heater").state == "off"
    assert hass.states.get("number.hearth_a1b2c3_preheat_duration").state == "30"
    assert hass.states.get("switch.hearth_a1b2c3_prevent_freezing").state == "off"
    assert hass.states.get("sensor.hearth_a1b2c3_next_start_temperature").state == "20"
    # The range is exactly where the heater starts and stops now; there is no buffer to set.
    assert hass.states.get("number.hearth_a1b2c3_start_hysteresis") is None
    assert hass.states.get("number.hearth_a1b2c3_stop_hysteresis") is None


async def test_nothing_to_report(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """No fault, notes or command show a value, and extended telemetry is unavailable until it arrives."""
    status = {**STATUS, "timer_note": "", "last_command": None, "ext_flame_temp_c": None}
    aioclient_mock.get(f"{BASE}/api/info", json=INFO)
    aioclient_mock.get(f"{BASE}/api/status", json=status)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    extended = [
        entity_registry.async_get_or_create(
            platform, DOMAIN, f"{INFO['device_name']}_{key}", config_entry=entry, disabled_by=None
        ).entity_id
        for platform, key in (("sensor", "ext_flame_temp"), ("binary_sensor", "ext_glow_plug"))
    ]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.hearth_a1b2c3_fault").state == "No fault"
    assert hass.states.get("sensor.hearth_a1b2c3_last_fault").state == "None"
    assert hass.states.get("sensor.hearth_a1b2c3_last_timer_event").state == "None"
    assert hass.states.get("sensor.hearth_a1b2c3_thermostat_note").state == "None"
    assert hass.states.get("sensor.hearth_a1b2c3_prevent_freezing_note").state == "None"
    assert hass.states.get("sensor.hearth_a1b2c3_last_command").state == "None"
    for entity_id in extended:
        assert hass.states.get(entity_id).state == "unavailable"


async def test_fault_unknown_before_heater_reports(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    status = {**STATUS, "fault": None, "fault_name": None}
    aioclient_mock.get(f"{BASE}/api/info", json=INFO)
    aioclient_mock.get(f"{BASE}/api/status", json=status)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    await setup(hass, entry)
    assert hass.states.get("sensor.hearth_a1b2c3_fault").state == "unknown"


async def test_running_heater(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    status = {**STATUS, "state": "running", "burner_active": True, "auto_enabled": True}
    aioclient_mock.get(f"{BASE}/api/info", json={"version": "1", "device_name": "hearth-a1b2c3", "rssi": -50})
    aioclient_mock.get(f"{BASE}/api/status", json=status)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    await setup(hass, entry)
    climate = hass.states.get(CLIMATE_ID)
    assert climate.state == "heat"
    assert climate.attributes["hvac_action"] == "heating"
    assert hass.states.get("switch.hearth_a1b2c3_heater").state == "on"


async def test_heat_arms_auto(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await setup(hass, entry)
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_HVAC_MODE: "heat"}, blocking=True
    )
    assert sent(box) == [{"cmd": "auto", "enabled": True}]


async def test_off_stops_auto_then_heater(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    status = {**STATUS, "state": "running", "burner_active": True, "auto_enabled": True}
    aioclient_mock.get(f"{BASE}/api/info", json={"version": "1", "device_name": "hearth-a1b2c3", "rssi": -50})
    aioclient_mock.get(f"{BASE}/api/status", json=status)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    aioclient_mock.post(f"{BASE}/api/cmd", json={"ok": True})
    await setup(hass, entry)
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_HVAC_MODE: "off"}, blocking=True
    )
    # Auto must be switched off first, or the box restarts the heater.
    assert sent(aioclient_mock) == [{"cmd": "auto", "enabled": False}, {"cmd": "stop"}]


async def test_temperature_range(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await setup(hass, entry)
    await hass.services.async_call(
        CLIMATE,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 22},
        blocking=True,
    )
    # one set temperature: the low end sits hyst_min + hyst_max (3° on this older box) below it
    assert sent(box) == [{"cmd": "auto_range", "low": 19, "high": 22}]

    with pytest.raises(ServiceValidationError, match="at least 3°"):
        await hass.services.async_call(
            CLIMATE,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 2},
            blocking=True,
        )
    assert len(sent(box)) == 1


async def test_switch_buttons_numbers(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await setup(hass, entry)
    await hass.services.async_call(
        SWITCH, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.hearth_a1b2c3_heater"}, blocking=True
    )
    await hass.services.async_call(
        SWITCH, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.hearth_a1b2c3_prevent_freezing"}, blocking=True
    )
    await hass.services.async_call(
        BUTTON, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.hearth_a1b2c3_water_pump_only"}, blocking=True
    )
    await hass.services.async_call(
        NUMBER,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.hearth_a1b2c3_preheat_coolant_limit", ATTR_VALUE: 80},
        blocking=True,
    )
    assert sent(box) == [
        {"cmd": "start_preheat", "minutes": 30},
        {"cmd": "freeze", "enabled": True},
        {"cmd": "start_pump"},
        {"cmd": "preheat_temp", "value": 80},
    ]


async def test_heater_info_and_unlock(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    # firmware 1.42, after Read heater info, with the heater locked out
    status = {
        **STATUS,
        "state_raw": 1,
        "substate": 1,
        "heater_info": {
            "reading": False,
            "version": "10.1.3.7",
            "settings": {"wait_temp": 88, "warmup": "off"},
            "report": {"starts": 300, "no_ignition": 5, "total_s": 36000, "high_s": 18000},
        },
    }
    aioclient_mock.get(f"{BASE}/api/info", json=INFO)
    aioclient_mock.get(f"{BASE}/api/status", json=status)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    aioclient_mock.post(f"{BASE}/api/cmd", json={"ok": True})
    await setup(hass, entry)
    assert hass.states.get("binary_sensor.hearth_a1b2c3_heater_locked").state == "on"
    assert hass.states.get("sensor.hearth_a1b2c3_heater_coolant_target").state == "88"
    assert hass.states.get("sensor.hearth_a1b2c3_heater_firmware").state == "10.1.3.7"
    assert hass.states.get("sensor.hearth_a1b2c3_heater_starts").state == "300"
    assert hass.states.get("sensor.hearth_a1b2c3_heater_failed_lights").state == "5"
    assert float(hass.states.get("sensor.hearth_a1b2c3_heater_running_time").state) == 10.0
    for button in ("read_heater_info", "unlock_heater"):
        await hass.services.async_call(
            BUTTON, SERVICE_PRESS, {ATTR_ENTITY_ID: f"button.hearth_a1b2c3_{button}"}, blocking=True
        )
    assert sent(aioclient_mock) == [{"cmd": "heater_read"}, {"cmd": "heater_unlock"}]


async def test_service_and_heater_settings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    # firmware 1.43, heater idle, settings and fault history read
    status = {
        **STATUS,
        "pump_minutes": 90,
        "heater_info": {
            "settings": {"wait_temp": 88, "pump_standby": True},
            "report": {
                "starts": 3,
                "faults": [{"code": 13, "name": "No ignition"}, {"code": 78, "name": "Flame break"}],
            },
        },
    }
    aioclient_mock.get(f"{BASE}/api/info", json=INFO)
    aioclient_mock.get(f"{BASE}/api/status", json=status)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    aioclient_mock.post(f"{BASE}/api/cmd", json={"ok": True})
    await setup(hass, entry)
    assert hass.states.get("number.hearth_a1b2c3_water_pump_run_time").state == "90"
    assert hass.states.get("number.hearth_a1b2c3_heater_coolant_target").state == "88"
    assert hass.states.get("switch.hearth_a1b2c3_heater_water_pump_in_standby").state == "on"
    last = hass.states.get("sensor.hearth_a1b2c3_heater_s_last_recorded_fault")
    assert last.state == "No ignition" and len(last.attributes["faults"]) == 2
    for button in ("prime_fuel", "fan_test", "stop"):
        await hass.services.async_call(
            BUTTON, SERVICE_PRESS, {ATTR_ENTITY_ID: f"button.hearth_a1b2c3_{button}"}, blocking=True
        )
    await hass.services.async_call(
        NUMBER,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.hearth_a1b2c3_heater_coolant_target", ATTR_VALUE: 75},
        blocking=True,
    )
    await hass.services.async_call(
        SWITCH, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: "switch.hearth_a1b2c3_heater_water_pump_in_standby"}, blocking=True
    )
    await hass.services.async_call(
        NUMBER,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.hearth_a1b2c3_water_pump_run_time", ATTR_VALUE: 30},
        blocking=True,
    )
    # Stop always goes out: the heater still reads idle while it primes
    assert sent(aioclient_mock) == [
        {"cmd": "fuel_prime"},
        {"cmd": "fan_test"},
        {"cmd": "stop"},
        {"cmd": "heater_settings", "wait_temp": 75},
        {"cmd": "heater_settings", "pump_standby": False},
        {"cmd": "pump_minutes", "value": 30},
    ]


async def test_heater_info_before_a_read(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await setup(hass, entry)
    assert hass.states.get("binary_sensor.hearth_a1b2c3_heater_locked").state == "off"
    assert hass.states.get("sensor.hearth_a1b2c3_heater_coolant_target").state == "unavailable"
    assert hass.states.get("sensor.hearth_a1b2c3_heater_starts").state == "unavailable"
    # firmware 1.42 or older: nothing it can change
    assert hass.states.get("number.hearth_a1b2c3_heater_coolant_target").state == "unavailable"
    assert hass.states.get("switch.hearth_a1b2c3_heater_water_pump_in_standby").state == "unavailable"
    assert hass.states.get("number.hearth_a1b2c3_water_pump_run_time").state == "unavailable"


async def test_fuel_estimate(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    hass.config.currency = "GBP"
    await setup(hass, entry)
    used = hass.states.get("sensor.hearth_a1b2c3_fuel_used_estimated")
    assert used.state == "12.3456"
    assert used.attributes["unit_of_measurement"] == "L"
    assert used.attributes["state_class"] == "total_increasing"
    cost = hass.states.get("sensor.hearth_a1b2c3_fuel_cost_estimated")
    assert cost.state == "18.52"
    assert cost.attributes["unit_of_measurement"] == "GBP"
    assert hass.states.get("sensor.hearth_a1b2c3_fuel_used_today_estimated").state == "0.75"
    assert hass.states.get("sensor.hearth_a1b2c3_power_level").state == "off"
    assert hass.states.get("sensor.hearth_a1b2c3_heater_mode").state == "waiting for a command"
    assert hass.states.get("sensor.hearth_a1b2c3_starts").state == "57"
    price = hass.states.get("number.hearth_a1b2c3_fuel_price")
    assert price.state == "1.5"
    assert price.attributes["unit_of_measurement"] == "GBP/L"

    await hass.services.async_call(
        NUMBER, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: "number.hearth_a1b2c3_fuel_price", ATTR_VALUE: 1.62}, blocking=True
    )
    await hass.services.async_call(
        NUMBER,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.hearth_a1b2c3_fuel_calibration_factor", ATTR_VALUE: 1.1},
        blocking=True,
    )
    assert sent(box) == [{"cmd": "fuel_price", "value": 1.62}, {"cmd": "fuel_factor", "value": 1.1}]


async def test_battery_use(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await setup(hass, entry)
    used = hass.states.get("sensor.hearth_a1b2c3_battery_used_estimated")
    assert used.state == "23.456"
    assert used.attributes["unit_of_measurement"] == "Ah"
    assert used.attributes["state_class"] == "total_increasing"
    assert hass.states.get("sensor.hearth_a1b2c3_battery_used_today_estimated").state == "1.7"
    assert hass.states.get("sensor.hearth_a1b2c3_battery_used_this_run_estimated").state == "0.85"
    assert hass.states.get("sensor.hearth_a1b2c3_battery_draw_estimated").state == "0"
    assert hass.states.get("number.hearth_a1b2c3_battery_calibration_factor").state == "1.0"
    await hass.services.async_call(
        NUMBER,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.hearth_a1b2c3_battery_calibration_factor", ATTR_VALUE: 1.2},
        blocking=True,
    )
    assert sent(box) == [{"cmd": "battery_factor", "value": 1.2}]


async def test_tank(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await setup(hass, entry)
    assert hass.states.get("sensor.hearth_a1b2c3_tank_level").state == "76.6"
    in_tank = hass.states.get("number.hearth_a1b2c3_fuel_in_tank")
    assert in_tank.state == "61.25"
    assert in_tank.attributes["max"] == 80
    assert hass.states.get("number.hearth_a1b2c3_tank_size").state == "80"
    await hass.services.async_call(
        NUMBER, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: "number.hearth_a1b2c3_fuel_in_tank", ATTR_VALUE: 40}, blocking=True
    )
    await hass.services.async_call(
        BUTTON, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.hearth_a1b2c3_tank_filled_to_the_top"}, blocking=True
    )
    assert sent(box) == [{"cmd": "tank_level", "value": 40}, {"cmd": "tank_filled"}]


def test_fuel_rate_sends_all_four() -> None:
    params = _rates_with("high")(0.7, STATUS)
    assert params == {"starting": 0.15, "low": 0.15, "middle": 0.35, "high": 0.7}


async def test_older_firmware_without_fuel(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    status = {
        k: v
        for k, v in STATUS.items()
        if not k.startswith(("fuel_", "power_level", "mode_name", "tank_", "auto_start_at"))
    }
    aioclient_mock.get(f"{BASE}/api/info", json=INFO)
    aioclient_mock.get(f"{BASE}/api/status", json=status)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    await setup(hass, entry)
    assert hass.states.get("sensor.hearth_a1b2c3_fuel_used_estimated").state == "unknown"
    assert hass.states.get("number.hearth_a1b2c3_fuel_price").state == "unavailable"
    assert hass.states.get("number.hearth_a1b2c3_fuel_in_tank").state == "unavailable"
    # before RoamOS 1.0038
    assert hass.states.get("sensor.hearth_a1b2c3_next_start_temperature").state == "unavailable"


async def test_refused_command_is_an_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{BASE}/api/info", json={"version": "1", "device_name": "hearth-a1b2c3", "rssi": -50})
    aioclient_mock.get(f"{BASE}/api/status", json=STATUS)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    aioclient_mock.post(f"{BASE}/api/cmd", json={"ok": False})
    await setup(hass, entry)
    with pytest.raises(HomeAssistantError, match="did not accept"):
        await hass.services.async_call(
            BUTTON, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.hearth_a1b2c3_water_pump_only"}, blocking=True
        )


async def test_set_timers_service(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "set_timers",
        {
            ATTR_ENTITY_ID: CLIMATE_ID,
            "timers": [
                {"id": "A", "mode": "preheat", "start": "06:30", "duration_min": 60, "days": ["mon", "fri"]},
                {"id": "B", "mode": "thermostat", "start": "18:00:00", "duration_min": 120, "date": "2026-12-25"},
            ],
        },
        blocking=True,
    )
    put = [c for c in box.mock_calls if c[0] == "PUT"][-1][2]
    assert put[0] == {
        "id": "A",
        "enabled": True,
        "mode": "preheat",
        "start_minute": 390,
        "duration_min": 60,
        "days": [1, 5],
        "date": None,
        "low_c": 19,
        "high_c": 22,
    }
    assert put[1]["date"] == "2026-12-25" and put[1]["days"] == [] and put[1]["start_minute"] == 1080


async def test_set_timers_rejected_by_box(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{BASE}/api/info", json={"version": "1", "device_name": "hearth-a1b2c3", "rssi": -50})
    aioclient_mock.get(f"{BASE}/api/status", json=STATUS)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    aioclient_mock.put(
        f"{BASE}/api/timers", status=400, json={"ok": False, "error": "timer 2: too close to another timer"}
    )
    await setup(hass, entry)
    with pytest.raises(HomeAssistantError, match="too close"):
        await hass.services.async_call(
            DOMAIN,
            "set_timers",
            {
                ATTR_ENTITY_ID: CLIMATE_ID,
                "timers": [{"mode": "preheat", "start": "06:30", "duration_min": 60, "days": ["mon"]}],
            },
            blocking=True,
        )


async def test_clock_is_set_when_box_has_none(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{BASE}/api/info", json={"version": "1", "device_name": "hearth-a1b2c3", "rssi": -50})
    aioclient_mock.get(f"{BASE}/api/status", json={**STATUS, "clock_ok": False, "timezone": ""})
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    aioclient_mock.post(f"{BASE}/api/cmd", json={"ok": True})
    await setup(hass, entry)
    cmds = sent(aioclient_mock)
    assert cmds[0] == {"cmd": "timezone", "value": hass.config.time_zone}
    assert cmds[1]["cmd"] == "set_time" and isinstance(cmds[1]["epoch"], int)


async def test_external_temperature_sensor_is_pushed(
    hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    hass.states.async_set("sensor.cabin", "64.4", {"unit_of_measurement": "°F"})
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"external_temperature_entity": "sensor.cabin"})
    await setup(hass, entry)
    assert {"cmd": "external_temp", "value": 18.0} in sent(box)


async def test_steady_sensor_keeps_being_pushed(
    hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A sensor whose value has not changed for hours, but which still reports, is a steady room, not a dead sensor."""
    from datetime import UTC, datetime, timedelta

    hass.states.async_set("sensor.cabin", "16.9", {"unit_of_measurement": "°C"})
    state = hass.states.get("sensor.cabin")
    old = datetime.now(UTC) - timedelta(hours=3)
    object.__setattr__(state, "last_changed", old)
    object.__setattr__(state, "last_updated", old)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"external_temperature_entity": "sensor.cabin"})
    await setup(hass, entry)
    assert {"cmd": "external_temp", "value": 16.9} in sent(box)


async def test_silent_sensor_is_not_pushed(
    hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """A sensor that has not reported for over an hour is no longer sent, so the box falls back to the controller."""
    from datetime import UTC, datetime, timedelta

    hass.states.async_set("sensor.cabin", "16.9", {"unit_of_measurement": "°C"})
    state = hass.states.get("sensor.cabin")
    old = datetime.now(UTC) - timedelta(minutes=61)
    for field in ("last_changed", "last_updated", "last_reported"):
        object.__setattr__(state, field, old)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"external_temperature_entity": "sensor.cabin"})
    await setup(hass, entry)
    assert not any(c["cmd"] == "external_temp" for c in sent(box))


async def test_unreachable_box_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{BASE}/api/status", exc=TimeoutError)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_box_with_web_password_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{BASE}/api/status", status=401)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


def serve(mock: AiohttpClientMocker, base: str, info: dict) -> None:
    mock.get(f"{base}/api/info", json=info)
    mock.get(f"{base}/api/status", json=dict(STATUS))
    mock.get(f"{base}/api/timers", json=TIMERS)


def device_name(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, INFO["device_name"]), entry.entry_id)
    return device.name if device else None


async def test_name_from_the_app(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    serve(aioclient_mock, BASE, {**INFO, "display_name": "Autoterm 5D"})
    await setup(hass, entry)
    assert entry.title == "Autoterm 5D"
    assert device_name(hass, entry) == "Autoterm 5D"

    # Renamed in the app: Home Assistant follows within an info refresh, without a reload.
    coordinator = entry.runtime_data
    aioclient_mock.clear_requests()
    serve(aioclient_mock, BASE, {**INFO, "display_name": "Galley"})
    coordinator._info_at = float("-inf")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert entry.title == "Galley"
    assert device_name(hass, entry) == "Galley"
    assert entry.runtime_data is coordinator


async def test_title_typed_in_home_assistant_is_kept(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=INFO["device_name"], title="Boat", data={"host": "hearth-a1b2c3.local"}
    )
    serve(aioclient_mock, BASE, {**INFO, "display_name": "Autoterm 5D"})
    await setup(hass, entry)
    assert entry.title == "Boat"
    assert device_name(hass, entry) == "Autoterm 5D"


async def test_box_found_at_a_new_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry, rediscover
) -> None:
    aioclient_mock.get(f"{BASE}/api/status", exc=TimeoutError)
    serve(aioclient_mock, "http://192.168.1.77", INFO)
    rediscover.return_value = ["192.168.1.77"]
    await setup(hass, entry)
    assert entry.data["host"] == "192.168.1.77"
    assert hass.states.get("sensor.hearth_a1b2c3_state").state == "idle"


async def test_other_box_at_the_address_is_ignored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry, rediscover
) -> None:
    aioclient_mock.get(f"{BASE}/api/status", exc=TimeoutError)
    serve(aioclient_mock, "http://192.168.1.77", {**INFO, "device_name": "hearth-ffffff"})
    rediscover.return_value = ["192.168.1.77"]
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.data["host"] == "hearth-a1b2c3.local"


async def test_new_address_needs_no_reload(
    hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    coordinator = entry.runtime_data
    serve(box, "http://192.168.1.77", INFO)
    hass.config_entries.async_update_entry(entry, data={"host": "192.168.1.77"})
    await hass.async_block_till_done()
    assert entry.runtime_data is coordinator
    assert coordinator.client.host == "192.168.1.77"
    assert any(str(call[1]) == "http://192.168.1.77/api/status" for call in box.mock_calls)

    # Options still reload, so the new sensor choice takes effect.
    hass.config_entries.async_update_entry(entry, options={"external_temperature_entity": "sensor.cabin"})
    await hass.async_block_till_done()
    assert entry.runtime_data is not coordinator


def test_every_translation_key_exists() -> None:
    """Entity names come from translations/en.json, so every key used must be there."""
    from custom_components.tillerwell import binary_sensor, button, number, sensor, switch

    en = json.loads((Path(__file__).parent.parent / "custom_components/tillerwell/translations/en.json").read_text())[
        "entity"
    ]
    wanted = {
        "binary_sensor": [d.translation_key for d in binary_sensor.BINARY_SENSORS],
        "button": [d.translation_key for d in button.BUTTONS if d.translation_key],
        "number": [d.translation_key for d in number.NUMBERS],
        "sensor": [d.translation_key for d in sensor.SENSORS] + ["timers"],
        "switch": [d.translation_key for d in switch.SWITCHES],
    }
    for platform, keys in wanted.items():
        missing = [k for k in keys if k not in en[platform]]
        assert not missing, f"{platform}: {missing}"


THERMO = {
    "work_low": 19.5,
    "work_high": 21.0,
    "learned_width": 1.5,
    "min_width": 1.0,
    "sensor": "external",
    "ceiling": 26.0,
    "at_limit": None,
    "reason": "widened",
    "min_run_left_s": 480,
    "min_off_left_s": 0,
    "holding": "top",
    "run_on_at": 1762871100,
    "flame_on_at": 1762871400,
    "sensor_stuck": False,
    "no_reading": False,
    "rise_rate": 0.05,
    "warmup_typical_s": 360,
    "cool_typical_s": 2700,
    "dip_below": 0,
    "early_stop_unseen": 1,
    "last_run_end": "top",
    "last_early_stop": {"at": 1762871940, "after_s": 540, "source": "matter"},
}


def heater_care_box(aioclient_mock: AiohttpClientMocker, refuse_stop: dict | None = None, **status: object) -> None:
    """A box with Heater Care. refuse_stop: the 400 body a 1.61 box answers a locked stop with."""
    aioclient_mock.get(f"{BASE}/api/info", json=INFO)
    aioclient_mock.get(
        f"{BASE}/api/status",
        json={**STATUS, "hyst_min": 0.5, "hyst_max": 0.5, "thermo": THERMO, **status},
    )
    aioclient_mock.get(f"{BASE}/api/timers", json=TIMERS)
    if refuse_stop is None:
        aioclient_mock.post(f"{BASE}/api/cmd", json={"ok": True})
        return

    async def reply(method, url, data):
        if isinstance(data, dict) and data.get("cmd") == "stop":
            return AiohttpClientMockResponse(method, url, status=400, json=refuse_stop)
        return AiohttpClientMockResponse(method, url, json={"ok": True})

    aioclient_mock.post(f"{BASE}/api/cmd", side_effect=reply)


async def test_heater_care_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    heater_care_box(aioclient_mock, state="late-run", burner_active=True, auto_enabled=True)
    await setup(hass, entry)
    assert hass.states.get("sensor.hearth_a1b2c3_heater_care_start_temperature").state == "19.5"
    assert hass.states.get("sensor.hearth_a1b2c3_minimum_run_left").state == "8.0"
    assert hass.states.get("sensor.hearth_a1b2c3_minimum_rest_left").state == "0.0"
    assert hass.states.get("sensor.hearth_a1b2c3_heater_care_holding_stop").state == "top"
    early = hass.states.get("sensor.hearth_a1b2c3_early_stops")
    assert early.state == "1" and early.attributes["last_early_stop"]["source"] == "matter"
    assert hass.states.get("binary_sensor.hearth_a1b2c3_no_temperature_reading").state == "off"


async def test_early_start_attributes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Firmware 1.60: how the next start was worked out rides on the next start temperature sensor."""
    early = {"start_at": 20.7, "est": "model", "net_rate": 0.0069, "climb": 0.08, "busy_w": 0}
    heater_care_box(aioclient_mock, auto_start_at=20.7, thermo={**THERMO, "early": early})
    await setup(hass, entry)
    s = hass.states.get("sensor.hearth_a1b2c3_next_start_temperature")
    assert s.state == "20.7"
    assert s.attributes["early_est"] == "model" and s.attributes["early_busy_w"] == 0
    assert s.attributes["early_net_rate"] == 0.0069 and s.attributes["early_climb"] == 0.08


async def test_heater_care_off_lets_the_run_finish(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    heater_care_box(aioclient_mock, state="running", burner_active=True, auto_enabled=True)
    await setup(hass, entry)
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_HVAC_MODE: "off"}, blocking=True
    )
    # Home Assistant never asks to stop a run early: Auto goes off and the box decides when the run may stop
    assert sent(aioclient_mock) == [
        {"cmd": "auto", "enabled": False, "source": "home_assistant"},
        {"cmd": "stop", "source": "home_assistant"},
    ]


REFUSED = {
    "ok": False,
    "error": "Heater Care: the run has 17 more min of its minimum run to go before it can stop. "
    "Only the Tillerwell app can stop it sooner.",
    "care": "min_run",
    "left_s": 1013,
}


async def test_heater_care_lock_off_inside_minimum_run_is_not_an_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Firmware 1.61: the stop is refused inside the minimum run, but thermostat off was taken, so no error."""
    heater_care_box(aioclient_mock, REFUSED, state="running", burner_active=True, auto_enabled=True)
    await setup(hass, entry)
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_HVAC_MODE: "off"}, blocking=True
    )
    assert [c["cmd"] for c in sent(aioclient_mock)][-2:] == ["auto", "stop"]
    last = hass.states.get("sensor.hearth_a1b2c3_last_start_or_stop")
    assert last.state.startswith("Thermostat off. The run has 8 more min of its 30-minute minimum")
    assert last.attributes["action"] == "stop" and last.attributes["result"] == "waiting"


async def test_heater_care_lock_refusal_shows_the_reason(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    """Firmware 1.61: a preheat stop inside the minimum run is refused and Home Assistant says why."""
    heater_care_box(aioclient_mock, REFUSED, state="running", burner_active=True)
    await setup(hass, entry)
    with pytest.raises(HomeAssistantError, match="17 more min of its minimum run"):
        await hass.services.async_call(
            SWITCH, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: "switch.hearth_a1b2c3_heater"}, blocking=True
        )
    last = hass.states.get("sensor.hearth_a1b2c3_last_start_or_stop")
    assert last.state.startswith("Not stopped: Heater Care: the run has 17 more min of its minimum run")
    assert last.attributes["result"] == "refused" and last.attributes["heater_care"] == "min_run"


async def test_last_start_or_stop_explains_what_happens(
    hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    assert hass.states.get("sensor.hearth_a1b2c3_last_start_or_stop").state == "unknown"
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_HVAC_MODE: "heat"}, blocking=True
    )
    last = hass.states.get("sensor.hearth_a1b2c3_last_start_or_stop")
    assert last.state == (
        "Thermostat on. The room is 17°, at or below the 20° start point, so the heater starts now "
        "and stops when the room reaches 21°."
    )
    assert last.attributes["action"] == "thermostat_on" and last.attributes["result"] == "done"
    await hass.services.async_call(
        SWITCH, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.hearth_a1b2c3_heater"}, blocking=True
    )
    assert (
        hass.states.get("sensor.hearth_a1b2c3_last_start_or_stop").state
        == "Started a 30-minute preheat. It ends early if the coolant reaches 88°."
    )


async def test_heater_care_one_degree_range(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    heater_care_box(aioclient_mock)
    await setup(hass, entry)
    await hass.services.async_call(
        CLIMATE,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 21},
        blocking=True,
    )
    assert sent(aioclient_mock) == [{"cmd": "auto_range", "low": 20, "high": 21, "source": "home_assistant"}]


async def test_heater_care_reset_button(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    heater_care_box(aioclient_mock)
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        BUTTON,
        DOMAIN,
        f"{entry.unique_id}_heater_care_reset",
        suggested_object_id="hearth_a1b2c3_reset_heater_care_learning",
        disabled_by=None,
        config_entry=entry,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        BUTTON, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.hearth_a1b2c3_reset_heater_care_learning"}, blocking=True
    )
    assert sent(aioclient_mock) == [{"cmd": "thermo_learn_reset"}]


async def test_heater_care_unavailable_on_older_firmware(
    hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    await setup(hass, entry)
    assert hass.states.get("sensor.hearth_a1b2c3_minimum_run_left").state == "unavailable"


async def test_heater_care_switch_off_stops(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    heater_care_box(aioclient_mock, state="running", burner_active=True, auto_enabled=True)
    await setup(hass, entry)
    await hass.services.async_call(
        SWITCH, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: "switch.hearth_a1b2c3_heater"}, blocking=True
    )
    assert sent(aioclient_mock) == [
        {"cmd": "auto", "enabled": False, "source": "home_assistant"},
        {"cmd": "stop", "source": "home_assistant"},
    ]
