"""Shared fixtures."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tillerwell.const import DOMAIN

HOST = "hearth-a1b2c3.local"
BASE = f"http://{HOST}"

INFO: dict[str, Any] = {
    "version": "1.0001",
    "device_name": "hearth-a1b2c3",
    "ip": "192.168.1.50",
    "rssi": -61,
    "timezone": "GMT0BST,M3.5.0/1,M10.5.0",
    "wifi_ssid": "boat",
    "can_switch_mode": True,
    "mode": "wifi",
    "web_password_set": False,
}

STATUS: dict[str, Any] = {
    "state": "idle",
    "state_raw": 0,
    "substate": 0,
    "fault": 0,
    "fault_name": None,
    "fault_active": False,
    "coolant_temp": 21.5,
    "elapsed_min": 0,
    "burner_active": False,
    "cabin_temp": 17,
    "stale": False,
    "last_fault_name": None,
    "effective_temp": 17,
    "external_temp_active": False,
    "use_external_temp": True,
    "auto_enabled": False,
    "auto_target": 20,
    "auto_low": 19,
    "auto_high": 21,
    "hyst_min": 3,
    "hyst_max": 2,
    "preheat_temp": 88,
    "auto_note": "",
    "prevent_freezing_enabled": False,
    "prevent_freezing_target": 5,
    "prevent_freezing_note": "",
    "preheat_minutes": 30,
    "bypass_mode": False,
    "debug_mode": False,
    "extended_active": False,
    "controller_seen": True,
    "heater_seen": True,
    "standalone": False,
    "clock_ok": True,
    "timezone": "GMT0BST,M3.5.0/1,M10.5.0",
    "next_timer": {"id": "T1", "start_epoch": 1793000000},
    "active_timer_id": None,
    "timer_note": "06:30 skipped: heater already running",
    "ext_voltage": None,
    "supply_voltage": 12.6,
    "ext_flame_temp_c": None,
    "mode_name": "waiting for a command",
    "power_level": "off",
    "fuel_rate_lph": 0,
    "fuel_litres_total": 12.3456,
    "fuel_cost_total": 18.52,
    "fuel_litres_today": 0.75,
    "fuel_cost_today": 1.13,
    "fuel_price": 1.5,
    "fuel_factor": 1.0,
    "fuel_rates": {"starting": 0.15, "low": 0.15, "middle": 0.35, "high": 0.62},
    "burn_hours_total": 41.5,
    "hours_low": 3.0,
    "hours_middle": 12.25,
    "hours_high": 25.0,
    "starts_total": 57,
    "last_command": "thermostat",
}

TIMERS: list[dict[str, Any]] = [
    {
        "id": "T1",
        "enabled": True,
        "mode": "preheat",
        "start_minute": 390,
        "duration_min": 60,
        "days": [1, 2, 3, 4, 5],
        "date": None,
        "low_c": 19,
        "high_c": 22,
    }
]


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components in the repo."""


@pytest.fixture(autouse=True)
def _no_real_zeroconf(mock_async_zeroconf: None) -> None:
    """The manifest depends on zeroconf; keep tests off the network."""


@pytest.fixture
def box(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """A healthy box."""
    aioclient_mock.get(f"{BASE}/api/info", json=INFO)
    aioclient_mock.get(f"{BASE}/api/status", json=dict(STATUS))
    aioclient_mock.get(f"{BASE}/api/timers", json=TIMERS)
    aioclient_mock.post(f"{BASE}/api/cmd", json={"ok": True})
    aioclient_mock.put(f"{BASE}/api/timers", json={"ok": True})
    aioclient_mock.post(f"{BASE}/api/reboot", json={"ok": True})
    return aioclient_mock


@pytest.fixture
def entry() -> MockConfigEntry:
    """A configured box."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=INFO["device_name"],
        title="Hearth a1b2c3",
        data={"host": HOST},
    )
