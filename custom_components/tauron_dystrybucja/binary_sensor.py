"""Binary sensor platform for Tauron Dystrybucja."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TauronConfigEntry
from .entity import TauronEntity
from .outages import outage_metadata


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TauronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tauron binary sensor from a config entry."""
    async_add_entities([TauronOutageActiveSensor(entry.runtime_data)])


class TauronOutageActiveSensor(TauronEntity, BinarySensorEntity):
    """True for an ongoing report; scope may be address, area or unknown."""

    _attr_translation_key = "outage_active"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:flash-off"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "outage_active")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data["current"] is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current = self.coordinator.data["current"]
        if not current:
            return super().extra_state_attributes
        return {
            **outage_metadata(current),
            "description": current["message"],
            "start": current["start"],
            "end": current["end"],
        }
