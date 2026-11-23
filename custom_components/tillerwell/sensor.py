"""Sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import FUEL_LEVELS, POWER_LEVELS, STATES
from .coordinator import HearthConfigEntry, HearthCoordinator, HearthData
from .entity import HearthEntity
from .reasons import clip

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class HearthSensorDescription(SensorEntityDescription):
    """A sensor read out of the status or info document."""

    value_fn: Callable[[HearthData], Any]
    # Money: shown in Home Assistant's own currency, which is what the box's fuel price is entered in.
    currency: bool = False
    # Shown when the box has nothing to report (no fault, no note), rather than Unknown.
    empty_value: str | None = None
    # Only filled by extended telemetry: unavailable until the heater actually sends it.
    extended: bool = False
    # Unavailable on firmware whose status does not carry this field at all.
    required_field: str | None = None
    # Firmware 1.42 heater_info: unavailable until the heater has answered a Read heater info.
    info_section: str | None = None
    # Today's fuel by power level (firmware 1.64): unavailable until the box has sent one.
    fuel_day: bool = False
    # Run history (firmware 1.82): "last" needs a run, "health" a run with the heater's own readings.
    run: str | None = None


def _fuel_level(level: str, field: str) -> Callable[[HearthData], Any]:
    """Today's litres or cost at one power level, from GET /api/fuel/day (Hearth 1.64)."""

    def read(data: HearthData) -> Any:
        day = data.fuel_day or {}
        levels = day.get("levels") or []
        entry = next((e for e in levels if e.get("level") == level), None)
        if entry is None:
            return None
        if field != "cost":
            return entry.get(field)
        # its litres at the day's cost per litre: firmware 1.64 and 1.65 put the whole day's cost on the levels seen
        # on the day of the update, so it is worked out here
        seen = sum(e.get("litres") or 0 for e in levels)
        total = max(day.get("litres") or 0, seen)
        return round((day.get("cost") or 0) * (entry.get("litres") or 0) / total, 3) if total > 0 else 0

    return read


def _status(key: str) -> Callable[[HearthData], Any]:
    return lambda data: data.status.get(key)


def _non_empty(key: str) -> Callable[[HearthData], Any]:
    return lambda data: data.status.get(key) or None


def _last_run(key: str) -> Callable[[HearthData], Any]:
    return lambda data: (data.last_run or {}).get(key)


def _health_run(key: str) -> Callable[[HearthData], Any]:
    return lambda data: (data.last_health_run or {}).get(key)


def _last_run_end(data: HearthData) -> datetime | None:
    """Hearth 1.82: when the last run finished (its start plus its length)."""
    run = data.last_run or {}
    start = run.get("start")
    return datetime.fromtimestamp(start + run.get("duration_s", 0), tz=UTC) if start else None


def _since(key: str) -> Callable[[HearthData], Any]:
    """Hearth 1.84: use since the last service (or since set-up, with none recorded)."""
    return lambda data: ((data.health or {}).get("since_service") or {}).get(key)


def _last_service(data: HearthData) -> datetime | None:
    at = ((data.health or {}).get("since_service") or {}).get("at")
    return datetime.fromtimestamp(at, tz=UTC) if isinstance(at, int | float) and at > 0 else None


def _last_ran(data: HearthData) -> datetime | None:
    at = (data.health or {}).get("last_run_at")
    return datetime.fromtimestamp(at, tz=UTC) if isinstance(at, int | float) and at > 0 else None


def _service_due(data: HearthData) -> Any:
    """Hearth 1.85: how many service items are due (or probably due)."""
    due = data.status.get("service_due")
    return due.get("due") if isinstance(due, dict) else None


def _health_state(data: HearthData) -> str | None:
    health = data.status.get("heater_health")
    return health.get("state") if isinstance(health, dict) else None


def _fault_held_name(data: HearthData) -> str | None:
    """Firmware 1.80: the fault the box is holding until it is cleared, with how often it happened."""
    held = data.status.get("fault_held")
    if not isinstance(held, dict):
        return None
    name = held.get("name") or f"Fault {held.get('code')}"
    count = held.get("count") or 1
    return f"{name} ({count} times)" if count > 1 else name


def _fault_name(data: HearthData) -> str | None:
    """The fault, "No fault" once the heater has said code 0, unknown until it has said anything."""
    if name := data.status.get("fault_name"):
        return name
    return "No fault" if data.status.get("fault") == 0 else None


def _next_timer(data: HearthData) -> datetime | None:
    nxt = data.status.get("next_timer")
    if not isinstance(nxt, dict) or "start_epoch" not in nxt:
        return None
    return datetime.fromtimestamp(nxt["start_epoch"], tz=UTC)


def _run_ends(data: HearthData) -> datetime | None:
    """Hearth 1.74: when the run going ends, a running timer's end or the thermostat's run time."""
    at = data.status.get("run_ends_at")
    return datetime.fromtimestamp(at, tz=UTC) if isinstance(at, int | float) else None


def _supply_voltage(data: HearthData) -> Any:
    """The box's own reading of the supply, else the heater's (extended telemetry) on a box without the divider."""
    value = data.status.get("supply_voltage")
    return value if value is not None else data.status.get("ext_voltage")


def _thermo(key: str, scale: float = 1) -> Callable[[HearthData], Any]:
    """A value from the Heater Care object (firmware 1.47), seconds scaled to minutes where asked."""

    def value(data: HearthData) -> Any:
        thermo = data.status.get("thermo")
        raw = thermo.get(key) if isinstance(thermo, dict) else None
        return round(raw / scale, 1) if isinstance(raw, (int, float)) and scale != 1 else raw

    return value


def _care_temperature(key: str, **kwargs: Any) -> HearthSensorDescription:
    return HearthSensorDescription(
        key=f"heater_care_{key}",
        translation_key=f"heater_care_{key}",
        device_class=kwargs.pop("device_class", SensorDeviceClass.TEMPERATURE),
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        required_field="thermo",
        value_fn=_thermo(key),
        **kwargs,
    )


def _care_minutes(key: str, **kwargs: Any) -> HearthSensorDescription:
    return HearthSensorDescription(
        key=f"heater_care_{key}",
        translation_key=f"heater_care_{key}",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        required_field="thermo",
        value_fn=_thermo(f"{key}_s", 60),
        **kwargs,
    )


def _care_text(key: str, **kwargs: Any) -> HearthSensorDescription:
    return HearthSensorDescription(
        key=f"heater_care_{key}",
        translation_key=f"heater_care_{key}",
        required_field="thermo",
        empty_value="None",
        value_fn=_thermo(key),
        **kwargs,
    )


def _heater_info(data: HearthData) -> dict[str, Any]:
    info = data.status.get("heater_info")
    return info if isinstance(info, dict) else {}


def _info(section: str, key: str, scale: float = 1) -> Callable[[HearthData], Any]:
    """A value from heater_info's settings or report, scaled (seconds to hours)."""

    def value(data: HearthData) -> Any:
        part = _heater_info(data).get(section)
        raw = part.get(key) if isinstance(part, dict) else None
        return raw / scale if isinstance(raw, (int, float)) and scale != 1 else raw

    return value


def _iso(epoch: Any) -> str | None:
    """An epoch from the box as an ISO date and time (UTC), or None."""
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _heater_faults(data: HearthData) -> list[dict[str, Any]]:
    report = _heater_info(data).get("report")
    faults = report.get("faults") if isinstance(report, dict) else None
    return faults if isinstance(faults, list) else []


def _last_heater_fault(data: HearthData) -> str | None:
    """The newest entry of the heater's own fault history (firmware 1.43); the whole list is an attribute."""
    faults = _heater_faults(data)
    return faults[0].get("name") if faults and isinstance(faults[0], dict) else None


def _report_count(key: str, **kwargs: Any) -> HearthSensorDescription:
    return HearthSensorDescription(
        key=f"heater_{key}",
        translation_key=f"heater_{key}",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        info_section="report",
        value_fn=_info("report", key),
        **kwargs,
    )


def _report_hours(key: str, **kwargs: Any) -> HearthSensorDescription:
    return HearthSensorDescription(
        key=f"heater_{key}",
        translation_key=f"heater_{key}",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        info_section="report",
        value_fn=_info("report", key, 3600),
        **kwargs,
    )


def _temperature(key: str, **kwargs: Any) -> HearthSensorDescription:
    return HearthSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status(kwargs.pop("field", key)),
        **kwargs,
    )


SENSORS: tuple[HearthSensorDescription, ...] = (
    HearthSensorDescription(
        key="state",
        translation_key="state",
        device_class=SensorDeviceClass.ENUM,
        options=STATES,
        value_fn=_status("state"),
    ),
    _temperature("coolant_temp"),
    _temperature("cabin_temp"),
    _temperature("effective_temp"),
    _temperature("heater_temp"),
    HearthSensorDescription(
        key="elapsed_min",
        translation_key="elapsed_min",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status("elapsed_min"),
    ),
    HearthSensorDescription(key="fault_name", translation_key="fault_name", value_fn=_fault_name),
    HearthSensorDescription(
        key="last_fault_name",
        translation_key="last_fault_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        empty_value="None",
        value_fn=_status("last_fault_name"),
    ),
    HearthSensorDescription(
        key="fault_held_name",
        translation_key="fault_held_name",
        empty_value="None",
        required_field="fault_held",
        value_fn=_fault_held_name,
    ),
    HearthSensorDescription(
        key="next_timer",
        translation_key="next_timer",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_timer,
    ),
    HearthSensorDescription(
        key="run_ends",
        translation_key="run_ends",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_run_ends,
    ),
    HearthSensorDescription(
        key="timer_note",
        translation_key="timer_note",
        entity_category=EntityCategory.DIAGNOSTIC,
        empty_value="None",
        value_fn=_non_empty("timer_note"),
    ),
    # The cabin temperature the thermostat's next start happens at: the bottom of the working range, or from firmware
    # 1.60 up to 0.3 C below the top on the room sensor (how it was worked out is in its attributes). RoamOS 1.0038 on.
    HearthSensorDescription(
        key="auto_start_at",
        translation_key="auto_start_at",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        required_field="auto_start_at",
        value_fn=_status("auto_start_at"),
    ),
    # Heater Care (firmware 1.47): the working range the thermostat runs on, the run locks, and what it learned.
    _care_temperature("work_low"),
    _care_temperature("work_high", entity_registry_enabled_default=False),
    _care_temperature("learned_width", device_class=None, entity_category=EntityCategory.DIAGNOSTIC),
    _care_temperature("ceiling", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    _care_minutes("min_run_left"),
    _care_minutes("min_off_left"),
    _care_minutes("warmup_typical", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    _care_minutes("cool_typical", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    HearthSensorDescription(
        key="heater_care_rise_rate",
        translation_key="heater_care_rise_rate",
        native_unit_of_measurement="°C/min",
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        required_field="thermo",
        value_fn=_thermo("rise_rate"),
    ),
    _care_temperature("dip_below", device_class=None, entity_category=EntityCategory.DIAGNOSTIC),
    _care_text("holding"),
    _care_text("reason", entity_category=EntityCategory.DIAGNOSTIC),
    _care_text("sensor", entity_category=EntityCategory.DIAGNOSTIC),
    _care_text("at_limit", entity_category=EntityCategory.DIAGNOSTIC),
    _care_text("last_run_end", entity_category=EntityCategory.DIAGNOSTIC),
    HearthSensorDescription(
        key="heater_care_early_stop_unseen",
        translation_key="heater_care_early_stop_unseen",
        entity_category=EntityCategory.DIAGNOSTIC,
        required_field="thermo",
        value_fn=_thermo("early_stop_unseen"),
    ),
    HearthSensorDescription(
        key="auto_note",
        translation_key="auto_note",
        entity_category=EntityCategory.DIAGNOSTIC,
        empty_value="None",
        value_fn=_non_empty("auto_note"),
    ),
    HearthSensorDescription(
        key="prevent_freezing_note",
        translation_key="prevent_freezing_note",
        entity_category=EntityCategory.DIAGNOSTIC,
        empty_value="None",
        value_fn=_non_empty("prevent_freezing_note"),
    ),
    HearthSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.info.get("rssi"),
    ),
    # What the heater is doing, from its status frame (any firmware), and the box's fuel estimate
    # (RoamOS 1.0011 on): time at each power level x a litres-per-hour rate x the calibration factor.
    HearthSensorDescription(
        key="power_level",
        translation_key="power_level",
        device_class=SensorDeviceClass.ENUM,
        options=POWER_LEVELS,
        value_fn=_status("power_level"),
    ),
    HearthSensorDescription(key="mode_name", translation_key="mode_name", value_fn=_status("mode_name")),
    HearthSensorDescription(
        key="fuel_rate",
        translation_key="fuel_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_status("fuel_rate_lph"),
    ),
    HearthSensorDescription(
        key="fuel_used",
        translation_key="fuel_used",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_status("fuel_litres_total"),
    ),
    HearthSensorDescription(
        key="fuel_used_today",
        translation_key="fuel_used_today",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_status("fuel_litres_today"),
    ),
    HearthSensorDescription(
        key="fuel_cost",
        translation_key="fuel_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        currency=True,
        value_fn=_status("fuel_cost_total"),
    ),
    HearthSensorDescription(
        key="fuel_cost_today",
        translation_key="fuel_cost_today",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        currency=True,
        value_fn=_status("fuel_cost_today"),
    ),
    # Today's fuel by power level (Hearth firmware 1.64): litres and cost at starting, low, middle and high.
    *[
        HearthSensorDescription(
            key=f"fuel_today_{level}",
            translation_key=f"fuel_today_{level}",
            device_class=SensorDeviceClass.VOLUME,
            native_unit_of_measurement=UnitOfVolume.LITERS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            suggested_display_precision=2,
            value_fn=_fuel_level(level, "litres"),
            fuel_day=True,
        )
        for level in FUEL_LEVELS
    ],
    *[
        HearthSensorDescription(
            key=f"fuel_cost_today_{level}",
            translation_key=f"fuel_cost_today_{level}",
            device_class=SensorDeviceClass.MONETARY,
            state_class=SensorStateClass.TOTAL,
            suggested_display_precision=2,
            currency=True,
            value_fn=_fuel_level(level, "cost"),
            fuel_day=True,
        )
        for level in FUEL_LEVELS
    ],
    # Battery-use estimate (Hearth firmware 1.62): time in each phase x Autoterm's power figures / the supply voltage.
    HearthSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_status("battery_w"),
    ),
    HearthSensorDescription(
        key="battery_used",
        translation_key="battery_used",
        native_unit_of_measurement="Ah",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_status("battery_ah_total"),
    ),
    HearthSensorDescription(
        key="battery_used_today",
        translation_key="battery_used_today",
        native_unit_of_measurement="Ah",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_status("battery_ah_today"),
    ),
    HearthSensorDescription(
        key="battery_used_run",
        translation_key="battery_used_run",
        native_unit_of_measurement="Ah",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_status("battery_ah_run"),
    ),
    HearthSensorDescription(
        key="burn_hours",
        translation_key="burn_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=_status("burn_hours_total"),
    ),
    HearthSensorDescription(
        key="starts",
        translation_key="starts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_status("starts_total"),
    ),
    HearthSensorDescription(
        key="tank_pct",
        translation_key="tank_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_status("tank_pct"),
    ),
    *(
        HearthSensorDescription(
            key=f"hours_{level}",
            translation_key=f"hours_{level}",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.HOURS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            suggested_display_precision=1,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_status(f"hours_{level}"),
        )
        for level in ("low", "middle", "high")
    ),
    # Link health
    *(
        HearthSensorDescription(
            key=key,
            translation_key=key,
            state_class=SensorStateClass.TOTAL_INCREASING,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=_status(key),
        )
        for key in ("bad_crc_heater", "bad_crc_controller", "inject_fail")
    ),
    HearthSensorDescription(
        key="last_command",
        translation_key="last_command",
        entity_category=EntityCategory.DIAGNOSTIC,
        empty_value="None",
        value_fn=_status("last_command"),
    ),
    HearthSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_status("uptime_s"),
    ),
    HearthSensorDescription(
        key="supply_voltage",
        translation_key="supply_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_supply_voltage,
    ),
    # Extended telemetry: filled while the heater sends it. From firmware 1.82 the box keeps it on by itself (its
    # "Detailed heater readings" switch), so the main readings are on by default.
    _temperature("ext_flame_temp", field="ext_flame_temp_c", extended=True),
    _temperature("ext_liquid_temp", field="ext_liquid_temp_c", extended=True),
    _temperature("ext_overheat_temp", field="ext_overheat_temp_c", extended=True),
    _temperature("ext_board_temp", field="ext_board_temp_c", extended=True, entity_registry_enabled_default=False),
    HearthSensorDescription(
        key="ext_fuel_pump_hz",
        translation_key="ext_fuel_pump_hz",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        extended=True,
        value_fn=_status("ext_fuel_pump_hz"),
    ),
    # The fan's target and measured speed (raw units): a growing gap means a worn fan or blocked air.
    HearthSensorDescription(
        key="ext_fan_target",
        translation_key="ext_fan_target",
        state_class=SensorStateClass.MEASUREMENT,
        extended=True,
        value_fn=_status("ext_defined_rev"),
    ),
    HearthSensorDescription(
        key="ext_fan_speed",
        translation_key="ext_fan_speed",
        state_class=SensorStateClass.MEASUREMENT,
        extended=True,
        value_fn=_status("ext_measured_rev"),
    ),
    # Firmware 1.82: the health check's verdict, and the last run from the run history.
    HearthSensorDescription(
        key="heater_health",
        translation_key="heater_health",
        device_class=SensorDeviceClass.ENUM,
        options=["learning", "ok", "watch", "service"],
        required_field="heater_health",
        value_fn=_health_state,
    ),
    HearthSensorDescription(
        key="service_items_due",
        translation_key="service_items_due",
        state_class=SensorStateClass.MEASUREMENT,
        required_field="service_due",
        value_fn=_service_due,
    ),
    HearthSensorDescription(
        key="last_service",
        translation_key="last_service",
        device_class=SensorDeviceClass.TIMESTAMP,
        run="service",
        value_fn=_last_service,
    ),
    HearthSensorDescription(
        key="burn_hours_since_service",
        translation_key="burn_hours_since_service",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        run="service",
        value_fn=_since("burn_hours"),
    ),
    HearthSensorDescription(
        key="starts_since_service",
        translation_key="starts_since_service",
        run="service",
        value_fn=_since("starts"),
    ),
    HearthSensorDescription(
        key="last_ran",
        translation_key="last_ran",
        device_class=SensorDeviceClass.TIMESTAMP,
        run="service",
        value_fn=_last_ran,
    ),
    HearthSensorDescription(
        key="last_run",
        translation_key="last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        run="last",
        value_fn=_last_run_end,
    ),
    HearthSensorDescription(
        key="last_run_fuel",
        translation_key="last_run_fuel",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=2,
        run="last",
        value_fn=_last_run("litres"),
    ),
    HearthSensorDescription(
        key="last_run_cost",
        translation_key="last_run_cost",
        device_class=SensorDeviceClass.MONETARY,
        currency=True,
        suggested_display_precision=2,
        run="last",
        value_fn=_last_run("cost"),
    ),
    HearthSensorDescription(
        key="ignition_time",
        translation_key="ignition_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        run="health",
        value_fn=_health_run("ignite_s"),
    ),
    HearthSensorDescription(
        key="glow_voltage",
        translation_key="glow_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        run="health",
        value_fn=_health_run("volts_glow"),
    ),
    HearthSensorDescription(
        key="flame_at_high",
        translation_key="flame_at_high",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        run="health",
        value_fn=_health_run("flame_high"),
    ),
    HearthSensorDescription(
        key="ext_output_pct",
        translation_key="ext_output_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        extended=True,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_output_pct"),
    ),
    HearthSensorDescription(
        key="ext_fan_current_ma",
        translation_key="ext_fan_current_ma",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        extended=True,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_fan_current_ma"),
    ),
    HearthSensorDescription(
        key="ext_mode_name",
        translation_key="ext_mode_name",
        extended=True,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_mode_name"),
    ),
    HearthSensorDescription(
        key="ext_fault_name",
        translation_key="ext_fault_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        extended=True,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_fault_name"),
    ),
    # Read from the heater itself (firmware 1.42, Read heater info). The report's layout comes from Autoterm's test
    # tool and is not yet checked against a real heater.
    HearthSensorDescription(
        key="heater_wait_temp",
        translation_key="heater_wait_temp",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        info_section="settings",
        value_fn=_info("settings", "wait_temp"),
    ),
    HearthSensorDescription(
        key="heater_version",
        translation_key="heater_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        info_section="version",
        value_fn=lambda data: _heater_info(data).get("version"),
    ),
    HearthSensorDescription(
        key="heater_last_fault",
        translation_key="heater_last_fault",
        entity_category=EntityCategory.DIAGNOSTIC,
        empty_value="None",
        info_section="report",
        value_fn=_last_heater_fault,
    ),
    _report_count("starts"),
    _report_count("second_attempt", entity_registry_enabled_default=False),
    _report_count("no_ignition"),
    _report_count("power_cuts", entity_registry_enabled_default=False),
    _report_hours("total_s"),
    _report_hours("high_s", entity_registry_enabled_default=False),
    _report_hours("middle_s", entity_registry_enabled_default=False),
    _report_hours("low_s", entity_registry_enabled_default=False),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HearthConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [HearthSensor(coordinator, description) for description in SENSORS]
    entities.append(HearthTimersSensor(coordinator))
    entities.append(HearthLastActionSensor(coordinator))
    async_add_entities(entities)


class HearthSensor(HearthEntity, SensorEntity):
    """One value from the box."""

    entity_description: HearthSensorDescription

    def __init__(self, coordinator: HearthCoordinator, description: HearthSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """The current value, None when the box has not reported it."""
        value = self.entity_description.value_fn(self.coordinator.data)
        if self.entity_description.key == "state" and value not in STATES:
            return None
        if self.entity_description.key == "power_level" and value not in POWER_LEVELS:
            return None
        if value in (None, "") and self.entity_description.empty_value is not None:
            return self.entity_description.empty_value
        return value

    @property
    def available(self) -> bool:
        """Unavailable, not unknown, until extended telemetry arrives, or where older firmware lacks the field."""
        if not super().available:
            return False
        if self.entity_description.required_field and self.entity_description.required_field not in self.status:
            return False
        if (
            self.entity_description.info_section
            and _heater_info(self.coordinator.data).get(self.entity_description.info_section) is None
        ):
            return False
        if self.entity_description.fuel_day and not self.coordinator.data.fuel_day:
            return False
        if self.entity_description.run == "last" and not self.coordinator.data.last_run:
            return False
        if self.entity_description.run == "health" and not self.coordinator.data.last_health_run:
            return False
        if self.entity_description.run == "service" and not self.coordinator.data.health:
            return False
        return not self.entity_description.extended or bool(self.status.get("extended_active"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The heater's fault history, newest first, on its last-fault sensor."""
        if self.entity_description.key == "heater_health":
            health = self.status.get("heater_health")
            if not isinstance(health, dict):
                return None
            attrs = {k: health.get(k) for k in ("reason", "runs", "learned")}
            # Hearth 1.84: each part, its normal reading against now and its state
            for part in (self.coordinator.data.health or {}).get("parts") or []:
                if isinstance(part, dict) and part.get("part"):
                    attrs[part["part"]] = {k: part.get(k) for k in ("state", "normal", "now", "unit")}
            return attrs
        if self.entity_description.key == "service_items_due":
            # Hearth 1.85: each item, its state, when it was last done and its interval; the pre-season prompt
            due = self.status.get("service_due")
            attrs: dict[str, Any] = {"season_prompt": bool(due.get("season")) if isinstance(due, dict) else False}
            if isinstance(due, dict):
                attrs["coming_up"] = due.get("soon")
                # Hearth 1.89: "Remind me later" from the apps or website hides the reminder until then
                snz = due.get("snoozed_until")
                snoozed = isinstance(snz, int | float) and snz > 0
                attrs["reminder_snoozed_until"] = datetime.fromtimestamp(snz, tz=UTC).isoformat() if snoozed else None
            for item in (self.coordinator.data.service or {}).get("items") or []:
                if isinstance(item, dict) and item.get("item"):
                    attrs[item["item"]] = {
                        k: item.get(k)
                        for k in ("state", "last_action", "hours_since", "months_since", "hours", "months")
                    }
            return attrs
        if self.entity_description.key == "last_run" and self.coordinator.data.last_run:
            run = dict(self.coordinator.data.last_run)
            run["start"] = _iso(run.get("start"))
            return run
        if self.entity_description.key == "heater_last_fault":
            # firmware 1.70: each fault's seen_at (epoch) is when the box first read it, as the heater keeps no
            # dates; "seen" gives it as a date and time, and read_at is when the heater was last read
            faults = [
                {**f, "seen": _iso(f.get("seen_at"))} if isinstance(f, dict) and f.get("seen_at") else f
                for f in _heater_faults(self.coordinator.data)
            ]
            attrs: dict[str, Any] = {"faults": faults}
            read_at = _heater_info(self.coordinator.data).get("read_at")
            if read_at:
                attrs["read_at"] = _iso(read_at)
            return attrs
        if self.entity_description.key == "heater_care_early_stop_unseen":
            thermo = self.status.get("thermo")
            last = thermo.get("last_early_stop") if isinstance(thermo, dict) else None
            return {"last_early_stop": last} if isinstance(last, dict) else None
        if self.entity_description.key == "auto_start_at":
            # firmware 1.60: thermo.early (start_at, est, net_rate, climb, busy_w)
            thermo = self.status.get("thermo")
            early = thermo.get("early") if isinstance(thermo, dict) else None
            if not isinstance(early, dict):
                return None
            return {f"early_{k}": early.get(k) for k in ("est", "net_rate", "climb", "busy_w") if k in early}
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """The description's unit, or Home Assistant's currency for money."""
        if self.entity_description.currency:
            return self.hass.config.currency
        return super().native_unit_of_measurement


class HearthTimersSensor(HearthEntity, SensorEntity):
    """How many timers are saved on the box, with the list itself as an attribute."""

    _unrecorded_attributes = frozenset({"timers"})
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HearthCoordinator) -> None:
        super().__init__(coordinator, "timers")

    @property
    def native_value(self) -> int:
        """Number of saved timers."""
        return len(self.coordinator.data.timers)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The raw list, in the shape the set_timers service takes back (see the README)."""
        return {"timers": self.coordinator.data.timers}


class HearthLastActionSensor(HearthEntity, SensorEntity):
    """The last start or stop asked for from Home Assistant, and why it did or did not happen.

    Its state is the explanation: the box's own reason when it refused (Heater Care's minimum run and rest), or what
    the heater does next when it accepted (where the thermostat starts and stops, how long a rest or run has left).
    Unknown until something is started or stopped after Home Assistant starts.
    """

    _unrecorded_attributes = frozenset({"reason"})

    def __init__(self, coordinator: HearthCoordinator) -> None:
        super().__init__(coordinator, "last_action")

    @property
    def native_value(self) -> str | None:
        """Why the last start or stop did, or did not, happen."""
        last = self.coordinator.last_action
        return clip(last["reason"]) if last else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What was asked, how it went, and when."""
        last = self.coordinator.last_action
        if not last:
            return {}
        return {
            "action": last["action"],
            "result": last["result"],
            "heater_care": last["care"],
            "at": last["at"].isoformat(),
            "reason": last["reason"],
        }
