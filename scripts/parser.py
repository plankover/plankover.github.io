import hashlib
import html
import re
from datetime import datetime, timezone

EMOJI = "🔴🟡🟢⚠️✈️🛩️🕒"


def clean_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?(?:b|i|strong|em|u|s|blockquote|code|pre)[^>]*>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def parse_radius(text):
    m = re.search(r"(?:/|\bрадиус(?:ом)?\s*)\s*(\d+(?:[.,]\d+)?)\s*(?:км|km)\b|\bна расстоянии\s+(\d+(?:[.,]\d+)?)\s*(?:км|km)\b", text, re.I)
    if not m:
        return None
    value = next((x for x in m.groups() if x is not None), None)
    return float(value.replace(",", ".")) if value else None


def parse_sector(text):
    for pattern in (r"(?:азимут(?:ы|а)?|сектор)\s*[:=]?\s*(\d{1,3})\s*[°º]?\s*[-–—]\s*(\d{1,3})\s*[°º]?", r"\b(\d{1,3})\s*[°º]\s*[-–—]\s*(\d{1,3})\s*[°º]"):
        m = re.search(pattern, text, re.I)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= 360 and b <= 360 and a != b:
                return {"startAzimuth": a, "endAzimuth": b}
    return None


def is_snapshot(text):
    """Only explicit summary headings trigger destructive reconciliation."""
    return bool(re.search(r"(?:актуальные\s+ограничени|список\s+актуальных\s+ограничени|текущие\s+ограничени)\s*(?:на\s+\d{1,2}:\d{2}\s*(?:мск)?)?", clean_text(text).lower()))


def detect_action(text):
    t = re.sub(r"\s+", " ", clean_text(text).lower()).strip()
    if re.search(r"🟢\s*ранее\s+объявлен", t):
        return "CLEAR"
    removal = re.search(r"(?:сняты|снято|отменены|отменено|прекращены|прекращено)", t)
    topic = re.search(r"(?:ограничени|при[её]м|выпуск|работ[ау]\s+аэропорт|аэропорт)", t)
    if removal and topic:
        return "REMOVE"
    if re.search(r"(?:ограничени\w*|при[её]м\s+и\s+выпуск).{0,180}(?:сняты|снято|отменены|отменено|прекращены|прекращено)", t):
        return "REMOVE"
    if "🔴" in text or "🟡" in text:
        return "ADD"
    if re.search(r"(?:введен[ыо]?|вводятся|действуют|установлен[ыо]?|временно\s+ограничен[ыо]?|ограничен[ыо]?)", t):
        return "ADD"
    return "UNKNOWN"


def parse_target_line(line):
    """Return one target from one bullet/line, or None if it is not safely parsed."""
    line = clean_text(line)
    if not line or not re.search(r"[🔴🟡]", line):
        return None
    # Remove status emoji and leading bullet-like symbols.
    body = re.sub(r"^\s*[🔴🟡⚠️✈️🛩️\s]+", "", line).strip()
    explicit_airport = bool(re.match(r"(?:(?:а\s*/\s*д|аэродром|аэропорт(?:а|у|е|ом)?)\s+|(?:✈️|🛩️)\s*)", body, re.I)) or "🛩️" in line or "✈️" in line
    body = re.sub(r"^(?:а\s*/\s*д|аэродром|аэропорт(?:а|у|е|ом)?)\s+", "", body, flags=re.I)
    body = re.sub(r"^\s*(?:🛩️|✈️)\s*", "", body)
    # Ignore the radius tail and any sector description while identifying the place.
    body = re.split(r"\s*/\s*\d|\bрадиус(?:ом)?\s*\d|\bсектор\b|\bазимут", body, maxsplit=1, flags=re.I)[0].strip(" .,:;—–-")
    icao_match = re.search(r"\b([A-Z]{4})\b", body)
    icao = icao_match.group(1) if icao_match else None
    if icao_match:
        before = body[:icao_match.start()].strip(" ,()—–-")
        # Typical format "Калуга - Грабцево, UUBC": airport is the final name after dash.
        if re.search(r"[-—–]", before):
            name = re.split(r"\s*[-—–]\s*", before)[-1].strip()
        else:
            name = before
        name = re.sub(r",\s*$", "", name).strip()
        if not name:
            return None
        return {"name": name, "icao": icao, "kind": "airport", "locationHint": None}
    # "а/д Клоково, Тула" should keep the city hint for geocoding.
    if explicit_airport and "," in body:
        first, rest = [x.strip() for x in body.split(",", 1)]
        name = f"{first}, {rest}" if rest else first
    else:
        name = body
    name = re.sub(r"\s+", " ", name).strip(" ,.:;—–-")
    if not name or len(name) > 100:
        return None
    return {"name": name, "icao": None, "kind": "airport" if explicit_airport else "city", "locationHint": None}


def parse_message(message, published_at=None, source_url=None):
    original = message or ""
    text = clean_text(original)
    ts = published_at
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = ts.astimezone(timezone.utc).isoformat()
    elif ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(f"{ts}|{text}".encode()).hexdigest()[:16]
    snapshot = is_snapshot(text)

    if re.search(r"🟢\s*ранее\s+объявлен", text.lower()):
        return [{"id": digest, "action": "CLEAR", "messageTimestamp": ts, "text": text, "sourceUrl": source_url}]

    # Ordinary removal messages keep their existing non-destructive handling.
    overall_action = detect_action(original)
    if overall_action == "REMOVE" and not snapshot:
        return [{"id": digest, "action": "REMOVE", "messageTimestamp": ts, "text": text, "sourceUrl": source_url, "unresolved": True}]

    events = []
    lines = text.splitlines() or [text]
    failed_snapshot_line = False
    for line_no, line in enumerate(lines):
        target = parse_target_line(line)
        if not target:
            if snapshot and re.search(r"[🔴🟡]", line):
                failed_snapshot_line = True
            continue
        radius = parse_radius(line)
        sector = parse_sector(line)
        yellow = "🟡" in line
        key = target.get("icao") or target["name"].casefold()
        eid = hashlib.sha256(f"{digest}|{key}|{line_no}".encode()).hexdigest()[:16]
        event = {
            "id": eid,
            "action": "ADD",
            "name": target["name"],
            "icao": target.get("icao"),
            "kind": target["kind"],
            "type": "partial" if yellow or sector else "full",
            "radiusKm": radius,
            "messageTimestamp": ts,
            "sourceUrl": source_url,
            "text": line,
        }
        if target.get("locationHint"):
            event["locationHint"] = target["locationHint"]
        if sector:
            event["sector"] = {"radiusKm": radius, **sector}
        if snapshot:
            event["snapshot"] = True
        events.append(event)

    # Any unparsed restriction line makes the snapshot unsafe for destructive reconciliation.
    if snapshot and (not events or failed_snapshot_line):
        return [{"id": digest, "action": "SNAPSHOT_UNRESOLVED", "messageTimestamp": ts, "text": text, "sourceUrl": source_url}]
    if events:
        return events
    if overall_action in {"ADD", "REMOVE"}:
        return [{"id": digest, "action": overall_action, "messageTimestamp": ts, "text": text, "unresolved": True, "sourceUrl": source_url}]
    return [{"id": digest, "action": "UNKNOWN", "messageTimestamp": ts, "text": text, "sourceUrl": source_url}]
