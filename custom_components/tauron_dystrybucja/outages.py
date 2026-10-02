"""Outage classification without guessing which houses actually lose power."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def response_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep the API-declared scope separate from address resolution."""
    list_type = raw.get("OutageListType")
    point = raw.get("AddressPoint")
    return {
        "scope": {1: "address", 2: "area"}.get(list_type, "unknown"),
        "outage_list_type": list_type,
        "address_resolved": isinstance(point, dict)
        and point.get("AddressPointId") is not None,
    }


def point_match(raw: dict[str, Any], item: dict[str, Any]) -> str:
    """Describe membership, never use it as a completeness/impact guarantee."""
    point = raw.get("AddressPoint")
    point_id = point.get("AddressPointId") if isinstance(point, dict) else None
    ids = item.get("AddressPointIds")
    if point_id is None or not ids:
        return "unavailable"
    return "listed" if point_id in ids else "not_listed"


def outage_metadata(outage: dict[str, Any]) -> dict[str, Any]:
    """Metadata shared by entities, events and diagnostics (no address IDs)."""
    return {
        key: outage[key]
        for key in (
            "scope", "outage_list_type", "address_resolved",
            "address_point_match", "coordinates_type",
        )
    }


def is_current(outage: dict[str, Any], now: datetime) -> bool:
    """Planned work follows its interval; a fault must also still be active."""
    start, end = outage["start"], outage["end"]
    return bool(
        start and end and start <= now < end
        and (outage["type_id"] == 1
             or (outage["type_id"] == 2 and outage["is_active"]))
    )


def is_upcoming(outage: dict[str, Any], now: datetime) -> bool:
    """Only valid future planned work is an upcoming outage."""
    start, end = outage["start"], outage["end"]
    return bool(outage["type_id"] == 1 and start and end and now < start < end)


def status_value(current: dict | None, upcoming: dict | None) -> str:
    """Distinguish API address results, area warnings and unknown scope."""
    outage = current or upcoming
    if outage is None:
        return "none"
    status = "ongoing" if current else "upcoming"
    return status if outage["scope"] == "address" else f"{status}_{outage['scope']}"
