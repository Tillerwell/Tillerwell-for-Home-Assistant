"""Run history rows (Hearth 1.82, spec §9.15) read the way the apps read them."""

from __future__ import annotations

from custom_components.tillerwell.coordinator import parse_run

ROW = [
    1795000033,
    2346,
    3328,
    5258,
    2,
    1,
    0,
    7,
    175,
    210,
    287,
    287,
    240,
    19,
    2,
    128,
    110,
    0,
    30,
    42,
    33,
    54,
    50,
    "1:0,3:240,4:246,5:2046",
]


def test_parse_run() -> None:
    run = parse_run(ROW)
    assert run is not None
    assert run["start"] == 1795000033
    assert run["duration_s"] == 2346
    assert run["litres"] == 0.333
    assert run["cost"] == 5.258
    assert run["started_by"] == "auto"
    assert run["ended"] == "top"
    assert run["fuel_from_pump"] and run["health"]
    assert run["room_start"] == 17.5 and run["room_end"] == 21.0
    assert run["flame_high"] == 287 and run["ignite_s"] == 240
    assert run["volts_glow"] == 11.0
    assert run["phases_min"] == {"starting": 4.0, "middle": 0.1, "high": 30.0, "cooling": 5.0}


def test_parse_run_rejects_junk() -> None:
    assert parse_run([1, 2, 3]) is None
    assert parse_run("x") is None
    assert parse_run([*ROW[:23], 5]) is None
    no_clock = [0, 60, 0, 0, 1, 0, 0, 0, None, None, None, None, 0, 0, 0, 0, 0, 0, 0, 0, None, 0, 0, "1:0"]
    run = parse_run(no_clock)
    assert run is not None and run["start"] is None and run["room_start"] is None and run["ignite_s"] is None
