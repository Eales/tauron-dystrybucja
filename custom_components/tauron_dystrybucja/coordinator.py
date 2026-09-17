"""Data update coordinator for Tauron Dystrybucja."""
from __future__ import annotations

import logging
import unicodedata
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TauronApi, TauronApiError
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


def _address_point_id(raw: dict[str, Any]) -> int | None:
    """Return the id of the configured address point, if the API gave one."""
    address_point = raw.get("AddressPoint")
    if isinstance(address_point, dict):
        return address_point.get("AddressPointId")
    return None


def _fold(text: str) -> str:
    """Lower-case ASCII form of Polish text, for loose matching."""
    decomposed = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _message_names_city(message: str | None, raw: dict[str, Any]) -> bool:
    """True when the outage text mentions the configured city.

    Used only for items Tauron publishes without AddressPointIds. Those are
    area-wide faults drawn as a polygon over the whole commune, so the text is
    the only hint left. The comparison uses a stem of the city name, because
    the text declines it (Polish: "Długołęka" / "w Długołęce").
    """
    address_point = raw.get("AddressPoint")
    if not message or not isinstance(address_point, dict):
        return True  # nothing to compare against - keep the item
    city = _fold(address_point.get("CityName") or "")
    if len(city) < 4:
        return True
    stem = city[: max(4, len(city) - 2)]
    return stem in _fold(message)


def parse_outages(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise the API payload into a sorted list of outages."""
    outages = []
    # The endpoint answers with every outage in the area, not only the ones
    # that reach this address, and the Message text is no guide either - a
    # street can be named there while this house number stays powered.
    # AddressPointIds lists the points actually cut off, so that is what
    # decides. An item without the list is an area-wide fault: Tauron draws a
    # polygon over the whole commune and lists streets of a neighbouring town
    # in the text, so it is kept only when its text names the configured city.
    address_point_id = _address_point_id(raw)
    for item in raw.get("OutageItems") or []:
        point_ids = item.get("AddressPointIds")
        if address_point_id is not None and point_ids and address_point_id not in point_ids:
            _LOGGER.debug(
                "Skipping outage %s (%s): address point %s not among %d affected points",
                item.get("OutageId"),
                item.get("Message"),
                address_point_id,
                len(point_ids),
            )
            continue
        if not point_ids and not _message_names_city(item.get("Message"), raw):
            _LOGGER.info(
                "Skipping outage %s (%s): no address points and the text does not name the city",
                item.get("OutageId"),
                item.get("Message"),
            )
            continue
        start = _parse_date(item.get("StartDate"))
        end = _parse_date(item.get("EndDate"))
        outage_id = item.get("OutageId")
        outages.append(
            {
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

        outages = parse_outages(raw)

        current = next(
            (o for o in outages if o["start"] and o["end"] and o["start"] <= now <= o["end"]),
            None,
        )
        upcoming = next((o for o in outages if o["start"] and o["start"] > now), None)

        # Outages announced since the previous refresh. On the very first run
        # everything is "new", but nothing is reported - otherwise every restart
        # would replay old announcements as fresh notifications.
        if self._seen_keys is None:
            new_outages: list[dict[str, Any]] = []
        else:
            new_outages = [o for o in outages if o["key"] not in self._seen_keys]
        self._seen_keys = {o["key"] for o in outages}

        return {
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
