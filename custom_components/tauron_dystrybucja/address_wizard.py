"""Short-lived address picker opened through HA's external config-flow step.

The page has no HA credentials. A random, flow-bound capability authorizes
only address lookup and completing that flow; it expires after 15 minutes.
The capability is carried in the URL fragment, then in a request header, so it
does not appear in request URLs, access logs or referrers.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets
from time import monotonic
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow

from .api import TauronApi, TauronApiError, TauronUnsupportedAreaError
from .const import DOMAIN, MIN_SEARCH_LENGTH

WIZARD_URL = f"/api/{DOMAIN}/address_setup"
DATA_WIZARD = f"{DOMAIN}_address_wizard"
SESSION_TTL = 15 * 60
MAX_REQUESTS = 120
MAX_RESULTS = 100
MAX_SESSIONS = 16
TOKEN_HEADER = "X-Tauron-Setup"


@dataclass
class WizardSession:
    """Only server-returned GAIDs may be used to finish a flow."""

    flow_id: str
    api: TauronApi
    expires: float = field(default_factory=lambda: monotonic() + SESSION_TTL)
    requests: int = 0
    cities: dict[str, dict[str, Any]] = field(default_factory=dict)
    streets: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    searches: int = 0


class WizardError(Exception):
    """A safe error code displayed by the browser."""

    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status


class AddressWizard:
    """Manage expiring sessions for simultaneous, independent config flows."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.sessions: dict[str, WizardSession] = {}

    def create(self, flow_id: str, api: TauronApi) -> str:
        """Issue a capability only from the authenticated HA config flow."""
        self.sessions = {
            token: session for token, session in self.sessions.items()
            if session.expires > monotonic() and session.flow_id != flow_id
        }
        if len(self.sessions) >= MAX_SESSIONS:
            raise WizardError("busy", 429)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = WizardSession(flow_id, api)
        return token

    def revoke(self, token: str | None) -> None:
        self.sessions.pop(token, None)

    def _session(self, token: str) -> WizardSession:
        session = self.sessions.get(token)
        if session is None or session.expires <= monotonic():
            self.revoke(token)
            raise WizardError("expired", 410)
        try:
            step = self.hass.config_entries.flow.async_get(session.flow_id)
        except UnknownFlow:
            self.revoke(token)
            raise WizardError("expired", 410) from None
        # async_get returns partial progress (handler/step_id), not the
        # complete form result, so it deliberately has no "type" field.
        if step.get("handler") != DOMAIN or step.get("step_id") != "address":
            self.revoke(token)
            raise WizardError("expired", 410)
        return session

    async def handle(self, token: str, data: dict[str, Any]) -> dict[str, Any]:
        """Bound lookup concurrency; only completion needs exclusive access."""
        session = self._session(token)
        session.requests += 1
        if session.requests > MAX_REQUESTS:
            self.revoke(token)
            raise WizardError("expired", 410)
        if data.get("action") in ("cities", "streets"):
            if session.searches >= 4:
                raise WizardError("busy", 429)
            session.searches += 1
            try:
                return await self._handle(token, session, data)
            finally:
                session.searches -= 1
        if session.lock.locked():
            raise WizardError("busy", 429)
        async with session.lock:
            self._session(token)
            return await self._handle(token, session, data)

    async def _handle(
        self, token: str, session: WizardSession, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Process validated input without exposing arbitrary API access."""
        action = data.get("action")
        if action in ("cities", "streets"):
            query = data.get("query")
            if not isinstance(query, str) or not MIN_SEARCH_LENGTH <= len(query.strip()) <= 100:
                raise WizardError("query")
            query = query.strip()
            if action == "cities":
                results = await session.api.async_get_cities(query)
                options = []
                for city in results[:MAX_RESULTS]:
                    key = str(city["GAID"])
                    session.cities[key] = city
                    district = city.get("DistrictName")
                    label = f"{city['Name']} ({district})" if district else city["Name"]
                    options.append({"value": key, "label": label})
            else:
                city_key = data.get("city")
                if not isinstance(city_key, str) or city_key not in session.cities:
                    raise WizardError("selection")
                results = await session.api.async_get_streets(
                    session.cities[city_key]["GAID"], query
                )
                options = []
                for street in results[:MAX_RESULTS]:
                    key = str(street["GAID"])
                    session.streets[city_key, key] = street
                    options.append({"value": key, "label": street.get("FullName") or street["Name"]})
            return {"options": options, "truncated": len(results) > MAX_RESULTS}

        if action != "finish":
            raise WizardError("request")
        city_key, street_key = data.get("city"), data.get("street")
        if not isinstance(city_key, str) or not isinstance(street_key, str):
            raise WizardError("selection")
        city = session.cities.get(city_key)
        street = session.streets.get((city_key, street_key))
        if city is None or street is None:
            raise WizardError("selection")
        house = data.get("house_no")
        if not isinstance(house, str) or not 1 <= len(house.strip()) <= 64:
            raise WizardError("house")
        # All work/validation happens in the next native HA step, as
        # required by the external-step contract. Names come from Tauron,
        # never from arbitrary browser input.
        self._session(token)
        try:
            result = await self.hass.config_entries.flow.async_configure(
                session.flow_id,
                {"city": city, "street": street, "house_no": house.strip()},
            )
        except UnknownFlow:
            self.revoke(token)
            raise WizardError("expired", 410) from None
        if result["type"] != FlowResultType.EXTERNAL_STEP_DONE:
            raise WizardError("request")
        self.revoke(token)
        return {"done": True}


class AddressWizardView(HomeAssistantView):
    """Serve an inert page and capability-authorized, same-origin requests."""

    url = WIZARD_URL
    name = f"api:{DOMAIN}:address_setup"
    requires_auth = False

    def __init__(self, wizard: AddressWizard, html: str) -> None:
        self.wizard = wizard
        self.html = html

    async def get(self, request: web.Request) -> web.Response:
        nonce = secrets.token_urlsafe(24)
        return web.Response(
            text=self.html.replace("__NONCE__", nonce),
            content_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    f"default-src 'none'; script-src 'nonce-{nonce}'; "
                    f"style-src 'nonce-{nonce}'; connect-src 'self'; "
                    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                ),
            },
        )

    async def post(self, request: web.Request) -> web.Response:
        try:
            # Bound the body before JSON parsing. A custom header and JSON
            # content type prevent cross-origin HTML form submissions.
            if request.content_type != "application/json":
                raise WizardError("request")
            body = bytearray()
            async with asyncio.timeout(10):
                async for chunk in request.content.iter_chunked(4097):
                    body.extend(chunk)
                    if len(body) > 4096:
                        raise WizardError("request")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise WizardError("request")
            result = await self.wizard.handle(request.headers.get(TOKEN_HEADER, ""), data)
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (ValueError, UnicodeDecodeError):
            error = WizardError("request")
        except WizardError as err:
            error = err
        except TauronUnsupportedAreaError:
            error = WizardError("unsupported_area", 422)
        except (TauronApiError, TimeoutError):
            error = WizardError("cannot_connect", 502)
        return web.json_response(
            {"error": error.code}, status=error.status,
            headers={"Cache-Control": "no-store"},
        )


async def async_get_wizard(hass: HomeAssistant) -> AddressWizard:
    """Register once, reading the packaged page outside the event loop."""
    if DATA_WIZARD not in hass.data:
        # Store the task before awaiting IO so simultaneous flows cannot
        # register the same HTTP view twice.
        async def register() -> AddressWizard:
            html = await hass.async_add_executor_job(
                (Path(__file__).parent / "address_setup.html").read_text, "utf-8"
            )
            wizard = AddressWizard(hass)
            hass.http.register_view(AddressWizardView(wizard, html))
            return wizard

        hass.data[DATA_WIZARD] = hass.async_create_task(register())
    return await hass.data[DATA_WIZARD]
