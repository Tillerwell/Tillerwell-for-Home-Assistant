"""Numbers: the box's saved settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    MAX_FREEZE_TARGET,
    MAX_PREHEAT_COOLANT,
    MAX_PREHEAT_MINUTES,
    MIN_PREHEAT_COOLANT,
    MIN_PREHEAT_MINUTES,
)
from .coordinator import HearthConfigEntry, HearthCoordinator
from .entity import HearthEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class HearthNumberDescription(NumberEntityDescription):
    """A number backed by a status field and a command."""

    field: str
    # Builds the command parameters from the new value and the current status.
    params_fn: Callable[[float, dict[str, Any]], dict[str, Any]]
    cmd: str


NUMBERS: tuple[HearthNumberDescription, ...] = (
    HearthNumberDescription(
        key="preheat_minutes",
        translation_key="preheat_minutes",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=MIN_PREHEAT_MINUTES,
        native_max_value=MAX_PREHEAT_MINUTES,
        native_step=1,
        field="preheat_minutes",
        cmd="preheat_minutes",
        params_fn=lambda value, _status: {"value": int(value)},
    ),
    HearthNumberDescription(
        key="preheat_temp",
        translation_key="preheat_temp",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=MIN_PREHEAT_COOLANT,
        native_max_value=MAX_PREHEAT_COOLANT,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        field="preheat_temp",
        cmd="preheat_temp",
        params_fn=lambda value, _status: {"value": value},
    ),
    HearthNumberDescription(
        key="prevent_freezing_target",
        translation_key="prevent_freezing_target",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0,
        native_max_value=MAX_FREEZE_TARGET,
        native_step=0.5,
        entity_category=EntityCategory.CONFIG,
        field="prevent_freezing_target",
        cmd="freeze_target",
        params_fn=lambda value, _status: {"value": value},
    ),
    # The box takes both hysteresis values in one command, so each number sends the other one back too.
    HearthNumberDescription(
        key="hyst_min",
        translation_key="hyst_min",
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=2,
        native_max_value=7,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        field="hyst_min",
        cmd="hysteresis",
        params_fn=lambda value, status: {"min": value, "max": status.get("hyst_max", 1)},
    ),
    HearthNumberDescription(
        key="hyst_max",
        translation_key="hyst_max",
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=1,
        native_max_value=3,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        field="hyst_max",
        cmd="hysteresis",
        params_fn=lambda value, status: {"min": status.get("hyst_min", 2), "max": value},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HearthConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the numbers."""
    coordinator = entry.runtime_data
    async_add_entities(HearthNumber(coordinator, description) for description in NUMBERS)


class HearthNumber(HearthEntity, NumberEntity):
    """A setting stored on the box."""

    entity_description: HearthNumberDescription
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: HearthCoordinator, description: HearthNumberDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Current setting."""
        return self.status.get(self.entity_description.field)

    async def async_set_native_value(self, value: float) -> None:
        """Save a new setting on the box."""
        description = self.entity_description
        await self.coordinator.async_command(description.cmd, **description.params_fn(value, self.status))
