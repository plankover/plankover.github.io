import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from parser import is_snapshot, parse_message


class ParserTests(unittest.TestCase):
    def test_red_airport_with_radius(self):
        events = parse_message(
            "🔴🛩️ Калуга — Грабцево, UUBC / 70 км",
            "2026-10-24T12:00:00+00:00",
            "https://t.me/PlanKoverTDay/6000",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "ADD")
        self.assertEqual(events[0]["name"], "Грабцево")
        self.assertEqual(events[0]["icao"], "UUBC")
        self.assertEqual(events[0]["kind"], "airport")
        self.assertEqual(events[0]["type"], "full")
        self.assertEqual(events[0]["radiusKm"], 70.0)

    def test_yellow_without_radius_stays_partial(self):
        events = parse_message(
            "🟡🛩️ Сочи, URSS",
            "2026-10-24T12:00:00+00:00",
        )
        self.assertEqual(events[0]["action"], "ADD")
        self.assertEqual(events[0]["type"], "partial")
        self.assertIsNone(events[0]["radiusKm"])

    def test_heading_plus_single_target_is_not_snapshot(self):
        text = "👆 Актуальные ограничения + 🔴 Рыбинск / 80 км"
        self.assertFalse(is_snapshot(text))
        events = parse_message(text, "2026-10-24T12:00:00+00:00")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "Рыбинск")
        self.assertFalse(events[0].get("snapshot", False))

    def test_standalone_snapshot_heading_is_recognized(self):
        text = "Актуальные ограничения\n🔴 Рыбинск / 80 км\n🔴 Казань / 100 км"
        self.assertTrue(is_snapshot(text))
        events = parse_message(text, "2026-10-24T12:00:00+00:00")
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event.get("snapshot") for event in events))

    def test_green_markers_remove_named_targets(self):
        text = "🟢🛩️ Сочи, URSS\n🟢🛩️ Калуга - Грабцево, UUBC"
        events = parse_message(text, "2026-10-24T12:00:00+00:00")
        self.assertEqual([event["action"] for event in events], ["REMOVE", "REMOVE"])
        self.assertEqual([event["icao"] for event in events], ["URSS", "UUBC"])

    def test_green_summary_removes_each_named_airport(self):
        text = "🟢 Москва (🛩️ Внуково, UUWW; 🛩️ Домодедово, UUDD; 🛩️ Жуковский, UUBW; 🛩️ Шереметьево, UUEE)"
        events = parse_message(text, "2026-10-24T12:00:00+00:00")
        self.assertEqual([event["action"] for event in events], ["REMOVE"] * 4)
        self.assertEqual(
            [event["icao"] for event in events],
            ["UUWW", "UUDD", "UUBW", "UUEE"],
        )

    def test_new_global_clear_command(self):
        events = parse_message(
            "✅ Ранее объявленные",
            "2026-10-24T12:00:00+00:00",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "CLEAR")

    def test_clock_prefixed_snapshot_heading(self):
        text = "🕒 Актуальные ограничения на 16:45 мск:\n\n🔴🛩️ Калуга - Грабцево, UUBC / 70 км"
        self.assertTrue(is_snapshot(text))
        events = parse_message(text, "2026-10-24T12:00:00+00:00")
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].get("snapshot"))

    def test_generic_airports_heading_is_not_a_target(self):
        events = parse_message(
            "🟢⠀Аэропорты\n\n— ВНУКОВО\n— КАЛУГА (Грабцево)",
            "2026-10-24T12:00:00+00:00",
        )
        self.assertEqual(events[0]["action"], "UNKNOWN")

    def test_city_circle_does_not_use_first_airport_as_center(self):
        events = parse_message(
            "🔴 Москва (🛩️ Внуково, UUWW; 🛩️ Домодедово, UUDD; 🛩️ Жуковский, UUBW; 🛩️ Шереметьево, UUEE) / 150 км",
            "2026-10-24T12:00:00+00:00",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "Москва")
        self.assertIsNone(events[0]["icao"])
        self.assertEqual(events[0]["kind"], "city")


    def test_city_restriction_lists_explicitly_affected_airports(self):
        events = parse_message(
            "🔴 Москва (🛩️ Внуково, UUWW; 🛩️ Домодедово, UUDD) / 150 км",
            "2026-10-24T12:00:00+00:00",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "Москва")
        self.assertEqual(events[0]["kind"], "city")
        self.assertEqual(
            events[0]["affectedAirports"],
            [
                {"name": "Внуково", "icao": "UUWW", "kind": "airport"},
                {"name": "Домодедово", "icao": "UUDD", "kind": "airport"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
