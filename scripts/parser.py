
import re
import html
import hashlib
from datetime import datetime, timezone


def clean_text(text):
    """Remove Telegram HTML/Markdown formatting noise."""
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?(?:b|i|strong|em|u|s|blockquote|code|pre)[^>]*>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def parse_radius(text):
    match = re.search(
        r"(?:/|\bрадиус(?:ом)?\s*)\s*(\d+(?:[.,]\d+)?)\s*(?:км|km)\b",
        text,
        flags=re.I,
    )
    if not match:
        match = re.search(r"/\s*(\d+(?:[.,]\d+)?)\s*(?:км|km)", text, flags=re.I)
    return float(match.group(1).replace(",", ".")) if match else None


def parse_sector(text):
    """Recognize an explicitly stated azimuth range, e.g. 100–270 degrees."""
    patterns = [
        r"(?:азимут(?:ы|а)?|сектор)\s*[:=]?\s*(\d{1,3})\s*[°º]?\s*[-–—]\s*(\d{1,3})\s*[°º]?",
        r"\b(\d{1,3})\s*[°º]\s*[-–—]\s*(\d{1,3})\s*[°º]",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start <= 360 and end <= 360 and start != end:
                return {"startAzimuth": start, "endAzimuth": end}
    return None


def detect_action(text):
    """Return ADD, REMOVE, or CLEAR for common Russian restriction wording."""
    normalized = re.sub(r"\s+", " ", text.lower()).strip()

    if re.search(r"🟢\s*ранее\s+объявлен", normalized):
        return "CLEAR"

    removal = re.search(
        r"(?:сняты|снято|отменены|отменено|прекращены|прекращено)"
        r".{0,100}(?:ограничени|при[её]м|выпуск|работ[ау]\s+аэропорт)",
        normalized,
    )
    reverse_removal = re.search(
        r"(?:ограничени\w*|при[её]м\s+и\s+выпуск).{0,100}"
        r"(?:сняты|снято|отменены|отменено|прекращены|прекращено)",
        normalized,
    )
    if removal or reverse_removal:
        return "REMOVE"

    if re.search(
        r"(?:введен[ыо]?|вводятся|действуют|установлен[ыо]?|"
        r"временно\s+ограничен[ыо]?|ограничен[ыо]?)",
        normalized,
    ):
        return "ADD"

    if "🔴" in text or "🟡" in text:
        return "ADD"

    return "UNKNOWN"


def split_targets(text):
    """Split a message that mentions several airports into separate candidates."""
    cleaned = clean_text(text)
    matches = list(
        re.finditer(
            r"(?:аэропорт(?:а|у|е|ом)?\s+)?"
            r"([А-ЯЁA-Z][А-ЯЁа-яёA-Za-z-]+)"
            r"(?:\s+([А-ЯЁа-яёA-Za-z-]+))?"
            r"\s*,?\s*\(?\s*([A-Z]{4})\s*\)?",
            cleaned,
        )
    )

    targets = []
    for match in matches:
        name = " ".join(part for part in match.group(1, 2) if part)
        targets.append({
            "name": name.strip(),
            "icao": match.group(3),
            "kind": "airport",
        })

    if targets:
        unique = {}
        for target in targets:
            unique[target["icao"]] = target
        return list(unique.values())

    # Short messages can identify a city without an ICAO code.
    short = re.search(
        r"^\s*[🔴🟡🟢⚠️\s]*"
        r"(?:аэропорт\s+)?"
        r"([А-ЯЁа-яё-]+(?:\s+[А-ЯЁа-яё-]+)?)"
        r"\s*(?:/\s*\d+\s*км)?\s*$",
        cleaned,
        flags=re.I,
    )
    if short:
        name = short.group(1).strip()
        if name.lower() not in {"ранее объявленные", "ограничения сняты"}:
            airport = bool(re.search(r"аэропорт", cleaned, re.I)) or "🛩️" in text
            targets.append({
                "name": name,
                "icao": None,
                "kind": "airport" if airport else "city",
            })

    return targets


def parse_message(message, published_at=None, source_url=None):
    """
    Parse one Telegram message into a normalized event.
    Coordinates are intentionally not guessed here; geocoding is a separate step.
    """
    original = message or ""
    text = clean_text(original)
    action = detect_action(original + "\n" + text)

    timestamp = published_at
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc).isoformat()
    elif timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    digest = hashlib.sha256(
        f"{timestamp}|{text}".encode("utf-8")
    ).hexdigest()[:16]

    if action == "UNKNOWN":
        return [{
            "id": digest,
            "action": "UNKNOWN",
            "messageTimestamp": timestamp,
            "text": text,
            "sourceUrl": source_url,
        }]

    if action == "CLEAR":
        return [{
            "id": digest,
            "action": "CLEAR",
            "messageTimestamp": timestamp,
            "text": text,
            "sourceUrl": source_url,
        }]

    targets = split_targets(text)
    if not targets:
        return [{
            "id": digest,
            "action": action,
            "messageTimestamp": timestamp,
            "text": text,
            "unresolved": True,
            "sourceUrl": source_url,
        }]

    radius = parse_radius(text)
    sector = parse_sector(text)
    yellow = "🟡" in original

    events = []
    for index, target in enumerate(targets):
        event_id = hashlib.sha256(
            f"{digest}|{target['icao'] or target['name']}|{index}".encode("utf-8")
        ).hexdigest()[:16]

        event = {
            "id": event_id,
            "action": action,
            "name": target["name"],
            "icao": target["icao"],
            "kind": target["kind"],
            "type": "partial" if yellow or sector else "full",
            "radiusKm": radius,
            "messageTimestamp": timestamp,
            "sourceUrl": source_url,
            "text": text,
        }

        if sector:
            event["sector"] = {
                "radiusKm": radius,
                "startAzimuth": sector["startAzimuth"],
                "endAzimuth": sector["endAzimuth"],
            }

        events.append(event)

    return events
