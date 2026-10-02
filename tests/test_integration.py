"""Tests with real HA classes; transport and clock are mocked, not the parser."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.tauron_dystrybucja.api import TauronApi, TauronApiError, TauronUnsupportedAreaError
from custom_components.tauron_dystrybucja.coordinator import TauronOutageCoordinator, parse_outages
from custom_components.tauron_dystrybucja.config_flow import TauronConfigFlow
from custom_components.tauron_dystrybucja.calendar import _to_event
from custom_components.tauron_dystrybucja.binary_sensor import TauronOutageActiveSensor
from custom_components.tauron_dystrybucja.sensor import TauronStatusSensor, TauronOutageCountSensor
from custom_components.tauron_dystrybucja.event import TauronNewOutageEvent
from homeassistant.helpers.update_coordinator import UpdateFailed

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 27, 13, tzinfo=timezone.utc)


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class PayloadTests(unittest.TestCase):
    def test_recorded_responses_are_not_filtered_by_point_or_town(self):
        for name, count in [("rakowiecka30", 6), ("polna1", 11), ("polna99999", 11), ("siewierz1", 1)]:
            with self.subTest(name=name):
                raw = fixture(name)
                parsed = parse_outages(raw)
                self.assertEqual(len(parsed), count)
                self.assertEqual({o["id"] for o in parsed}, {o["OutageId"] for o in raw["OutageItems"]})
        rakowiecka = {o["id"]: o for o in parse_outages(fixture("rakowiecka30"))}
        self.assertEqual(rakowiecka["e036a30c-27b5-4887-b144-6fe928f6c41a"]["address_point_match"], "not_listed")
        self.assertEqual(rakowiecka["7ba327db-fa7f-424a-9ded-6d362302e4a3"]["scope"], "area")
        self.assertEqual(rakowiecka["c497d2cf-4338-4580-88e9-126cfd7f1a3a"]["address_point_match"], "unavailable")
        self.assertEqual(parse_outages(fixture("siewierz1"))[0]["scope"], "address")

    def test_synthetic_polish_inflection_does_not_remove_warnings(self):
        for city, message in [("Nysa", "Awaria w Nysie"), ("Kozy", "Awaria w Kozach"), ("Bielsko-Biała", "Awaria w Bielsku-Białej"), ("Wrocław", "Radomierzyce, ulica Wrocławska 1")]:
            raw = fixture("rakowiecka30")
            raw["AddressPoint"]["CityName"] = city
            raw["OutageItems"] = [dict(raw["OutageItems"][0], Message=message, AddressPointIds=[])]
            self.assertEqual(len(parse_outages(raw)), 1)

    def test_calendar_keeps_history_and_labels_scope(self):
        for name, title in [("rakowiecka30", "Wyłączenie prądu w okolicy"), ("siewierz1", "Wyłączenie prądu — adres wg Taurona")]:
            outage = parse_outages(fixture(name))[0]
            event = _to_event(outage, "test address")
            self.assertEqual(event.summary, title)
            self.assertIn(outage["message"], event.description)
            self.assertEqual(event.uid, outage["key"])


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.coordinator = object.__new__(TauronOutageCoordinator)
        self.coordinator.entry = SimpleNamespace(
            entry_id="test", data={"city_gaid": 1, "street_gaid": 2, "house_no": "1", "city_name": "Test", "street_name": "Street"},
        )
        self.coordinator._api = Mock(async_get_outages=AsyncMock())
        self.coordinator._seen_keys = None
        self.clock = patch("custom_components.tauron_dystrybucja.coordinator.dt_util.now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    async def refresh(self, raw):
        self.coordinator._api.async_get_outages.return_value = raw
        self.coordinator.data = await self.coordinator._async_update_data()
        return self.coordinator.data

    async def test_first_refresh_quiet_then_new_occurrences_only(self):
        raw = fixture("rakowiecka30")
        data = await self.refresh(raw)
        self.assertEqual(data["new"], [])
        self.assertEqual(len(data["outages"]), 6)
        self.assertEqual(TauronStatusSensor(self.coordinator).native_value, "upcoming_area")
        self.assertEqual(TauronOutageCountSensor(self.coordinator).native_value, 6)
        raw = copy.deepcopy(raw)
        extra = dict(raw["OutageItems"][0], StartDate="2026-10-15T08:00:00Z", EndDate="2026-10-15T10:00:00Z")
        raw["OutageItems"].append(extra)  # Same ID, different occurrence.
        self.assertEqual(len((await self.refresh(raw))["new"]), 1)
        self.assertEqual((await self.refresh(raw))["new"], [])

    async def test_ended_fault_is_not_counted_or_announced(self):
        await self.refresh({"OutageItems": []})
        data = await self.refresh(fixture("siewierz1"))
        self.assertEqual(data["outages"], [])
        self.assertEqual(data["new"], [])
        self.assertIsNone(data["current"])
        self.assertEqual(len(data["all_outages"]), 1)
        self.assertEqual(TauronStatusSensor(self.coordinator).native_value, "none")

    async def test_inactive_fault_and_future_fault_are_not_active_or_next(self):
        raw = fixture("siewierz1")
        raw["OutageItems"][0].update(StartDate="2026-09-27T12:00:00Z", EndDate="2026-09-27T14:00:00Z", IsActive=False)
        self.assertIsNone((await self.refresh(raw))["current"])
        raw["OutageItems"][0]["IsActive"] = True
        await self.refresh(raw)
        self.assertTrue(TauronOutageActiveSensor(self.coordinator).is_on)
        self.assertEqual(TauronStatusSensor(self.coordinator).native_value, "ongoing")
        raw["OutageItems"][0].update(StartDate="2026-09-28T12:00:00Z", EndDate="2026-09-28T14:00:00Z")
        self.assertIsNone((await self.refresh(raw))["next"])

    async def test_unsupported_area_fails_refresh_and_calendar(self):
        self.coordinator._api.async_get_outages.side_effect = TauronUnsupportedAreaError("unsupported")
        with self.assertRaises(UpdateFailed):
            await self.coordinator._async_update_data()
        with self.assertRaises(UpdateFailed):
            await self.coordinator.async_fetch_range(NOW, NOW + timedelta(days=1))

    async def test_event_keeps_ha_event_type_and_adds_outage_scope(self):
        await self.refresh({"OutageItems": []})
        raw = fixture("rakowiecka30")
        raw["OutageItems"] = raw["OutageItems"][:1]
        await self.refresh(raw)
        entity = TauronNewOutageEvent(self.coordinator)
        entity.async_write_ha_state = Mock()
        entity._handle_coordinator_update()
        attributes = entity.extra_state_attributes
        self.assertEqual(attributes["event_type"], "new_outage")
        self.assertEqual(attributes["scope"], "area")
        self.assertIn("address_point_match", attributes)


class ApiAndFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_distinguishes_unsupported_from_supported_empty(self):
        api = TauronApi(Mock())
        for payload, error in [(fixture("unsupported"), TauronUnsupportedAreaError), ([], TauronApiError)]:
            api._get = AsyncMock(return_value=payload)
            with self.assertRaises(error):
                await api.async_get_outages(1, 2, "1", "start", "end")
        for ids in [[], [1], [0, 1]]:
            api._get = AsyncMock(return_value={"IdsWWW": ids, "OutageItems": []})
            self.assertEqual((await api.async_get_outages(1, 2, "1", "start", "end"))["OutageItems"], [])

    async def test_config_flow_shows_specific_unsupported_error(self):
        flow = TauronConfigFlow()
        flow._city = {"GAID": 1, "Name": "City"}
        flow._street = {"GAID": 2, "Name": "Street"}
        flow._api = Mock(async_get_outages=AsyncMock(side_effect=TauronUnsupportedAreaError()))
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = Mock()
        result = await flow.async_step_house_number({"house_no": "1"})
        self.assertEqual(result["errors"], {"base": "unsupported_area"})
        self.assertEqual(result["step_id"], "house_number")
