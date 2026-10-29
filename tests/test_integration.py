"""Entities, commands and services against a mocked box."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE,
)
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.number import DOMAIN as NUMBER
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.switch import SERVICE_TURN_ON
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tillerwell.const import DOMAIN
from custom_components.tillerwell.number import _rates_with

from .conftest import BASE, INFO, STATUS

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
    assert climate.attributes["target_temp_low"] == 19
    assert climate.attributes["target_temp_high"] == 21
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
        {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TARGET_TEMP_LOW: 18, ATTR_TARGET_TEMP_HIGH: 22},
        blocking=True,
    )
    assert sent(box) == [{"cmd": "auto_range", "low": 18, "high": 22}]

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            CLIMATE,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TARGET_TEMP_LOW: 22, ATTR_TARGET_TEMP_HIGH: 20},
            blocking=True,
        )


async def test_switch_buttons_numbers(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    await setup(hass, entry)
    await hass.services.async_call(
        SWITCH, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.hearth_a1b2c3_heater"}, blocking=True
    )
    await hass.services.async_call(
        SWITCH, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.hearth_a1b2c3_prevent_freezing"}, blocking=True
    )
    await hass.services.async_call(
        BUTTON, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.hearth_a1b2c3_ventilate_pump_only"}, blocking=True
    )
    await hass.services.async_call(
        NUMBER,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: "number.hearth_a1b2c3_start_hysteresis", ATTR_VALUE: 4},
        blocking=True,
    )
    assert sent(box) == [
        {"cmd": "start_preheat", "minutes": 30},
        {"cmd": "freeze", "enabled": True},
        {"cmd": "start_pump"},
        {"cmd": "hysteresis", "min": 4, "max": 2},
    ]


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
    status = {k: v for k, v in STATUS.items() if not k.startswith(("fuel_", "power_level", "mode_name", "tank_"))}
    aioclient_mock.get(f"{BASE}/api/info", json=INFO)
    aioclient_mock.get(f"{BASE}/api/status", json=status)
    aioclient_mock.get(f"{BASE}/api/timers", json=[])
    await setup(hass, entry)
    assert hass.states.get("sensor.hearth_a1b2c3_fuel_used_estimated").state == "unknown"
    assert hass.states.get("number.hearth_a1b2c3_fuel_price").state == "unavailable"
    assert hass.states.get("number.hearth_a1b2c3_fuel_in_tank").state == "unavailable"


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
            BUTTON, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.hearth_a1b2c3_ventilate_pump_only"}, blocking=True
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
