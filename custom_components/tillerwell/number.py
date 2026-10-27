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
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime, UnitOfVolume, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    FUEL_RATE_LEVELS,
    MAX_FREEZE_TARGET,
    MAX_FUEL_FACTOR,
    MAX_FUEL_PRICE,
    MAX_FUEL_RATE,
    MAX_PREHEAT_COOLANT,
    MAX_PREHEAT_MINUTES,
    MAX_TANK_LITRES,
    MIN_FUEL_FACTOR,
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
    # Reads the value when it isn't a top-level status field.
    value_fn: Callable[[dict[str, Any]], Any] | None = None
    # Shown per litre in Home Assistant's own currency.
    per_litre_price: bool = False
    # Can't be set above the tank's size, once that is known.
    up_to_tank: bool = False


def _rate(level: str) -> Callable[[dict[str, Any]], Any]:
    return lambda status: (status.get("fuel_rates") or {}).get(level)


def _rates_with(level: str) -> Callable[[float, dict[str, Any]], dict[str, Any]]:
    """The box takes all four rates in one command: send the others back unchanged."""

    def params(value: float, status: dict[str, Any]) -> dict[str, Any]:
        rates = {k: v for k, v in (status.get("fuel_rates") or {}).items() if k in FUEL_RATE_LEVELS}
        return {**rates, level: value}

    return params


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
    # Fuel estimate (RoamOS 1.0011 on)
    HearthNumberDescription(
        key="fuel_price",
        translation_key="fuel_price",
        native_min_value=0,
        native_max_value=MAX_FUEL_PRICE,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        field="fuel_price",
        cmd="fuel_price",
        per_litre_price=True,
        params_fn=lambda value, _status: {"value": value},
    ),
    HearthNumberDescription(
        key="fuel_factor",
        translation_key="fuel_factor",
        native_min_value=MIN_FUEL_FACTOR,
        native_max_value=MAX_FUEL_FACTOR,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        field="fuel_factor",
        cmd="fuel_factor",
        params_fn=lambda value, _status: {"value": value},
    ),
    HearthNumberDescription(
        key="tank_capacity",
        translation_key="tank_capacity",
        device_class=NumberDeviceClass.VOLUME_STORAGE,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        native_min_value=1,
        native_max_value=MAX_TANK_LITRES,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        field="tank_capacity",
        cmd="tank_capacity",
        params_fn=lambda value, _status: {"value": value},
    ),
    # Shows the estimate of what is left; setting it tells the box what is really in the tank now.
    HearthNumberDescription(
        key="tank_litres",
        translation_key="tank_litres",
        device_class=NumberDeviceClass.VOLUME_STORAGE,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        native_min_value=0,
        native_max_value=MAX_TANK_LITRES,
        native_step=0.1,
        field="tank_litres",
        cmd="tank_level",
        up_to_tank=True,
        params_fn=lambda value, _status: {"value": value},
    ),
    *(
        HearthNumberDescription(
            key=f"fuel_rate_{level}",
            translation_key=f"fuel_rate_{level}",
            device_class=NumberDeviceClass.VOLUME_FLOW_RATE,
            native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_HOUR,
            native_min_value=0,
            native_max_value=MAX_FUEL_RATE,
            native_step=0.01,
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=False,
            field="fuel_rates",
            cmd="fuel_rates",
            value_fn=_rate(level),
            params_fn=_rates_with(level),
        )
        for level in FUEL_RATE_LEVELS
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
        if self.entity_description.value_fn:
            return self.entity_description.value_fn(self.status)
        return self.status.get(self.entity_description.field)

    @property
    def available(self) -> bool:
        """Unavailable on firmware that does not report this setting (fuel in tank: until it is first set)."""
        if self.entity_description.up_to_tank:
            return super().available and "tank_litres" in self.status
        return super().available and self.native_value is not None

    @property
    def native_max_value(self) -> float:
        """The tank's size for the fuel in it, once known."""
        if self.entity_description.up_to_tank and self.status.get("tank_capacity"):
            return float(self.status["tank_capacity"])
        return super().native_max_value

    @property
    def native_unit_of_measurement(self) -> str | None:
        """The description's unit, or Home Assistant's currency per litre for the fuel price."""
        if self.entity_description.per_litre_price:
            return f"{self.hass.config.currency}/L"
        return super().native_unit_of_measurement

    async def async_set_native_value(self, value: float) -> None:
        """Save a new setting on the box."""
        description = self.entity_description
        await self.coordinator.async_command(description.cmd, **description.params_fn(value, self.status))
