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
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import POWER_LEVELS, STATES
from .coordinator import HearthConfigEntry, HearthCoordinator, HearthData
from .entity import HearthEntity

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


def _status(key: str) -> Callable[[HearthData], Any]:
    return lambda data: data.status.get(key)


def _non_empty(key: str) -> Callable[[HearthData], Any]:
    return lambda data: data.status.get(key) or None


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
        key="next_timer",
        translation_key="next_timer",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_timer,
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
    # Extended telemetry: only filled while "Extended telemetry probing" is on and the heater answers (experimental).
    _temperature("ext_flame_temp", field="ext_flame_temp_c", extended=True, entity_registry_enabled_default=False),
    _temperature("ext_liquid_temp", field="ext_liquid_temp_c", extended=True, entity_registry_enabled_default=False),
    _temperature(
        "ext_overheat_temp", field="ext_overheat_temp_c", extended=True, entity_registry_enabled_default=False
    ),
    _temperature("ext_board_temp", field="ext_board_temp_c", extended=True, entity_registry_enabled_default=False),
    HearthSensorDescription(
        key="ext_fuel_pump_hz",
        translation_key="ext_fuel_pump_hz",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        extended=True,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_fuel_pump_hz"),
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
        return not self.entity_description.extended or bool(self.status.get("extended_active"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The heater's fault history, newest first, on its last-fault sensor."""
        if self.entity_description.key == "heater_last_fault":
            return {"faults": _heater_faults(self.coordinator.data)}
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
