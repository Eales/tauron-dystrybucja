"""Thin async client for the public Tauron Dystrybucja web API."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import (
    API_BASE_URL,
    ENDPOINT_CITIES,
    ENDPOINT_OUTAGES,
    ENDPOINT_STREETS,
)

_LOGGER = logging.getLogger(__name__)


class TauronApiError(Exception):
    """Raised when the Tauron API cannot be reached or returns an error."""


class TauronUnsupportedAreaError(TauronApiError):
    """The API explicitly says it does not serve the requested area."""


class TauronApi:
    """Wraps the three endpoints this integration needs."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        url = f"{API_BASE_URL}{endpoint}"
        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                response.raise_for_status()
                # The API serves JSON as text/plain on some endpoints.
                return await response.json(content_type=None)
        except aiohttp.ClientError as err:
            raise TauronApiError(f"Error calling {endpoint}: {err}") from err

    async def async_get_cities(self, part_name: str) -> list[dict[str, Any]]:
        """Search for cities matching a partial name."""
        data = await self._get(ENDPOINT_CITIES, {"partName": part_name})
        return data if isinstance(data, list) else []

    async def async_get_streets(self, city_gaid: int, part_name: str) -> list[dict[str, Any]]:
        """Search for streets within a city.

        The API expects the *city* GAID in the ownerGAID parameter - passing the
        city's own OwnerGAID (the commune) silently returns an empty list.
        """
        data = await self._get(
            ENDPOINT_STREETS, {"partName": part_name, "ownerGAID": city_gaid}
        )
        return data if isinstance(data, list) else []

    async def async_get_outages(
        self,
        city_gaid: int,
        street_gaid: int,
        house_no: str,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """Fetch planned and unplanned outages for an address."""
        data = await self._get(
            ENDPOINT_OUTAGES,
            {
                "cityGAID": city_gaid,
                "streetGAID": street_gaid,
                "houseNo": house_no,
                "fromDate": from_date,
                "toDate": to_date,
                "getLightingSupport": "true",
                "getServicedSwitchingoff": "true",
            },
        )
        if not isinstance(data, dict):
            raise TauronApiError("Invalid outage response")
        if data.get("IdsWWW") == [0]:
            raise TauronUnsupportedAreaError("Tauron does not support this area")
        return data
