"""Diagnostics support for Tauron Dystrybucja."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import TauronConfigEntry
from .const import CONF_HOUSE_NO
from .outages import outage_metadata

TO_REDACT = {CONF_HOUSE_NO}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TauronConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "version": entry.version,
        },
        "coordinator": {
            "scope": data.get("scope"),
            "outage_list_type": data.get("outage_list_type"),
            "address_resolved": data.get("address_resolved"),
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
        },
        "outages": [
            {
                **outage_metadata(outage),
                "key": outage["key"],
                "start": outage["start"].isoformat() if outage["start"] else None,
                "end": outage["end"].isoformat() if outage["end"] else None,
                "type_id": outage["type_id"],
                "is_active": outage["is_active"],
                "message": outage["message"],
            }
            for outage in data.get("all_outages", [])
        ],
    }
