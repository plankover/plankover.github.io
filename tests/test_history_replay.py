import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from parser import parse_message
from collector import build_active


class HistoryReplayTests(unittest.TestCase):
    def test_reopening_removes_area_zone_for_airport(self):
        history = [
            {"timestamp": "2026-11-01T00:00:00+00:00", "events": [
                {"action": "ADD", "name": "Москва", "kind": "city", "affectedAirports": [{"name": "Внуково", "icao": "UUWW", "kind": "airport"}]},
                {"action": "ADD", "name": "Казань", "icao": "UWKD", "kind": "airport"},
            ]},
            {"timestamp": "2026-11-01T00:10:00+00:00", "events": [
                {"action": "REMOVE", "name": "Внуково", "icao": "UUWW", "kind": "airport"},
            ]},
        ]
        active = build_active(history)
        self.assertEqual([item["name"] for item in active], ["Казань"])
    def test_archived_history_replays_without_empty_or_unresolved_targets(self):
        archive_path = ROOT / "data" / "messages.json"
        with archive_path.open(encoding="utf-8") as f:
            archived = json.load(f)

        deduped = {}
        for position, record in enumerate(archived):
            message_id = record.get("messageId")
            chat_id = record.get("chatId")
            key = (chat_id, message_id) if message_id is not None else ("legacy", position)
            deduped[key] = record

        history = list(deduped.values())
        for record in history:
            record["events"] = parse_message(
                record.get("text", ""),
                record.get("timestamp"),
                record.get("sourceUrl"),
            )

        active = build_active(history)
        # A legitimate final global-clear command can leave no active items.
        # Only require a non-empty map when the most recent message is not a clear.
        if history:
            latest = max(history, key=lambda record: record.get("timestamp", ""))
            latest_events = latest.get("events", [])
            if any(event.get("action") == "CLEAR" for event in latest_events):
                self.assertEqual(active, [])
            else:
                self.assertGreater(len(active), 0)
        self.assertTrue(all(item.get("name") for item in active))
        self.assertTrue(all(item.get("action") == "ADD" for item in active))
        print(
            "ARCHIVE_REPLAY_SUMMARY",
            json.dumps({
                "archived_records": len(archived),
                "unique_messages": len(history),
                "active_targets": len(active),
                "targets": [
                    {"name": item.get("name"), "icao": item.get("icao"),
                     "type": item.get("type"), "radiusKm": item.get("radiusKm")}
                    for item in active
                ],
            }, ensure_ascii=False),
        )


if __name__ == "__main__":
    unittest.main()
