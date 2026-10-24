import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from collector import build_data_payload


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


if __name__ == "__main__":
    unittest.main()
