import re
import html
import hashlib
from datetime import datetime, timezone


def clean_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(
        r"</?(?:b|i|strong|em|u|s|blockquote|code|pre)[^>]*>",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def parse_radius(text):
    match = re.search(
        r"(?:/|\bрадиус(?:ом)?\s*)\s*"
        r"(\d+(?:[.,]\d+)?)\s*(?:км|km)\b",
        text,
        flags=re.I,
    )
    if not match:
        match = re.search(
            r"/\s*(\d+(?:[.,]\d+)?)\s*(?:км|km)",
            text,
            flags=re.I,
        )
    return (
        float(match.group(1).replace(",", "."))
        if match else None
    )


def parse_sector(text):
    patterns = [
        r"(?:азимут(?:ы|а)?|сектор)\s*[:=]?\s*"
        r"(\d{1,3})\s*[°º]?\s*[-–—]\s*"
        r"(\d{1,3})\s*[°º]?",
        r"\b(\d{1,3})\s*[°º]\s*[-–—]\s*"
        r"(\d{1,3})\s*[°º]",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            start = int(match.group(1))
            end = int(match.group(2))

            if start <= 360 and end <= 360 and start != end:
                return {
                    "startAzimuth": start,
                    "endAzimuth": end,
                }

    return None


def detect_action(text):
    normalized = re.sub(r"\s+", " ", text.lower()).strip()

    if re.search(r"🟢\s*ранее\s+объявлен", normalized):
        return "CLEAR"

    removal = re.search(
        r"(?:сняты|снято|отменены|отменено|"
        r"прекращены|прекращено)"
        r".{0,150}(?:ограничени|при[её]м|выпуск|"
        r"работ[ау]\s+аэропорт)",
        normalized,
    )

    reverse_removal = re.search(
        r"(?:ограничени\w*|при[её]м\s+и\s+выпуск)"
        r".{0,150}(?:сняты|снято|отменены|отменено|"
        r"прекращены|прекращено)",
        normalized,
    )

    if removal or reverse_removal:
        return "REMOVE"

    if re.search(
        r"(?:введен[ыо]?|вводятся|действуют|"
        r"установлен[ыо]?|временно\s+ограничен[ыо]?|"
        r"ограничен[ыо]?)",
        normalized,
    ):
        return "ADD"

    if "🔴" in text or "🟡" in text:
        return "ADD"

    return "UNKNOWN"


def split_targets(text):
    """
    Определяет основной центр зоны до обработки ICAO.

    Если в сообщении сначала указан город, а затем в скобках
    упомянут другой аэропорт, сохраняет город как центр.

    Если город и аэропорт совпадают по названию, а аэропорт
    явно отмечен самолётом или словом «аэропорт», выбирает
    аэропорт.
    """
    cleaned = clean_text(text)

    # Сначала ищем явно обозначенный центр в начале сообщения.
    # Например: 🔴 Плёс / 150 км
    # или: 🔴 Казань (🛩️ Казань, UWKD) / 100 км
    leading = re.search(
        r"^\s*[🔴🟡🟢⚠️\s]*"
        r"([А-ЯЁа-яё-]+(?:\s+[А-ЯЁа-яё-]+){0,2})",
        cleaned,
    )

    leading_name = None
    if leading:
        candidate = leading.group(1).strip()
        if candidate.lower() not in {
            "ранее объявленные",
            "ограничения сняты",
            "введены временные",
            "временные ограничения",
        }:
            leading_name = candidate

    # Собираем названия аэропортов с ICAO-кодами.
    airport_pattern = re.compile(
        r"(?:аэропорт(?:а|у|е|ом)?\s+)?"
        r"([А-ЯЁA-Z][А-ЯЁа-яёA-Za-z-]+)"
        r"(?:\s+([А-ЯЁа-яёA-Za-z-]+))?"
        r"\s*,?\s*\(?\s*([A-Z]{4})\s*\)?",
        flags=re.I,
    )

    airports = []
    for match in airport_pattern.finditer(cleaned):
        name = " ".join(
            part for part in match.group(1, 2) if part
        ).strip()

        airports.append({
            "name": name,
            "icao": match.group(3).upper(),
            "kind": "airport",
        })

    if airports:
        # Если основной город явно отличается от названия
        # упомянутого аэропорта, центр — город.
        if leading_name:
            normalized_leading = leading_name.casefold()

            same_airport = any(
                normalized_leading == airport["name"].casefold()
                for airport in airports
            )

            explicit_airport_center = bool(
                re.search(
                    r"^\s*[🔴🟡🟢⚠️\s]*"
                    r"(?:🛩️\s*)?аэропорт\b",
                    cleaned,
                    flags=re.I,
                )
            )

            if not same_airport and not explicit_airport_center:
                return [{
                    "name": leading_name,
                    "icao": None,
                    "kind": "city",
                }]

        # Если центр совпадает с аэропортом либо в сообщении
        # перечислены несколько аэропортов, сохраняем ICAO.
        unique = {}
        for airport in airports:
            unique[airport["icao"]] = airport

        return list(unique.values())

    # Сообщение без ICAO-кода.
    short = re.search(
        r"^\s*[🔴🟡🟢⚠️\s]*"
        r"(?:аэропорт\s+)?"
        r"([А-ЯЁа-яё-]+(?:\s+[А-ЯЁа-яё-]+){0,2})",
        cleaned,
        flags=re.I,
    )

    if short:
        name = short.group(1).strip()

        if name.lower() not in {
            "ранее объявленные",
            "ограничения сняты",
        }:
            airport = bool(
                re.search(r"\bаэропорт\b", cleaned, re.I)
            ) or "🛩️" in text

            return [{
                "name": name,
                "icao": None,
                "kind": "airport" if airport else "city",
            }]

    return []


def parse_message(message, published_at=None, source_url=None):
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

    if action in {"UNKNOWN", "CLEAR"}:
        return [{
            "id": digest,
            "action": action,
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
            f"{digest}|{target['icao'] or target['name']}|{index}"
            .encode("utf-8")
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