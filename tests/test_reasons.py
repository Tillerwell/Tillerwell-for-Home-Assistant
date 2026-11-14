"""The Last start or stop sensor's explanations (reasons.py), without a box."""

from __future__ import annotations

from custom_components.tillerwell.reasons import (
    clip,
    explain_pump,
    explain_refusal,
    explain_stop,
    explain_thermostat_on,
)


def test_thermostat_on_while_resting() -> None:
    after = {
        "state": "idle",
        "effective_temp": 18.2,
        "auto_start_at": 19.5,
        "auto_high": 21,
        "thermo": {"min_off_left_s": 600},
    }
    text = explain_thermostat_on(after)
    assert "resting for another 10 min" in text and "stops at 21°" in text


def test_thermostat_on_room_warm_enough() -> None:
    after = {"state": "idle", "effective_temp": 20.4, "auto_start_at": 19.5, "auto_high": 21}
    assert explain_thermostat_on(after).startswith("Thermostat on. The room is 20.4°, above the 19.5° start point")


def test_thermostat_on_already_running_and_no_reading() -> None:
    assert "is running and stops when the room reaches 21°" in explain_thermostat_on(
        {"state": "running", "auto_high": 21}
    )
    assert "no room reading yet" in explain_thermostat_on({"state": "idle", "auto_high": 21})


def test_stops() -> None:
    running = {"state": "running", "thermo": {"min_run_left_s": 0}}
    assert explain_stop(running, {}, False).startswith("Stopped. The run had done its 30-minute minimum.")
    assert explain_stop({"state": "cooldown"}, {}, False).startswith("The heater was already cooling down")
    assert explain_stop({"state": "idle", "auto_enabled": True}, {}, False).startswith("Thermostat off.")
    assert explain_stop({"state": "idle"}, {}, False) == "Nothing to stop: the heater was already off."
    waiting = explain_stop({"state": "running"}, {"thermo": {"min_run_left_s": 300}}, True)
    assert "5 more min of its 30-minute minimum" in waiting


def test_refusal_pump_and_clip() -> None:
    assert explain_refusal("preheat", "Heater Care: resting.", "min_off") == "Not started: Heater Care: resting."
    assert explain_pump({"pump_minutes": 40}).startswith("Water pump running for 40 min")
    long = clip("x" * 300)
    assert len(long) == 255 and long.endswith("…")
