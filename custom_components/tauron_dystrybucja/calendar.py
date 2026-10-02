"""Calendar platform for Tauron Dystrybucja."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TauronConfigEntry
from .const import CONF_CITY_NAME, CONF_HOUSE_NO, CONF_STREET_NAME
from .coordinator import TauronOutageCoordinator
from .entity import TauronEntity

SUMMARIES = {
    "address": "Wyłączenie prądu — adres wg Taurona",
    "area": "Wyłączenie prądu w okolicy",
    "unknown": "Wyłączenie prądu — nieznany zakres",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TauronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tauron outage calendar."""
    async_add_entities([TauronOutageCalendar(entry.runtime_data)])


def _to_event(outage: dict[str, Any], location: str) -> CalendarEvent | None:
    """Convert an outage into a calendar event, skipping incomplete ones."""
    if not outage["start"] or not outage["end"] or outage["start"] >= outage["end"]:
        return None
    evidence = {
        "listed": "Punkt adresowy znajduje się na liście Taurona.",
        "not_listed": "Punktu adresowego nie ma na liście Taurona; lista może być niepełna.",
        "unavailable": "Brak danych do sprawdzenia punktu na liście Taurona.",
    }[outage["address_point_match"]]
    return CalendarEvent(
        start=dt_util.as_local(outage["start"]),
        end=dt_util.as_local(outage["end"]),
        summary=SUMMARIES[outage["scope"]],
        description=(
            f"{outage['message'] or ''}\n\n"
            f"{SUMMARIES[outage['scope']]}. {evidence}\n"
            "Komunikat Taurona nie potwierdza faktycznego braku zasilania w domu."
        ),
        location=location if outage["scope"] == "address" else f"Adres zapytania: {location}",
        uid=outage["key"],
    )


class TauronOutageCalendar(TauronEntity, CalendarEntity):
    """Exposes outages as calendar events so they can drive time-based automations."""

    _attr_translation_key = "outages"
    _attr_icon = "mdi:calendar-alert"

    def __init__(self, coordinator: TauronOutageCoordinator) -> None:
        super().__init__(coordinator, "calendar")
        data = coordinator.entry.data
        self._location = (
            f"{data[CONF_CITY_NAME]}, {data[CONF_STREET_NAME]} {data[CONF_HOUSE_NO]}"
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the ongoing outage, or the next upcoming one."""
        data = self.coordinator.data
        outage = data["current"] or data["next"]
        return _to_event(outage, self._location) if outage else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return events in an explicit window, as requested by the UI."""
        outages = await self.coordinator.async_fetch_range(start_date, end_date)
        events = [_to_event(outage, self._location) for outage in outages]
        return [event for event in events if event is not None]
