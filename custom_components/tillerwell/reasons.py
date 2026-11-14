"""Why a start or stop from Home Assistant did, or did not, happen: the text of the Last start or stop sensor.

A refusal carries the box's own reason (Heater Care, firmware 1.61, says how long is left). When the box accepts,
the explanation is worked out from its status just before and just after: where the thermostat starts and stops,
the room's temperature, and what is left of a run's minimum run or the rest after it.
"""

from __future__ import annotations

from typing import Any

from .const import ACTIVE_STATES, STATE_COOLDOWN, STATE_FINAL_SHUTDOWN, STATE_IDLE

ACTION_PREHEAT = "preheat"
ACTION_THERMOSTAT_ON = "thermostat_on"
ACTION_STOP = "stop"
ACTION_PUMP = "pump"
ACTIONS = [ACTION_PREHEAT, ACTION_THERMOSTAT_ON, ACTION_STOP, ACTION_PUMP]

RESULT_DONE = "done"
RESULT_REFUSED = "refused"
RESULT_FAILED = "failed"
RESULT_WAITING = "waiting"  # accepted, but the heater finishes its minimum run first

MAX_STATE = 255  # Home Assistant's limit for a state


def _deg(value: Any) -> str:
    return f"{float(value):g}°" if isinstance(value, (int, float)) else "?"


def _minutes(seconds: Any) -> int:
    return max(1, round(seconds / 60)) if isinstance(seconds, (int, float)) and seconds > 0 else 0


def _thermo(status: dict[str, Any]) -> dict[str, Any]:
    thermo = status.get("thermo")
    return thermo if isinstance(thermo, dict) else {}


def _room(status: dict[str, Any]) -> Any:
    value = status.get("effective_temp")
    return status.get("cabin_temp") if value is None else value


def explain_refusal(action: str, message: str, care: str | None) -> str:
    """The box's reason as it gave it; a plain prefix says what was refused."""
    what = {
        ACTION_PREHEAT: "Not started",
        ACTION_THERMOSTAT_ON: "Thermostat not switched on",
        ACTION_STOP: "Not stopped",
        ACTION_PUMP: "Water pump not started",
    }.get(action, "Refused")
    return f"{what}: {message}"


def explain_preheat(after: dict[str, Any], minutes: int) -> str:
    """A timed preheat the box accepted."""
    text = f"Started a {minutes}-minute preheat."
    if isinstance(after.get("preheat_temp"), (int, float)):
        text += f" It ends early if the coolant reaches {_deg(after['preheat_temp'])}."
    return text


def explain_thermostat_on(after: dict[str, Any]) -> str:
    """The thermostat was switched on: what it will do next, from the box's status straight afterwards."""
    room, start_at, top = _room(after), after.get("auto_start_at"), after.get("auto_high")
    thermo = _thermo(after)
    rest = _minutes(thermo.get("min_off_left_s"))
    state = after.get("state")
    if state in ACTIVE_STATES:
        return f"Thermostat on. The heater is running and stops when the room reaches {_deg(top)}."
    if not isinstance(room, (int, float)) or not isinstance(start_at, (int, float)):
        return f"Thermostat on. It keeps the room at {_deg(top)}; there is no room reading yet, so it waits for one."
    if room <= start_at:
        if rest:
            return (
                f"Thermostat on. The room is {_deg(room)}, at or below the {_deg(start_at)} start point, "
                f"but the heater is resting for another {rest} min after its last run "
                f"(heaters like this last longer that way). Then it starts, and stops at {_deg(top)}."
            )
        return (
            f"Thermostat on. The room is {_deg(room)}, at or below the {_deg(start_at)} start point, so the heater "
            f"starts now and stops when the room reaches {_deg(top)}."
        )
    return (
        f"Thermostat on. The room is {_deg(room)}, above the {_deg(start_at)} start point, so the heater waits. "
        f"It starts once the room is at or below {_deg(start_at)} and stops at {_deg(top)}."
    )


def explain_stop(before: dict[str, Any], after: dict[str, Any], waiting: bool) -> str:
    """A stop (or thermostat off) the box accepted."""
    state, auto_was_on = before.get("state"), bool(before.get("auto_enabled"))
    if waiting:
        left = _minutes(_thermo(after).get("min_run_left_s") or _thermo(before).get("min_run_left_s"))
        more = f"{left} more min of" if left else "a little left of"
        return (
            f"Thermostat off. The run has {more} its 30-minute minimum, so the heater stops by itself then. "
            "Short runs wear the heater, so Heater Care lets a run finish its minimum."
        )
    if state in ACTIVE_STATES:
        text = "Stopped."
        thermo = _thermo(before)
        if thermo and not thermo.get("min_run_left_s"):
            text += " The run had done its 30-minute minimum."
        return text + " The heater now cools down, which takes a few minutes."
    if state in (STATE_COOLDOWN, STATE_FINAL_SHUTDOWN):
        return "The heater was already cooling down after a run. It finishes that by itself and then stays off."
    if auto_was_on:
        return "Thermostat off. The heater was not running, so it just stays off."
    if state == STATE_IDLE:
        return "Nothing to stop: the heater was already off."
    return "Stop sent."


def explain_pump(after: dict[str, Any]) -> str:
    """The water pump on its own."""
    minutes = after.get("pump_minutes")
    if isinstance(minutes, (int, float)):
        return f"Water pump running for {int(minutes)} min, without lighting the heater."
    return "Water pump running, without lighting the heater."


def clip(text: str) -> str:
    """Fit a state (255 characters at most); the full text goes in an attribute."""
    return text if len(text) <= MAX_STATE else text[: MAX_STATE - 1] + "…"
