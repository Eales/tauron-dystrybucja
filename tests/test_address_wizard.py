"""Address picker tests using real aiohttp requests and HA flow results."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowManager, FlowResultType, UnknownFlow

from custom_components.tauron_dystrybucja.address_wizard import (
    AddressWizard, AddressWizardView, MAX_REQUESTS, MAX_RESULTS,
    TOKEN_HEADER, WIZARD_URL, WizardError,
)
from custom_components.tauron_dystrybucja.api import TauronApiError
from custom_components.tauron_dystrybucja.config_flow import TauronConfigFlow

CITY = {"GAID": 119431, "Name": "Wrocław", "DistrictName": "Wrocław"}
OTHER_CITY = {"GAID": 1, "Name": "Wrocław", "DistrictName": "Inny powiat"}
STREET = {"GAID": 898134, "Name": "Rakowiecka", "FullName": "ul. Rakowiecka"}
HTML = Path(__file__).parents[1] / "custom_components/tauron_dystrybucja/address_setup.html"


class WizardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = Mock()
        self.flow.async_get.return_value = {"handler": "tauron_dystrybucja", "step_id": "address"}
        self.flow.async_configure = AsyncMock(return_value={"type": FlowResultType.EXTERNAL_STEP_DONE})
        self.hass = SimpleNamespace(config_entries=SimpleNamespace(flow=self.flow))
        self.api = Mock(
            async_get_cities=AsyncMock(return_value=[CITY, OTHER_CITY]),
            async_get_streets=AsyncMock(return_value=[STREET]),
        )
        self.wizard = AddressWizard(self.hass)
        self.token = self.wizard.create("flow-1", self.api)
        view = AddressWizardView(self.wizard, HTML.read_text(encoding="utf-8"))
        app = web.Application()
        app.router.add_get(WIZARD_URL, view.get)
        app.router.add_post(WIZARD_URL, view.post)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def post(self, data, token=None):
        return await self.client.post(WIZARD_URL, json=data, headers={TOKEN_HEADER: self.token if token is None else token})

    async def load_choices(self):
        await self.wizard.handle(self.token, {"action": "cities", "query": "Wro"})
        await self.wizard.handle(self.token, {"action": "streets", "city": str(CITY["GAID"]), "query": "Rak"})

    async def test_city_options_preserve_duplicate_names_by_id(self):
        response = await self.post({"action": "cities", "query": " Wro "})
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertEqual(result["options"], [
            {"value": "119431", "label": "Wrocław (Wrocław)"},
            {"value": "1", "label": "Wrocław (Inny powiat)"},
        ])
        self.api.async_get_cities.assert_awaited_once_with("Wro")

    async def test_streets_require_returned_city_and_use_city_gaid(self):
        result = await self.post({"action": "streets", "city": "119431", "query": "Rak"})
        self.assertEqual(result.status, 400)
        self.api.async_get_streets.assert_not_awaited()
        await self.load_choices()
        self.api.async_get_streets.assert_awaited_once_with(119431, "Rak")

    async def test_finish_binds_street_to_city_and_uses_server_names(self):
        await self.load_choices()
        wrong = await self.post({"action": "finish", "city": "1", "street": "898134", "house_no": "30"})
        self.assertEqual(wrong.status, 400)
        self.flow.async_configure.assert_not_awaited()
        result = await self.post({"action": "finish", "city": "119431", "street": "898134", "house_no": " 30 ", "city_name": "forged"})
        self.assertEqual(await result.json(), {"done": True})
        self.flow.async_configure.assert_awaited_once_with("flow-1", {"city": CITY, "street": STREET, "house_no": "30"})
        self.assertNotIn(self.token, self.wizard.sessions)
        replay = await self.post({"action": "finish", "city": "119431", "street": "898134", "house_no": "30"})
        self.assertEqual(replay.status, 410)

    async def test_expired_and_cancelled_flows_cannot_search(self):
        self.wizard.sessions[self.token].expires = 0
        self.assertEqual((await self.post({"action": "cities", "query": "Wro"})).status, 410)
        self.token = self.wizard.create("flow-2", self.api)
        self.flow.async_get.side_effect = UnknownFlow
        self.assertEqual((await self.post({"action": "cities", "query": "Wro"})).status, 410)
        self.api.async_get_cities.assert_not_awaited()

    async def test_completed_flow_step_revokes_capability(self):
        self.flow.async_get.return_value = {"handler": "tauron_dystrybucja", "step_id": "house_number"}
        self.assertEqual((await self.post({"action": "cities", "query": "Wro"})).status, 410)
        self.assertNotIn(self.token, self.wizard.sessions)

    async def test_unknown_token_cannot_use_api(self):
        self.assertEqual((await self.post({"action": "cities", "query": "Wro"}, "wrong-token")).status, 410)
        self.api.async_get_cities.assert_not_awaited()

    async def test_request_budget_and_result_limit(self):
        self.api.async_get_cities.return_value = [dict(CITY, GAID=i) for i in range(MAX_RESULTS + 1)]
        result = await (await self.post({"action": "cities", "query": "Wro"})).json()
        self.assertEqual(len(result["options"]), MAX_RESULTS)
        self.assertTrue(result["truncated"])
        self.wizard.sessions[self.token].requests = MAX_REQUESTS
        self.assertEqual((await self.post({"action": "cities", "query": "Wro"})).status, 410)

    async def test_lookup_errors_are_safe_and_retryable(self):
        self.api.async_get_cities.side_effect = TauronApiError("internal detail")
        response = await self.post({"action": "cities", "query": "Wro"})
        self.assertEqual(response.status, 502)
        self.assertEqual(await response.json(), {"error": "cannot_connect"})
        self.api.async_get_cities.side_effect = None
        self.assertEqual((await self.post({"action": "cities", "query": "Wro"})).status, 200)

    async def test_invalid_and_oversized_input_never_reaches_tauron(self):
        for data in [[], {"action": "cities", "query": "ab"}, {"action": "cities", "query": "x" * 101}, {"action": "cities", "query": []}, {"action": "anything"}]:
            self.assertEqual((await self.post(data)).status, 400)
        response = await self.client.post(WIZARD_URL, data="x" * 5000, headers={TOKEN_HEADER: self.token, "Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        response = await self.client.post(WIZARD_URL, data={"action": "cities", "query": "Wro"})
        self.assertEqual(response.status, 400)
        self.api.async_get_cities.assert_not_awaited()

    async def test_page_is_inert_without_token_and_has_restrictive_policy(self):
        response = await self.client.get(WIZARD_URL)
        page = await response.text()
        self.assertEqual(response.status, 200)
        self.assertNotIn(self.token, page)
        self.assertNotIn("__NONCE__", page)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    async def test_slow_lookup_does_not_block_new_search(self):
        started = asyncio.Event()
        release = asyncio.Event()
        async def search(query):
            if query == "Wro":
                started.set()
                await release.wait()
                return [CITY]
            return [OTHER_CITY]
        self.api.async_get_cities.side_effect = search
        old = asyncio.create_task(self.wizard.handle(self.token, {"action": "cities", "query": "Wro"}))
        await started.wait()
        new = await self.wizard.handle(self.token, {"action": "cities", "query": "Wroc"})
        self.assertEqual(new["options"][0]["value"], "1")
        release.set()
        await old
        self.assertEqual(len(self.wizard.sessions[self.token].cities), 2)

    async def test_sessions_do_not_share_choices(self):
        await self.load_choices()
        other_token = self.wizard.create("flow-2", self.api)
        result = await self.post({"action": "finish", "city": "119431", "street": "898134", "house_no": "30"}, other_token)
        self.assertEqual(result.status, 400)
        self.flow.async_configure.assert_not_awaited()


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_flow_manager_round_trip(self):
        """Exercise HA's actual partial-progress and external-step protocol."""
        flow = TauronConfigFlow()
        flow._api = Mock(
            async_get_cities=AsyncMock(return_value=[CITY]),
            async_get_streets=AsyncMock(return_value=[STREET]),
            async_get_outages=AsyncMock(return_value={"OutageItems": []}),
        )
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = Mock()

        class Manager(FlowManager):
            async def async_create_flow(self, handler_key, *, context=None, data=None):
                flow.init_step = context["source"]
                return flow

            async def async_finish_flow(self, handler, result):
                return result

        hass = HomeAssistant("/tmp/tauron-wizard-unit-test")
        manager = Manager(hass)
        hass.config_entries = SimpleNamespace(flow=manager)
        wizard = AddressWizard(hass)
        with patch("custom_components.tauron_dystrybucja.config_flow.async_get_wizard", AsyncMock(return_value=wizard)):
            menu = await manager.async_init("tauron_dystrybucja", context={"source": "user"})
            external = await manager.async_configure(menu["flow_id"], {"next_step_id": "address"})
        token = external["url"].split("#")[1]
        self.assertNotIn("type", manager.async_get(menu["flow_id"]))
        await wizard.handle(token, {"action": "cities", "query": "Wro"})
        await wizard.handle(token, {"action": "streets", "city": "119431", "query": "Rak"})
        result = await wizard.handle(token, {"action": "finish", "city": "119431", "street": "898134", "house_no": "30"})
        self.assertEqual(result, {"done": True})
        entry = await manager.async_configure(menu["flow_id"])
        self.assertEqual(entry["type"], FlowResultType.CREATE_ENTRY)
        self.assertEqual(entry["data"]["street_gaid"], 898134)
        self.assertEqual(entry["data"]["house_no"], "30")
        flow._api.async_get_outages.assert_awaited_once()

    async def test_entry_menu_keeps_native_fallback(self):
        result = await TauronConfigFlow().async_step_user()
        self.assertEqual(result["type"], FlowResultType.MENU)
        self.assertEqual(result["menu_options"], ["address", "manual"])

    async def test_manual_path_still_queries_tauron(self):
        flow = TauronConfigFlow()
        flow._api = Mock(async_get_cities=AsyncMock(return_value=[CITY]))
        result = await flow.async_step_manual({"city_partial": "Wro"})
        self.assertEqual(result["step_id"], "city")
        flow._api.async_get_cities.assert_awaited_once_with("Wro")

    async def test_external_completion_returns_to_existing_validation(self):
        flow = TauronConfigFlow()
        result = await flow.async_step_address({"city": CITY, "street": STREET, "house_no": "30"})
        self.assertEqual(result["type"], FlowResultType.EXTERNAL_STEP_DONE)
        self.assertEqual(result["step_id"], "finish")
        flow.async_step_house_number = AsyncMock(return_value={"type": "form"})
        await flow.async_step_finish()
        flow.async_step_house_number.assert_awaited_once_with({"house_no": "30"})

    async def test_external_link_is_flow_bound_and_not_reissued_on_refresh(self):
        flow = TauronConfigFlow()
        flow.hass = Mock()
        flow.flow_id = "flow-1"
        flow._api = Mock()
        wizard = Mock()
        wizard.create.return_value = "secret-token"
        with patch("custom_components.tauron_dystrybucja.config_flow.async_get_wizard", AsyncMock(return_value=wizard)):
            first = await flow.async_step_address()
            second = await flow.async_step_address()
        self.assertEqual(first["url"], WIZARD_URL + "#secret-token")
        self.assertEqual(second["url"], first["url"])
        wizard.create.assert_called_once_with("flow-1", flow._api)
