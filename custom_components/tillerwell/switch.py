"""Switches."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ACTIVE_STATES
from .coordinator import HearthConfigEntry, HearthCoordinator
from .entity import HearthEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class HearthSwitchDescription(SwitchEntityDescription):
    """A switch backed by a status field and a command taking `enabled`."""

    is_on_fn: Callable[[dict[str, Any]], bool | None]
    set_fn: Callable[[HearthCoordinator, bool], Coroutine[Any, Any, None]]


def _flag(field: str) -> Callable[[dict[str, Any]], bool | None]:
    def read(status: dict[str, Any]) -> bool | None:
        value = status.get(field)
        return None if value is None else bool(value)

    return read


def _command(cmd: str) -> Callable[[HearthCoordinator, bool], Coroutine[Any, Any, None]]:
    async def write(coordinator: HearthCoordinator, on: bool) -> None:
        await coordinator.async_command(cmd, enabled=on)

    return write


async def _run(coordinator: HearthCoordinator, on: bool) -> None:
    """Heater on is a timed preheat of the saved duration; off is a proper stop."""
    if on:
        minutes = int(coordinator.data.status.get("preheat_minutes") or 30)
        if coordinator.data.status.get("auto_enabled"):
            await coordinator.async_command("auto", enabled=False)
        await coordinator.async_command("start_preheat", minutes=minutes)
    else:
        await coordinator.async_stop()


def _running(status: dict[str, Any]) -> bool | None:
    state = status.get("state")
    return None if state is None else state in ACTIVE_STATES


SWITCHES: tuple[HearthSwitchDescription, ...] = (
    HearthSwitchDescription(key="heater", translation_key="heater", is_on_fn=_running, set_fn=_run),
    HearthSwitchDescription(
        key="prevent_freezing",
        translation_key="prevent_freezing",
        is_on_fn=_flag("prevent_freezing_enabled"),
        set_fn=_command("freeze"),
    ),
    HearthSwitchDescription(
        key="use_external_temp",
        translation_key="use_external_temp",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=_flag("use_external_temp"),
        set_fn=_command("use_external_temp"),
    ),
    HearthSwitchDescription(
        key="bypass_mode",
        translation_key="bypass_mode",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=_flag("bypass_mode"),
        set_fn=_command("bypass"),
    ),
    HearthSwitchDescription(
        key="debug_mode",
        translation_key="debug_mode",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=_flag("debug_mode"),
        set_fn=_command("debug"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HearthConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the switches."""
    coordinator = entry.runtime_data
    async_add_entities(HearthSwitch(coordinator, description) for description in SWITCHES)


class HearthSwitch(HearthEntity, SwitchEntity):
    """A switch on the box."""

    entity_description: HearthSwitchDescription

    def __init__(self, coordinator: HearthCoordinator, description: HearthSwitchDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Current state, None when unknown."""
        return self.entity_description.is_on_fn(self.status)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self.entity_description.set_fn(self.coordinator, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self.entity_description.set_fn(self.coordinator, False)
