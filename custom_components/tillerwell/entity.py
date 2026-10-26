"""Shared base entity."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import HearthCoordinator


class HearthEntity(CoordinatorEntity[HearthCoordinator]):
    """An entity of one Hearth box."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HearthCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{key}"
        self._attr_device_info = coordinator.device_info

    @property
    def status(self) -> dict[str, Any]:
        """The latest /api/status document."""
        return self.coordinator.data.status

    @property
    def info(self) -> dict[str, Any]:
        """The latest /api/info document."""
        return self.coordinator.data.info
