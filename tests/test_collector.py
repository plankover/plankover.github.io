import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from collector import build_data_payload, fetch_updates, polling_timeout, resolve_event_coords


class DataPayloadTests(unittest.TestCase):
    def test_unchanged_map_preserves_timestamp(self):
        existing = {
            "updatedAt": "2026-10-24T12:00:00+00:00",
            "source": "@PlanKoverTDay",
            "restrictions": [
                {"name": "Сочи", "icao": "URSS", "lat": 43.4, "lon": 39.9}
            ],
        }
        current_restrictions = [
            {"lon": 39.9, "lat": 43.4, "icao": "URSS", "name": "Сочи"}
        ]
        result = build_data_payload(
            existing, current_restrictions, "@PlanKoverTDay",
            "2026-10-24T12:05:00+00:00",
        )
        self.assertEqual(result, existing)

    def test_changed_map_updates_timestamp(self):
        existing = {
            "updatedAt": "2026-10-24T12:00:00+00:00",
            "source": "@PlanKoverTDay",
            "restrictions": [{"name": "Сочи", "icao": "URSS"}],
        }
        current_restrictions = [
            {"name": "Казань", "icao": "UWKD"}
        ]
        result = build_data_payload(
            existing, current_restrictions, "@PlanKoverTDay",
            "2026-10-24T12:05:00+00:00",
        )
        self.assertEqual(result["updatedAt"], "2026-10-24T12:05:00+00:00")
        self.assertEqual(result["restrictions"], current_restrictions)



class AffectedAirportCoordinateTests(unittest.TestCase):
    def test_resolves_coordinates_for_restriction_and_each_affected_airport(self):
        event = {
            "name": "Москва",
            "icao": None,
            "affectedAirports": [
                {"name": "Внуково", "icao": "UUWW", "kind": "airport"},
                {"name": "Домодедово", "icao": "UUDD", "kind": "airport"},
            ],
        }
        city = {"lat": 55.75, "lon": 37.62}
        vnukovo = {"lat": 55.59, "lon": 37.26}
        domodedovo = {"lat": 55.41, "lon": 37.91}

        with patch("collector.cache_coords", side_effect=[city, vnukovo, domodedovo]):
            result = resolve_event_coords(event, {})

        self.assertEqual((result["lat"], result["lon"]), (55.75, 37.62))
        self.assertEqual(
            [(a["lat"], a["lon"]) for a in result["affectedAirports"]],
            [(55.59, 37.26), (55.41, 37.91)],
        )


class TelegramLongPollingTests(unittest.TestCase):
    def test_fetch_updates_uses_configured_timeout(self):
        with patch.dict(os.environ, {"TELEGRAM_LONG_POLL_TIMEOUT_SECONDS": "20"}):
            with patch("collector.api", return_value=[]) as api_mock:
                messages, new_offset = fetch_updates(12)

        self.assertEqual(messages, [])
        self.assertEqual(new_offset, 12)
        method, params = api_mock.call_args.args
        self.assertEqual(method, "getUpdates")
        self.assertEqual(params["offset"], 13)
        self.assertEqual(params["timeout"], 20)

    def test_timeout_is_capped_below_http_timeout(self):
        with patch.dict(os.environ, {"TELEGRAM_LONG_POLL_TIMEOUT_SECONDS": "99"}):
            self.assertEqual(polling_timeout(), 25)

    def test_invalid_timeout_falls_back_to_short_poll(self):
        with patch.dict(os.environ, {"TELEGRAM_LONG_POLL_TIMEOUT_SECONDS": "invalid"}):
            self.assertEqual(polling_timeout(), 0)


if __name__ == "__main__":
    unittest.main()
