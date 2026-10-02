"""Dependency-free tests of the classification rules (synthetic edge cases)."""
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE = Path(__file__).parents[1] / "custom_components/tauron_dystrybucja/outages.py"
spec = importlib.util.spec_from_file_location("outages", MODULE)
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)
NOW = datetime(2026, 9, 27, 13, tzinfo=timezone.utc)


class ClassificationTests(unittest.TestCase):
    def test_scope_is_not_inferred_from_point(self):
        for kind, expected in [(1, "address"), (2, "area"), (None, "unknown"), (99, "unknown")]:
            with self.subTest(kind=kind):
                result = rules.response_metadata({"OutageListType": kind, "AddressPoint": {"AddressPointId": 10}})
                self.assertEqual(result["scope"], expected)
                self.assertTrue(result["address_resolved"])
        self.assertFalse(rules.response_metadata({})["address_resolved"])

    def test_point_membership_only_describes_evidence(self):
        raw = {"AddressPoint": {"AddressPointId": 10}}
        for ids, expected in [([10], "listed"), ([20], "not_listed"), ([], "unavailable"), (None, "unavailable")]:
            self.assertEqual(rules.point_match(raw, {"AddressPointIds": ids}), expected)
        self.assertEqual(rules.point_match({}, {"AddressPointIds": [10]}), "unavailable")

    def test_fault_and_planned_time_boundaries(self):
        outage = {"start": NOW, "end": NOW + timedelta(hours=1), "type_id": 2, "is_active": False}
        self.assertFalse(rules.is_current(outage, NOW))
        outage["is_active"] = True
        self.assertTrue(rules.is_current(outage, NOW))
        self.assertFalse(rules.is_current(outage, outage["end"]))
        self.assertFalse(rules.is_current(outage, NOW - timedelta(seconds=1)))
        self.assertFalse(rules.is_upcoming(outage, NOW - timedelta(seconds=1)))
        outage.update(type_id=1, is_active=False)
        self.assertTrue(rules.is_current(outage, NOW))
        self.assertTrue(rules.is_upcoming(outage, NOW - timedelta(seconds=1)))
        outage["end"] = NOW
        self.assertFalse(rules.is_current(outage, NOW))
        self.assertFalse(rules.is_upcoming(outage, NOW - timedelta(seconds=1)))

    def test_missing_dates_do_not_announce_an_outage(self):
        for start, end in [(None, NOW), (NOW, None), (None, None)]:
            outage = {"start": start, "end": end, "type_id": 1, "is_active": True}
            self.assertFalse(rules.is_current(outage, NOW))
            self.assertFalse(rules.is_upcoming(outage, NOW))

    def test_status_distinguishes_scope_and_keeps_address_values(self):
        self.assertEqual(rules.status_value(None, None), "none")
        for scope, suffix in [("address", ""), ("area", "_area"), ("unknown", "_unknown")]:
            outage = {"scope": scope}
            self.assertEqual(rules.status_value(outage, None), "ongoing" + suffix)
            self.assertEqual(rules.status_value(None, outage), "upcoming" + suffix)
