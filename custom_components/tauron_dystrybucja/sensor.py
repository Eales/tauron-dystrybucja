"""Sensor platform for Tauron Dystrybucja."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TauronConfigEntry
from .coordinator import TauronOutageCoordinator
from .entity import TauronEntity
from .outages import outage_metadata, status_value

# Home Assistant rejects states longer than this.
MAX_STATE_LENGTH = 255

STATUS_NONE = "none"
STATUS_UPCOMING = "upcoming"
STATUS_ONGOING = "ongoing"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TauronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tauron sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            TauronStatusSensor(coordinator),
            TauronNextOutageSensor(coordinator),
            TauronNextOutageEndSensor(coordinator),
            TauronNextOutageDurationSensor(coordinator),
            TauronNextOutageDescriptionSensor(coordinator),
            TauronOutageCountSensor(coordinator),
        ]
    )


class TauronRelevantOutageEntity(TauronEntity, SensorEntity):
    """Base for sensors describing the ongoing outage, or the next one."""

    @property
    def _outage(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        return data["current"] or data["next"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the outage as flat attributes, usable without templates."""
        outage = self._outage
        if not outage:
            return super().extra_state_attributes
        return {
            **outage_metadata(outage),
            "start": outage["start"],
            "end": outage["end"],
            "description": outage["message"],
        }


class TauronStatusSensor(TauronEntity, SensorEntity):
    """One glanceable state: nothing planned, planned, or happening now."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        STATUS_NONE, STATUS_UPCOMING, STATUS_ONGOING,
        "upcoming_area", "ongoing_area", "upcoming_unknown", "ongoing_unknown",
    ]
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator: TauronOutageCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        return status_value(data["current"], data["next"])


class TauronNextOutageSensor(TauronRelevantOutageEntity):
    """Start of the ongoing or next outage."""

    _attr_translation_key = "next_outage"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator: TauronOutageCoordinator) -> None:
        super().__init__(coordinator, "next_outage")

    @property
    def native_value(self) -> datetime | None:
        outage = self._outage
        return outage["start"] if outage else None


class TauronNextOutageEndSensor(TauronRelevantOutageEntity):
    """End of the ongoing or next outage."""

    _attr_translation_key = "next_outage_end"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-end"

    def __init__(self, coordinator: TauronOutageCoordinator) -> None:
        super().__init__(coordinator, "next_outage_end")

    @property
    def native_value(self) -> datetime | None:
        outage = self._outage
        return outage["end"] if outage else None


class TauronNextOutageDurationSensor(TauronRelevantOutageEntity):
    """How long the ongoing or next outage lasts."""

    _attr_translation_key = "next_outage_duration"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: TauronOutageCoordinator) -> None:
        super().__init__(coordinator, "next_outage_duration")

    @property
    def native_value(self) -> float | None:
        outage = self._outage
        if not outage or not outage["start"] or not outage["end"]:
            return None
        return (outage["end"] - outage["start"]).total_seconds() / 3600


class TauronNextOutageDescriptionSensor(TauronRelevantOutageEntity):
    """The description Tauron publishes for the ongoing or next outage.

    Read alongside the API scope. Neither the text nor point membership is a
    guarantee of the house's physical power supply state.
    """

    _attr_translation_key = "next_outage_description"
    _attr_icon = "mdi:text-long"

    def __init__(self, coordinator: TauronOutageCoordinator) -> None:
        super().__init__(coordinator, "next_outage_description")

    @property
    def native_value(self) -> str | None:
        outage = self._outage
        if not outage:
            return None
        message = outage["message"] or ""
        if len(message) <= MAX_STATE_LENGTH:
            return message
        # Descriptions occasionally exceed the state limit; the untruncated text
        # stays available in the full_message attribute.
        return f"{message[: MAX_STATE_LENGTH - 1]}…"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        outage = self._outage
        if outage:
            attributes["full_description"] = outage["message"]
        return attributes


class TauronOutageCountSensor(TauronEntity, SensorEntity):
    """Number of outages announced for the lookahead window."""

    _attr_translation_key = "outage_count"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: TauronOutageCoordinator) -> None:
        super().__init__(coordinator, "outage_count")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data["outages"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **super().extra_state_attributes,
            "outages": [
                {
                    **outage_metadata(outage),
                    "description": outage["message"],
                    "start": outage["start"],
                    "end": outage["end"],
                }
                for outage in self.coordinator.data["outages"]
            ]
        }
