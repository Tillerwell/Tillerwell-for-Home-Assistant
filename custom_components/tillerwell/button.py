"""Buttons: one-shot actions."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import HearthError
from .coordinator import HearthConfigEntry, HearthCoordinator
from .entity import HearthEntity
from .reasons import ACTION_PREHEAT, ACTION_PUMP, ACTION_STOP, ACTION_THERMOSTAT_ON

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class HearthButtonDescription(ButtonEntityDescription):
    """A button that runs a coroutine against the coordinator."""

    press_fn: Callable[[HearthCoordinator], Coroutine[Any, Any, None]]


async def _start_preheat(coordinator: HearthCoordinator) -> None:
    minutes = int(coordinator.data.status.get("preheat_minutes") or 30)

    async def run() -> None:
        if coordinator.data.status.get("auto_enabled"):
            await coordinator.async_command("auto", enabled=False)
        await coordinator.async_command("start_preheat", minutes=minutes)

    await coordinator.async_act(ACTION_PREHEAT, run, minutes=minutes)


async def _start_thermostat(coordinator: HearthCoordinator) -> None:
    await coordinator.async_act(ACTION_THERMOSTAT_ON, lambda: coordinator.async_command("start_thermostat"))


async def _start_pump(coordinator: HearthCoordinator) -> None:
    await coordinator.async_act(ACTION_PUMP, lambda: coordinator.async_command("start_pump"))


async def _stop(coordinator: HearthCoordinator) -> None:
    """Stop whatever is running. The heater can still read idle while it primes or tests its fan, so the stop always
    goes out (the box takes it in any state); Auto goes off first or it would start the heater again. Inside Heater
    Care's minimum run the thermostat still goes off and the box stops the run once it is up, as for thermostat off,
    rather than reporting the whole press as refused."""
    await coordinator.async_act(ACTION_STOP, lambda: coordinator.async_stop(always=True))


async def _prime_fuel(coordinator: HearthCoordinator) -> None:
    await coordinator.async_command("fuel_prime")


async def _fan_test(coordinator: HearthCoordinator) -> None:
    await coordinator.async_command("fan_test")


async def _tank_filled(coordinator: HearthCoordinator) -> None:
    await coordinator.async_command("tank_filled")


async def _read_heater_info(coordinator: HearthCoordinator) -> None:
    await coordinator.async_command("heater_read")


async def _unlock_heater(coordinator: HearthCoordinator) -> None:
    await coordinator.async_command("heater_unlock")


async def _health_reset(coordinator: HearthCoordinator) -> None:
    """Firmware 1.82: after a service, the health check learns what is normal for the heater again."""
    await coordinator.async_command("health_reset")


async def _clear_fault(coordinator: HearthCoordinator) -> None:
    """Firmware 1.80: forget the box's held fault, once its cause is fixed."""
    await coordinator.async_command("fault_clear")


async def _reset_learning(coordinator: HearthCoordinator) -> None:
    """Heater Care (firmware 1.47): forget what was learned about how fast the space heats."""
    await coordinator.async_command("thermo_learn_reset")


async def _reboot(coordinator: HearthCoordinator) -> None:
    try:
        await coordinator.client.reboot()
    except HearthError as err:
        raise HomeAssistantError(f"Could not restart {coordinator.config_entry.title}: {err}") from err


BUTTONS: tuple[HearthButtonDescription, ...] = (
    HearthButtonDescription(key="start_preheat", translation_key="start_preheat", press_fn=_start_preheat),
    HearthButtonDescription(key="start_thermostat", translation_key="start_thermostat", press_fn=_start_thermostat),
    HearthButtonDescription(key="start_pump", translation_key="start_pump", press_fn=_start_pump),
    HearthButtonDescription(key="stop", translation_key="stop", press_fn=_stop),
    # Sets the fuel in the tank to its size; the box refuses it until the tank size is set.
    HearthButtonDescription(key="tank_filled", translation_key="tank_filled", press_fn=_tank_filled),
    # Firmware 1.42: ask the heater for its own settings and lifetime report (the heater_info sensors fill in a few
    # seconds later), and clear its lockout. The box refuses an unlock unless the heater is off.
    HearthButtonDescription(
        key="read_heater_info",
        translation_key="read_heater_info",
        entity_category=EntityCategory.CONFIG,
        press_fn=_read_heater_info,
    ),
    # Firmware 1.43: the heater's parts by hand, only while it is off and Auto is off. Stop ends them.
    HearthButtonDescription(key="prime_fuel", translation_key="prime_fuel", press_fn=_prime_fuel),
    HearthButtonDescription(
        key="fan_test", translation_key="fan_test", entity_category=EntityCategory.CONFIG, press_fn=_fan_test
    ),
    HearthButtonDescription(
        key="unlock_heater",
        translation_key="unlock_heater",
        entity_category=EntityCategory.CONFIG,
        press_fn=_unlock_heater,
    ),
    # Firmware 1.80: the box keeps the newest fault after the heater forgets its code at the next start; this clears it.
    HearthButtonDescription(key="clear_fault", translation_key="clear_fault", press_fn=_clear_fault),
    HearthButtonDescription(
        key="heater_serviced",
        translation_key="heater_serviced",
        entity_category=EntityCategory.CONFIG,
        press_fn=_health_reset,
    ),
    HearthButtonDescription(
        key="heater_care_reset",
        translation_key="heater_care_reset",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        press_fn=_reset_learning,
    ),
    HearthButtonDescription(
        key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=_reboot,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HearthConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data
    async_add_entities(HearthButton(coordinator, description) for description in BUTTONS)


class HearthButton(HearthEntity, ButtonEntity):
    """A one-shot action on the box."""

    entity_description: HearthButtonDescription

    def __init__(self, coordinator: HearthCoordinator, description: HearthButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.translation_key is None:
            self._attr_translation_key = None

    async def async_press(self) -> None:
        """Run the action."""
        await self.entity_description.press_fn(self.coordinator)
