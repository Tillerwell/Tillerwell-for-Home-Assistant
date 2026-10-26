"""Binary sensors."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HearthConfigEntry, HearthCoordinator
from .entity import HearthEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class HearthBinarySensorDescription(BinarySensorEntityDescription):
    """A boolean field of the status document."""

    field: str | None = None


BINARY_SENSORS: tuple[HearthBinarySensorDescription, ...] = (
    HearthBinarySensorDescription(
        key="fault_active", translation_key="fault_active", device_class=BinarySensorDeviceClass.PROBLEM
    ),
    HearthBinarySensorDescription(
        key="burner_active", translation_key="burner_active", device_class=BinarySensorDeviceClass.HEAT
    ),
    HearthBinarySensorDescription(
        key="heater_seen",
        translation_key="heater_seen",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HearthBinarySensorDescription(
        key="controller_seen",
        translation_key="controller_seen",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HearthBinarySensorDescription(
        key="stale",
        translation_key="stale",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HearthBinarySensorDescription(
        key="standalone", translation_key="standalone", entity_category=EntityCategory.DIAGNOSTIC
    ),
    HearthBinarySensorDescription(
        key="clock_ok", translation_key="clock_ok", entity_category=EntityCategory.DIAGNOSTIC
    ),
    HearthBinarySensorDescription(
        key="external_temp_active",
        translation_key="external_temp_active",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HearthBinarySensorDescription(
        key="extended_active",
        translation_key="extended_active",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    HearthBinarySensorDescription(
        key="ext_glow_plug",
        translation_key="ext_glow_plug",
        device_class=BinarySensorDeviceClass.HEAT,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HearthConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(HearthBinarySensor(coordinator, description) for description in BINARY_SENSORS)


class HearthBinarySensor(HearthEntity, BinarySensorEntity):
    """A yes/no value from the box."""

    entity_description: HearthBinarySensorDescription

    def __init__(self, coordinator: HearthCoordinator, description: HearthBinarySensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """True, False, or None when the box has not reported it."""
        value = self.status.get(self.entity_description.field or self.entity_description.key)
        return None if value is None else bool(value)
