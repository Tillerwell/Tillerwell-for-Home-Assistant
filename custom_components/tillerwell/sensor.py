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
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import STATES
from .coordinator import HearthConfigEntry, HearthCoordinator, HearthData
from .entity import HearthEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class HearthSensorDescription(SensorEntityDescription):
    """A sensor read out of the status or info document."""

    value_fn: Callable[[HearthData], Any]


def _status(key: str) -> Callable[[HearthData], Any]:
    return lambda data: data.status.get(key)


def _non_empty(key: str) -> Callable[[HearthData], Any]:
    return lambda data: data.status.get(key) or None


def _next_timer(data: HearthData) -> datetime | None:
    nxt = data.status.get("next_timer")
    if not isinstance(nxt, dict) or "start_epoch" not in nxt:
        return None
    return datetime.fromtimestamp(nxt["start_epoch"], tz=UTC)


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
    HearthSensorDescription(
        key="elapsed_min",
        translation_key="elapsed_min",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status("elapsed_min"),
    ),
    HearthSensorDescription(key="fault_name", translation_key="fault_name", value_fn=_non_empty("fault_name")),
    HearthSensorDescription(
        key="last_fault_name",
        translation_key="last_fault_name",
        entity_category=EntityCategory.DIAGNOSTIC,
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
        value_fn=_non_empty("timer_note"),
    ),
    HearthSensorDescription(
        key="auto_note",
        translation_key="auto_note",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_non_empty("auto_note"),
    ),
    HearthSensorDescription(
        key="prevent_freezing_note",
        translation_key="prevent_freezing_note",
        entity_category=EntityCategory.DIAGNOSTIC,
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
    # Extended telemetry: only filled while "Extended telemetry probing" is on (experimental).
    HearthSensorDescription(
        key="ext_voltage",
        translation_key="ext_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_voltage"),
    ),
    _temperature("ext_flame_temp", field="ext_flame_temp_c", entity_registry_enabled_default=False),
    _temperature("ext_liquid_temp", field="ext_liquid_temp_c", entity_registry_enabled_default=False),
    _temperature("ext_overheat_temp", field="ext_overheat_temp_c", entity_registry_enabled_default=False),
    _temperature("ext_board_temp", field="ext_board_temp_c", entity_registry_enabled_default=False),
    HearthSensorDescription(
        key="ext_fuel_pump_hz",
        translation_key="ext_fuel_pump_hz",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_fuel_pump_hz"),
    ),
    HearthSensorDescription(
        key="ext_output_pct",
        translation_key="ext_output_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_output_pct"),
    ),
    HearthSensorDescription(
        key="ext_fan_current_ma",
        translation_key="ext_fan_current_ma",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_fan_current_ma"),
    ),
    HearthSensorDescription(
        key="ext_mode_name",
        translation_key="ext_mode_name",
        entity_registry_enabled_default=False,
        value_fn=_status("ext_mode_name"),
    ),
    HearthSensorDescription(
        key="ext_fault_name",
        translation_key="ext_fault_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_status("ext_fault_name"),
    ),
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
        return value


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
