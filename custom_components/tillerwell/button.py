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

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class HearthButtonDescription(ButtonEntityDescription):
    """A button that runs a coroutine against the coordinator."""

    press_fn: Callable[[HearthCoordinator], Coroutine[Any, Any, None]]


async def _start_preheat(coordinator: HearthCoordinator) -> None:
    minutes = int(coordinator.data.status.get("preheat_minutes") or 30)
    if coordinator.data.status.get("auto_enabled"):
        await coordinator.async_command("auto", enabled=False)
    await coordinator.async_command("start_preheat", minutes=minutes)


async def _start_thermostat(coordinator: HearthCoordinator) -> None:
    await coordinator.async_command("start_thermostat")


async def _start_pump(coordinator: HearthCoordinator) -> None:
    await coordinator.async_command("start_pump")


async def _stop(coordinator: HearthCoordinator) -> None:
    await coordinator.async_stop()


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
