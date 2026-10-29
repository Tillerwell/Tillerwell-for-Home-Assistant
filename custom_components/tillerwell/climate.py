"""The heater as a thermostat, plus the timer and external-temperature services."""

from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback, async_get_current_platform

from .const import (
    ACTIVE_STATES,
    ATTR_TIMERS,
    MAX_AUTO_TEMP,
    MIN_AUTO_TEMP,
    SERVICE_SET_EXTERNAL_TEMPERATURE,
    SERVICE_SET_TIMERS,
)
from .coordinator import HearthConfigEntry, HearthCoordinator
from .entity import HearthEntity

PARALLEL_UPDATES = 1

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

TIMER_SCHEMA = vol.Schema(
    {
        vol.Optional("id"): cv.string,
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Required("mode"): vol.In(["preheat", "thermostat"]),
        vol.Required("start"): cv.time,
        vol.Required("duration_min"): vol.All(vol.Coerce(int), vol.Range(min=20, max=480)),
        vol.Optional("days", default=[]): vol.All(cv.ensure_list, [vol.In(WEEKDAYS)]),
        vol.Optional("date"): cv.date,
        vol.Optional("low_c", default=19): vol.All(vol.Coerce(int), vol.Range(min=0, max=30)),
        vol.Optional("high_c", default=22): vol.All(vol.Coerce(int), vol.Range(min=0, max=30)),
    }
)


def timer_to_wire(timer: dict[str, Any]) -> dict[str, Any]:
    """Turn a service-friendly timer (start "06:30", days ["mon"]) into the box's format."""
    start = timer["start"]
    date = timer.get("date")
    days = sorted({WEEKDAYS.index(day) + 1 for day in timer["days"]})
    if date is None and not days:
        raise ServiceValidationError("Each timer needs either days or a date")
    return {
        "id": timer.get("id") or str(uuid.uuid4()).upper(),
        "enabled": timer["enabled"],
        "mode": timer["mode"],
        "start_minute": start.hour * 60 + start.minute,
        "duration_min": timer["duration_min"],
        "days": [] if date else days,
        "date": date.isoformat() if date else None,
        "low_c": timer["low_c"],
        "high_c": timer["high_c"],
    }


async def async_setup_entry(
    hass: HomeAssistant, entry: HearthConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the thermostat and register the entity services."""
    async_add_entities([HearthClimate(entry.runtime_data)])

    platform = async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_TIMERS,
        {vol.Required(ATTR_TIMERS): vol.All(cv.ensure_list, [TIMER_SCHEMA])},
        "async_set_timers",
    )
    platform.async_register_entity_service(
        SERVICE_SET_EXTERNAL_TEMPERATURE,
        {vol.Required(ATTR_TEMPERATURE): vol.Coerce(float)},
        "async_set_external_temperature",
    )


class HearthClimate(HearthEntity, ClimateEntity):
    """The thermostat: Heat arms the box's Auto mode, Off stops the heater.

    The box starts the heater at `low` minus its start hysteresis and stops it at `high`
    plus its stop hysteresis, so the range is the comfort band, not the switching points.
    """

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = MIN_AUTO_TEMP
    _attr_max_temp = MAX_AUTO_TEMP
    _attr_target_temperature_step = 1

    def __init__(self, coordinator: HearthCoordinator) -> None:
        super().__init__(coordinator, "thermostat")
        # The main entity of the device takes the device's name.
        self._attr_translation_key = None

    @property
    def _running(self) -> bool:
        return self.status.get("state") in ACTIVE_STATES

    @property
    def hvac_mode(self) -> HVACMode:
        """Heat while Auto is armed or the heater is running, otherwise Off."""
        return HVACMode.HEAT if self.status.get("auto_enabled") or self._running else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        """What the burner is doing."""
        if self.status.get("burner_active"):
            return HVACAction.HEATING
        return HVACAction.IDLE if self.hvac_mode == HVACMode.HEAT else HVACAction.OFF

    @property
    def current_temperature(self) -> float | None:
        """The temperature the box's thermostat is using (external sensor if set, else the controller's)."""
        value = self.status.get("effective_temp")
        return self.status.get("cabin_temp") if value is None else value

    @property
    def precision(self) -> float:
        """Tenths from a Home Assistant sensor; the controller only reports whole degrees."""
        return PRECISION_TENTHS if self.status.get("external_temp_active") else PRECISION_WHOLE

    @property
    def target_temperature_low(self) -> float | None:
        """Bottom of the comfort band."""
        return self.status.get("auto_low")

    @property
    def target_temperature_high(self) -> float | None:
        """Top of the comfort band."""
        return self.status.get("auto_high")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the comfort band."""
        low = kwargs.get("target_temp_low", self.target_temperature_low)
        high = kwargs.get("target_temp_high", self.target_temperature_high)
        if low is None or high is None:
            raise ServiceValidationError("Give both a low and a high temperature")
        if low >= high:
            raise ServiceValidationError("The low temperature must be below the high temperature")
        await self.coordinator.async_command("auto_range", low=low, high=high)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Heat arms Auto; Off switches Auto off and stops the heater."""
        if hvac_mode == HVACMode.HEAT:
            await self.coordinator.async_command("auto", enabled=True)
        else:
            await self.coordinator.async_stop()

    async def async_set_timers(self, timers: list[dict[str, Any]]) -> None:
        """Entity service: replace the box's whole timer list."""
        await self.coordinator.async_set_timers([timer_to_wire(timer) for timer in timers])

    async def async_set_external_temperature(self, temperature: float) -> None:
        """Entity service: tell the box the room temperature (used for 90 s)."""
        await self.coordinator.async_command("external_temp", value=temperature)
