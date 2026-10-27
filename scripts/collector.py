import json, os, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from parser import parse_message

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/"data"; DATA_FILE=DATA/"data.json"; HISTORY_FILE=DATA/"messages.json"
STATE_FILE=DATA/"state.json"; CACHE_FILE=DATA/"coordinates.json"
TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
CHANNEL=os.environ.get("TELEGRAM_CHANNEL","@PlanKoverTDay").strip()
API=f"https://api.telegram.org/bot{TOKEN}"

def now(): return datetime.now(timezone.utc).isoformat()
def dt(v):
    try:
        x=datetime.fromisoformat((v or "").replace("Z","+00:00"))
        return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)
    except (ValueError,TypeError): return None
def read(path,default):
    try:
        with path.open(encoding="utf-8") as f:return json.load(f)
    except (OSError,json.JSONDecodeError):return default
def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf-8") as f:json.dump(obj,f,ensure_ascii=False,indent=2);f.write("\n")
    tmp.replace(path)
def canonical(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def build_data_payload(existing,restrictions,channel,updated_at):
    # Preserve updatedAt when the actual map content is unchanged, so periodic
    # polls without new Telegram activity do not create pointless commits.
    if existing.get("source")==channel and canonical(existing.get("restrictions",[]))==canonical(restrictions):
        return existing
    return {"updatedAt":updated_at,"source":channel,"restrictions":restrictions}
def api(method,params=None):
    if not TOKEN:raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN в GitHub Secrets.")
    req=urllib.request.Request(f"{API}/{method}",data=urllib.parse.urlencode(params or {}).encode(),headers={"Content-Type":"application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req,timeout=35) as r:result=json.loads(r.read().decode())
    except urllib.error.HTTPError as e:raise RuntimeError(f"Telegram API HTTP {e.code}: {e.read().decode(errors='replace')}") from e
    if not result.get("ok"):raise RuntimeError(f"Telegram API error: {result}")
    return result["result"]
def message_url(m):
    username=m.get("chat",{}).get("username"); mid=m.get("message_id")
    if username and mid:return f"https://t.me/{username}/{mid}"
    channel=CHANNEL.removeprefix("@")
    return f"https://t.me/{channel}/{mid}" if channel and mid else None
def polling_timeout():
    """Return a long-poll timeout that stays below the HTTP request timeout."""
    try:
        return min(25, max(0, int(os.environ.get("TELEGRAM_LONG_POLL_TIMEOUT_SECONDS", "0"))))
    except (TypeError, ValueError):
        return 0

def fetch_updates(offset):
    updates=api("getUpdates",{"offset":offset+1,"timeout":polling_timeout(),"allowed_updates":json.dumps(["channel_post","edited_channel_post"])})
    new_offset=offset; msgs=[]
    for u in updates:
        if isinstance(u.get("update_id"),int):new_offset=max(new_offset,u["update_id"])
        m=u.get("channel_post") or u.get("edited_channel_post")
        if m:msgs.append(m)
    return msgs,new_offset
# Manually verified coordinates for ambiguous place names and airports.
# Novoselovka is in Ekaterinovsky District, Saratov Oblast; its 150 km
# restriction explicitly references Saratov and Penza airports.
CURATED_COORDS = {
    # Москва: фиксируем центр города, а не геометрический центр его территории.
    "place:москва": {"lat": 55.7522, "lon": 37.6156},
    # Большое, Завидово: фиксируем координаты посадочной площадки.
    "airport:place:большое, завидово": {"lat": 56.5536, "lon": 36.5595},
    "place:новоселовка": {"lat": 51.9489, "lon": 44.2678},
    # Медведица, Жирновский район, Волгоградская область.
    "place:медведица": {"lat": 51.079559, "lon": 44.8182885},
    "icao:URKG": {"lat": 44.58209, "lon": 38.01248},
}


def cache_coords(name,icao,cache,airport=False):
    name=(name or "").strip(); icao=(icao or "").strip().upper()
    if not name:return None
    key=f"icao:{icao}" if icao else (f"airport:place:{name.casefold()}" if airport else f"place:{name.casefold()}")
    curated = CURATED_COORDS.get(key)
    if curated:
        if cache.get(key) != curated:
            cache[key] = curated
            write(CACHE_FILE, cache)
        return curated
    old=cache.get(key)
    if isinstance(old,dict) and "lat" in old and "lon" in old:return old
    if isinstance(old,dict) and old.get("notFoundAt"):
        stamp=dt(old["notFoundAt"])
        if stamp and datetime.now(timezone.utc)-stamp<timedelta(days=30):return None
    queries=[f"{name} airport, Russia",f"{name}, Russia"] if icao or airport or name.casefold().startswith("аэропорт ") else [f"{name}, Russia"]
    for query in queries:
        params=urllib.parse.urlencode({"q":query,"format":"jsonv2","limit":5,"countrycodes":"ru"})
        req=urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{params}",headers={"User-Agent":"PlankoverAirspaceMap/1.0 (public GitHub Pages project)"})
        try:
            time.sleep(1.1)
            with urllib.request.urlopen(req,timeout=20) as r:results=json.loads(r.read().decode())
            if results:
                result=results[0]
                if icao or airport:
                    for candidate in results:
                        cls=str(candidate.get("class","")).lower(); typ=str(candidate.get("type","")).lower(); display=str(candidate.get("display_name","")).lower()
                        if "aeroway" in cls or "aerodrome" in typ or "airport" in display:
                            result=candidate;break
                coords={"lat":float(result["lat"]),"lon":float(result["lon"])}
                cache[key]=coords;write(CACHE_FILE,cache);return coords
        except Exception as e:print(f"Не удалось найти координаты для {query}: {e}")
    cache[key]={"notFoundAt":now()};write(CACHE_FILE,cache);return None
def resolve_event_coords(event, cache):
    """Resolve restriction center and explicitly affected airports.

    Saransk was once published with Cheboksary's ICAO code (UWKS).
    Resolve it by its explicit name so the wrong code cannot move its marker.
    Do not change the global UWKS coordinates: they belong to Cheboksary.
    """
    def airport_coords(item):
        name=(item.get("name") or "").strip().casefold()
        if item.get("kind") == "airport" and name in {"саранск", "аэропорт саранск"}:
            return {"lat": 54.1274695, "lon": 45.2152086}
        return cache_coords(item.get("name"),item.get("icao"),cache,airport=item.get("kind")=="airport")

    coords=airport_coords(event)
    if coords:
        event["lat"],event["lon"]=coords["lat"],coords["lon"]
    else:
        event.pop("lat",None);event.pop("lon",None)

    for airport in event.get("affectedAirports",[]):
        coords=airport_coords(airport)
        if coords:
            airport["lat"],airport["lon"]=coords["lat"],coords["lon"]
        else:
            airport.pop("lat",None);airport.pop("lon",None)
    return event

def key(e):
    icao=(e.get("icao") or "").strip().upper(); name=(e.get("name") or "").strip().casefold()
    return f"icao:{icao}" if icao else f"place:{name}"
def build_active(history):
    active={}
    history=sorted(history,key=lambda r:dt(r.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
    for rec in history:
        events=rec.get("events",[])
        # Explicit "Актуальные ограничения" posts are snapshots. Replace the current
        # set only when every restriction-looking line was parsed successfully.
        if any(e.get("action")=="SNAPSHOT_UNRESOLVED" for e in events):
            print(f"Snapshot skipped (could not parse all lines): {rec.get('sourceUrl') or rec.get('messageId')}")
            continue
        snapshot_events=[e for e in events if e.get("snapshot") and e.get("action")=="ADD" and e.get("name")]
        if snapshot_events:
            active={key(e):dict(e) for e in snapshot_events}
            continue
        for e in events:
            action=e.get("action")
            if action=="CLEAR":active.clear()
            elif action=="REMOVE":
                targets=e.get("targets") or ([e] if e.get("name") else [])
                for target in targets:active.pop(key(target),None)
            elif action=="ADD" and e.get("name"):
                active[key(e)]=dict(e)
    return list(active.values())
def main():
    if not TOKEN:raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN в GitHub Secrets.")
    chat=api("getChat",{"chat_id":CHANNEL}); chat_id=chat["id"]
    state=read(STATE_FILE,{"lastUpdateId":-1}); offset=int(state.get("lastUpdateId",-1))
    updates,new_offset=fetch_updates(offset); history=read(HISTORY_FILE,[])
    original_history_json=canonical(history)
    index={(r.get("chatId"),r.get("messageId")):i for i,r in enumerate(history) if r.get("messageId") is not None}
    processed=0
    for m in updates:
        if m.get("chat",{}).get("id")!=chat_id:continue
        mid=m.get("message_id"); stamp=datetime.fromtimestamp(m["date"],timezone.utc).isoformat() if isinstance(m.get("date"),(int,float)) else now()
        text=m.get("text") or m.get("caption") or ""
        rec={"chatId":chat_id,"messageId":mid,"timestamp":stamp,"text":text,"sourceUrl":message_url(m),"events":parse_message(text,stamp,message_url(m))}
        k=(chat_id,mid)
        if k in index:history[index[k]]=rec
        else:history.append(rec)
        processed+=1
    # Deduplicate message edits already present in the legacy archive.
    # Keep the last archived version for each Telegram message ID; duplicate
    # edited copies otherwise replay stale content as if it were a new event.
    deduped = {}
    for position, rec in enumerate(history):
        if not isinstance(rec, dict):
            continue
        mid = rec.get("messageId")
        chat = rec.get("chatId")
        key_id = (chat, mid) if mid is not None else ("legacy", position)
        deduped[key_id] = rec
    history = list(deduped.values())

    # Re-parse saved messages with the current parser. This migrates old
    # UNKNOWN green-marker posts into explicit REMOVE events without needing
    # to fetch the channel's full historical backlog from Telegram.
    for rec in history:
        if isinstance(rec, dict) and isinstance(rec.get("text"), str):
            rec["events"] = parse_message(
                rec.get("text", ""),
                rec.get("timestamp"),
                rec.get("sourceUrl"),
            )

    history.sort(key=lambda r:dt(r.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
    # Avoid writing files when a poll found no new Telegram update and the
    # migrated archive is already stable. This keeps frequent schedules quiet.
    if canonical(history)!=original_history_json:
        write(HISTORY_FILE,history)
    if new_offset!=offset or processed:
        write(STATE_FILE,{"lastUpdateId":new_offset,"lastRunAt":now()})
    active=build_active(history);cache=read(CACHE_FILE,{})
    public=[]
    for e in active:
        # Active restrictions remain until an explicit removal/clear event.
        # Do not expire them merely because 24 hours have elapsed.
        # Airport markers are published only for airports explicitly listed
        # as affected in the source message; geographic proximity is not enough.
        public.append(resolve_event_coords(e,cache))
    existing_data=read(DATA_FILE,{})
    payload=build_data_payload(existing_data,public,CHANNEL,now())
    if canonical(payload)!=canonical(existing_data):
        write(DATA_FILE,payload)
    print(f"Telegram updates processed: {processed}")
    print(f"Messages in history: {len(history)}")
    print(f"Active restrictions published: {len(public)}")
if __name__=="__main__":main()
