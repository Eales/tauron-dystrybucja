"""Event platform for Tauron Dystrybucja."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TauronConfigEntry
from .coordinator import TauronOutageCoordinator
from .entity import TauronEntity
from .outages import outage_metadata

EVENT_NEW_OUTAGE = "new_outage"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TauronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tauron announcement event entity."""
    async_add_entities([TauronNewOutageEvent(entry.runtime_data)])


class TauronNewOutageEvent(TauronEntity, EventEntity):
    """Fires when Tauron announces an outage that was not known before."""

    _attr_translation_key = "new_outage"
    _attr_event_types = [EVENT_NEW_OUTAGE]
    _attr_icon = "mdi:bell-alert"

    def __init__(self, coordinator: TauronOutageCoordinator) -> None:
        super().__init__(coordinator, "new_outage")

    @property
    def extra_state_attributes(self) -> None:
        """EventEntity retains scope with the last event in state_attributes.

        Do not overwrite it with the scope of a newer API response.
        """
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Trigger one event per newly announced outage."""
        if not self.coordinator.last_update_success:
            # A failed refresh keeps the previous payload. Do not announce its
            # cached "new" list again while marking the entity unavailable.
            super()._handle_coordinator_update()
            return
        for outage in self.coordinator.data["new"]:
            start = outage["start"]
            end = outage["end"]
            self._trigger_event(
                EVENT_NEW_OUTAGE,
                {
                    **outage_metadata(outage),
                    "outage_id": outage["id"],
                    "description": outage["message"],
                    "start": dt_util.as_local(start).isoformat() if start else None,
                    "end": dt_util.as_local(end).isoformat() if end else None,
                },
            )
        super()._handle_coordinator_update()
