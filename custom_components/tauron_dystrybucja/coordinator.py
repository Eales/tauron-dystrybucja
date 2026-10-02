"""Data update coordinator for Tauron Dystrybucja."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TauronApi, TauronApiError
from .outages import is_current, is_upcoming, point_match, response_metadata
from .const import (
    CONF_CITY_GAID,
    CONF_HOUSE_NO,
    CONF_SCAN_INTERVAL,
    CONF_STREET_GAID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOOKAHEAD,
)

_LOGGER = logging.getLogger(__name__)


def _parse_date(value: str | None) -> datetime | None:
    """Parse an API timestamp into an aware datetime."""
    if not value:
        return None
    return dt_util.parse_datetime(value)


def parse_outages(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise the API payload into a sorted list of outages."""
    outages = []
    metadata = response_metadata(raw)
    for item in raw.get("OutageItems") or []:
        start = _parse_date(item.get("StartDate"))
        end = _parse_date(item.get("EndDate"))
        outage_id = item.get("OutageId")
        outages.append(
            {
                **metadata,
                "address_point_match": point_match(raw, item),
                "coordinates_type": item.get("CoordinatesType"),
                "id": outage_id,
                # The API reuses OutageId for separate time slots of the same
                # works, so the start time is needed to identify an occurrence.
                "key": f"{outage_id}-{start.isoformat() if start else 'unknown'}",
                "message": item.get("Message"),
                "start": start,
                "end": end,
                "type_id": item.get("TypeId"),
                "is_active": bool(item.get("IsActive")),
            }
        )
    outages.sort(key=lambda o: o["start"] or dt_util.utc_from_timestamp(0))
    return outages


class TauronOutageCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches the outage list for one address."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(minutes=minutes),
        )
        self.entry = entry
        self._api = TauronApi(async_get_clientsession(hass))
        # None until the first successful refresh, so a restart does not
        # re-announce outages that were already known.
        self._seen_keys: set[str] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.now()
        try:
            raw = await self._api.async_get_outages(
                city_gaid=self.entry.data[CONF_CITY_GAID],
                street_gaid=self.entry.data[CONF_STREET_GAID],
                house_no=self.entry.data[CONF_HOUSE_NO],
                from_date=now.strftime("%Y-%m-%dT%H:%M:%S"),
                to_date=(now + LOOKAHEAD).strftime("%Y-%m-%dT%H:%M:%S"),
            )
        except TauronApiError as err:
            raise UpdateFailed(str(err)) from err

        all_outages = parse_outages(raw)
        # Preserve raw reports for diagnostics; only actionable reports feed
        # the count and announcements. Calendar history is fetched separately.
        outages = [
            o for o in all_outages if is_current(o, now) or is_upcoming(o, now)
        ]

        current = next(
            (o for o in outages if is_current(o, now)),
            None,
        )
        upcoming = next((o for o in outages if is_upcoming(o, now)), None)

        # Outages announced since the previous refresh. On the very first run
        # everything is "new", but nothing is reported - otherwise every restart
        # would replay old announcements as fresh notifications.
        if self._seen_keys is None:
            new_outages: list[dict[str, Any]] = []
        else:
            new_outages = [o for o in outages if o["key"] not in self._seen_keys]
        self._seen_keys = {o["key"] for o in outages}

        return {
            **response_metadata(raw),
            "all_outages": all_outages,
            "outages": outages,
            "current": current,
            "next": upcoming,
            "new": new_outages,
        }

    async def async_fetch_range(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Fetch outages for an arbitrary window (used by the calendar)."""
        try:
            raw = await self._api.async_get_outages(
                city_gaid=self.entry.data[CONF_CITY_GAID],
                street_gaid=self.entry.data[CONF_STREET_GAID],
                house_no=self.entry.data[CONF_HOUSE_NO],
                from_date=start.strftime("%Y-%m-%dT%H:%M:%S"),
                to_date=end.strftime("%Y-%m-%dT%H:%M:%S"),
            )
        except TauronApiError as err:
            raise UpdateFailed(str(err)) from err
        return parse_outages(raw)
