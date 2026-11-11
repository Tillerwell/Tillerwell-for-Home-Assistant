"""Binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
    # Only filled by extended telemetry: unavailable until the heater actually sends it.
    extended: bool = False
    # Worked out from several fields instead of read from one; unavailable when it returns None.
    value_fn: Callable[[dict[str, Any]], bool | None] | None = None


def _locked(status: dict[str, Any]) -> bool | None:
    """Locked out after repeated failed starts or overheating: state 1, substate 1 ("locked" in the vendor's table)."""
    if status.get("state_raw") is None:
        return None
    return status.get("state_raw") == 1 and status.get("substate") == 1


def _thermo_flag(key: str) -> Callable[[dict[str, Any]], bool | None]:
    """A flag from the Heater Care object (firmware 1.47); unknown on older firmware."""

    def read(status: dict[str, Any]) -> bool | None:
        thermo = status.get("thermo")
        return bool(thermo.get(key)) if isinstance(thermo, dict) else None

    return read


BINARY_SENSORS: tuple[HearthBinarySensorDescription, ...] = (
    # Heater Care: the room sensor has not changed for 15 min while heating, so the controller's reading is used;
    # and no usable temperature at all (a run then stops at the end of its minimum run).
    HearthBinarySensorDescription(
        key="heater_care_sensor_stuck",
        translation_key="heater_care_sensor_stuck",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_thermo_flag("sensor_stuck"),
    ),
    HearthBinarySensorDescription(
        key="heater_care_no_reading",
        translation_key="heater_care_no_reading",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_thermo_flag("no_reading"),
    ),
    HearthBinarySensorDescription(
        key="fault_active", translation_key="fault_active", device_class=BinarySensorDeviceClass.PROBLEM
    ),
    HearthBinarySensorDescription(
        key="locked", translation_key="locked", device_class=BinarySensorDeviceClass.PROBLEM, value_fn=_locked
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
        extended=True,
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
        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(self.status)
        value = self.status.get(self.entity_description.field or self.entity_description.key)
        return None if value is None else bool(value)

    @property
    def available(self) -> bool:
        """Extended telemetry is unavailable, not unknown, until the heater sends it."""
        if not super().available:
            return False
        return not self.entity_description.extended or bool(self.status.get("extended_active"))
