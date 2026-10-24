import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from collector import build_data_payload, fetch_updates, polling_timeout


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
