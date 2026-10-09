
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from parser import parse_message

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_FILE = DATA_DIR / "data.json"
HISTORY_FILE = DATA_DIR / "messages.json"
STATE_FILE = DATA_DIR / "state.json"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@PlanKoverTDay").strip()
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, fallback):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp.replace(path)


def telegram_api(method, params=None):
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан TELEGRAM_BOT_TOKEN. Добавьте токен в GitHub Secrets."
        )

    payload = urllib.parse.urlencode(params or {}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API вернул HTTP {error.code}: {detail}") from error

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")

    return result["result"]


def get_chat_id():
    # Public channels can be addressed by @username.
    # getChat confirms that the bot can access the configured channel.
    chat = telegram_api("getChat", {"chat_id": CHANNEL})
    return chat["id"]


def get_history(chat_id, previous_update_id):
    """
    Bot API does not provide a general history endpoint for arbitrary old
    channel posts. getUpdates only returns updates received by the bot.
    This function safely reads new updates available to the bot.
    """
    updates = telegram_api(
        "getUpdates",
        {
            "offset": previous_update_id + 1,
            "timeout": 0,
            "allowed_updates": json.dumps(["channel_post", "edited_channel_post"]),
        },
    )

    new_offset = previous_update_id
    messages = []

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            new_offset = max(new_offset, update_id)

        message = update.get("channel_post") or update.get("edited_channel_post")
        if not message:
            continue

        chat = message.get("chat", {})
        if chat.get("id") != chat_id:
            continue

        messages.append(message)

    return messages, new_offset


def get_text(message):
    # text covers ordinary posts; caption covers media posts.
    return message.get("text") or message.get("caption") or ""


def message_url(message, channel):
    username = message.get("chat", {}).get("username")
    message_id = message.get("message_id")

    if username and message_id:
        return f"https://t.me/{username}/{message_id}"

    clean_channel = channel.removeprefix("@")
    if clean_channel and message_id:
        return f"https://t.me/{clean_channel}/{message_id}"

    return None


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set.", file=sys.stderr)
        return 1

    chat_id = get_chat_id()
    state = read_json(STATE_FILE, {"last_update_id": 0})
    last_update_id = int(state.get("last_update_id", 0))

    messages, new_offset = get_history(chat_id, last_update_id)
    history = read_json(HISTORY_FILE, [])

    if not isinstance(history, list):
        history = []

    known = {
        str(item.get("telegramMessageId"))
        for item in history
        if isinstance(item, dict) and item.get("telegramMessageId") is not None
    }

    added = 0

    for message in messages:
        message_id = message.get("message_id")
        if message_id is None or str(message_id) in known:
            continue

        text = get_text(message)
        if not text.strip():
            continue

        timestamp = datetime.fromtimestamp(
            message["date"], tz=timezone.utc
        ).isoformat()

        source_url = message_url(message, CHANNEL)
        parsed_events = parse_message(
            text,
            published_at=timestamp,
            source_url=source_url,
        )

        history.append({
            "telegramMessageId": message_id,
            "chatId": message.get("chat", {}).get("id"),
            "messageTimestamp": timestamp,
            "sourceUrl": source_url,
            "text": text,
            "events": parsed_events,
        })

        known.add(str(message_id))
        added += 1

    # Store history and update cursor, even if no new messages arrived.
    history.sort(key=lambda item: (
        item.get("messageTimestamp", ""),
        int(item.get("telegramMessageId", 0)),
    ))
    write_json(HISTORY_FILE, history)
    write_json(STATE_FILE, {
        "last_update_id": new_offset,
        "updatedAt": utc_now(),
    })

    # This first collector version only publishes parsed ADD events.
    # Removal/re-addition replay and geocoding are added in a later step.
    active_by_id = {}
    for record in history:
        for event in record.get("events", []):
            if event.get("action") != "ADD":
                continue
            active_by_id[event["id"]] = event

    output = {
        "updatedAt": utc_now(),
        "source": CHANNEL,
        "restrictions": list(active_by_id.values()),
    }
    write_json(DATA_FILE, output)

    print(f"Telegram updates processed: {len(messages)}")
    print(f"New messages saved: {added}")
    print(f"Parsed active candidates: {len(active_by_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
