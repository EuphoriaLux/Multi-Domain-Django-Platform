"""Bounded public-source collection and durable, date-aware content selection.

No model calls, media downloads, member data, Telegram sends or publishing.
Spec: ai-memory-hub/reviews/2026-10-01-firecrawl-idea-feed.md
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

DB = Path(os.environ.get("IDEA_DB", "/data/ideas.sqlite"))
TOKEN = os.environ.get("IDEA_TOKEN", "")
FIRECRAWL = os.environ.get("FIRECRAWL_URL", "http://hermes.fritz.box:3002")
SOURCES = json.loads(Path(__file__).with_name("idea-sources.json").read_text())
LOCK = threading.RLock()
REFRESH_LOCK = threading.Lock()
MAX_PAGES = 12
TZ = ZoneInfo("Europe/Luxembourg")


def today():
    return datetime.now(TZ).date()


@contextmanager
def database():
    connection = sqlite3.connect(DB, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS facts (id TEXT PRIMARY KEY, record TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS selections (run_key TEXT PRIMARY KEY, fact_id TEXT, idea_id TEXT, state TEXT, selected REAL, completed REAL, record TEXT)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, record TEXT)"
        )


def allowed(url, host):
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == host
        and parsed.port in (None, 443)
        and not parsed.username
        and not parsed.query
        and not parsed.fragment
    )


def api(path, body):
    response = requests.post(FIRECRAWL + path, json=body, timeout=(5, 35))
    if not response.ok:
        raise RuntimeError(f"Firecrawl HTTP {response.status_code}")
    result = response.json()
    if not result.get("success"):
        raise RuntimeError("Firecrawl could not read this source")
    return result.get("data", {})


def scrape(source):
    url = source["url"]
    if not allowed(url, source["host"]):
        raise ValueError("Source URL is outside the allowlist")
    data = api(
        "/v2/scrape",
        {
            "url": url,
            "formats": ["markdown", "links"],
            "includeTags": ["main"],
            "onlyMainContent": True,
            "maxAge": 0,
            "timeout": 25000,
        },
    )
    metadata = data.get("metadata", {})
    final_url = metadata.get("url") or metadata.get("sourceURL") or url
    if metadata.get("statusCode") != 200 or not allowed(final_url, source["host"]):
        raise ValueError("Source returned an error or redirected outside its host")
    if len(data.get("markdown", "")) < 100:
        raise ValueError("Source has no usable main content")
    return data


def plain(markdown):
    # Discard all image references, including temporary signed private-media URLs.
    text = re.sub(r"!\[[^\]]*\]\([^\n]*?\)", "", markdown)
    text = re.sub(r"\[([^\]]*)\]\([^\n]*?\)", r"\1", text)
    return re.sub(r"[*_#|]", "", text)


def main_text(source, data):
    text = plain(data["markdown"])
    title = str(data.get("metadata", {}).get("title", ""))
    title = re.sub(r"^(Mudam\s*\|\s*)", "", title)
    title = re.split(
        r" - (?:Visit|Statistics|Crush\.lu)| \| (?:Ville|Crush)| – \d", title
    )[0]
    # Start at the actual article heading, not the website navigation.
    heading = re.search(
        r"(?im)^\s*" + re.escape(plain(title).strip()) + r"\s*\n[=-]{3,}\s*$", text
    )
    index = heading.start() if heading else text.lower().find(title.lower())
    if index >= 0:
        text = text[index:]
    text = re.split(
        r"\n\s*(?:Next events|Other Tours|Your Event Coaches|Points of interest)\b",
        text,
    )[0]
    return title[:180], text[:12000]


def event_date(text, first_party=False):
    if first_party:
        # Use the dedicated Date & Time block, never dates inside the prose.
        text = text.split("Date & Time", 1)[-1].split("Location", 1)[0][:500]
        months = {
            name: n
            for n, name in enumerate(
                [
                    "January",
                    "February",
                    "March",
                    "April",
                    "May",
                    "June",
                    "July",
                    "August",
                    "September",
                    "October",
                    "November",
                    "December",
                ],
                1,
            )
        }
        months.update({name[:3]: n for name, n in list(months.items())})
        match = re.search(r"\b([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(20\d{2})", text)
        if match and match[1] in months:
            return date(int(match[3]), months[match[1]], int(match[2])).isoformat()
        return None
    # A date printed in the primary event heading/details, before related events.
    text = text.split("Prévente", 1)[0].split("Pre-sale", 1)[0][:2200]
    match = re.search(r"\b(\d{1,2})\.\s*[–-]\s*\d{1,2}\.(\d{1,2})\.(20\d{2})\b", text)
    if match:
        return date(int(match[3]), int(match[2]), int(match[1])).isoformat()
    match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b", text)
    if match:
        return date(int(match[3]), int(match[2]), int(match[1])).isoformat()
    return None  # Missing/ambiguous dates cannot become timely event ideas.


def normalized(value):
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"\b20\d{2}\b", "", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def fact_from(source, data):
    title, text = main_text(source, data)
    if not title or source["type"] in {"directory", "search"}:
        return None
    when = None
    if source["type"] == "event":
        if source["host"] == "crush.lu":
            when = event_date(text, True)
        elif source["host"] == "www.visitluxembourg.com":
            # The primary date is above the heading; related cards cannot supply it.
            match = re.search(r"When\?[^\n]{0,100}", plain(data["markdown"])[:600])
            when = event_date(match[0]) if match else None
        elif source["host"] == "www.mudam.com":
            match = re.search(r"Quand\s*([^\n]+)", text)
            when = event_date(match[0]) if match else None
    if source["type"] == "event" and (not when or date.fromisoformat(when) < today()):
        return None
    # Official event titles can differ by venue/language for the same city-wide event.
    canonical = (
        "nuit des musees"
        if "nuit des musees" in normalized(title)
        else normalized(title)
    )
    key = hashlib.sha256(f"{canonical}:{when or source['id']}".encode()).hexdigest()[
        :24
    ]
    details = {}
    if source["host"] == "crush.lu":
        block = text.split("Date & Time", 1)[-1].split("Location", 1)[0]
        clock = re.search(r"\b\d{1,2}(?::\d{2})?\s+[ap]\.m\.", block)
        if clock:
            details["start_time"] = clock[0]
        price = re.search(r"\nPrice\s*\n\s*(Free|€\d+(?:[.,]\d{2})?)\s*\n", text)
        if price:
            details["listed_price"] = price[1]
    registered_names = {
        "remich-walk": ["Remich", "Moselle", "Jeannot Belling"],
        "grund-walk": ["Bock", "Corniche", "Grund", "Rham Plateau"],
        "esch-culture": ["Esch-sur-Alzette", "Kulturfabrik", "Escher Theatre"],
        "languages": ["Luxembourgish", "French", "English", "German", "Portuguese"],
    }
    if source["id"] in registered_names:
        details[
            "mentioned_places" if source["type"] == "place" else "mentioned_languages"
        ] = [
            name
            for name in registered_names[source["id"]]
            if name.lower() in text.lower()
        ]
    return {
        "id": key,
        "title": title,
        "source_url": source["url"],
        "source_id": source["id"],
        "source_type": source["type"],
        "region": source["region"],
        "first_party": source["host"] == "crush.lu",
        "event_date": when,
        "checked_at": datetime.now(TZ).isoformat(),
        "priority": source["priority"],
        # Only titles, dates and registered locations enter the model prompt.
        "data_period": "2021 census" if source["type"] == "context" else None,
        "details": details,
    }


def discover(source, data):
    text = data["markdown"]
    urls = re.findall(r"https://crush\.lu/en/events/\d+/", text)
    return [
        {
            **source,
            "id": f"crush-event-{url.rstrip('/').split('/')[-1]}",
            "url": url,
            "type": "event",
        }
        for url in dict.fromkeys(urls)
    ][:4]


def refresh():
    if not REFRESH_LOCK.acquire(blocking=False):
        return {"status": "already_running"}
    try:
        queue = [s for s in SOURCES if s["type"] != "search"]
        failures, stored, visited = [], [], set()
        # Keep checking previously discovered events even when today's search differs.
        with database() as db:
            previous = [
                json.loads(r[0]) for r in db.execute("SELECT record FROM facts")
            ]
        for fact in previous:
            source = next(
                (
                    s
                    for s in SOURCES
                    if s["type"] == "search"
                    and fact["source_id"].startswith(s["id"] + ":")
                ),
                None,
            )
            if source and allowed(fact["source_url"], source["host"]):
                queue.append(
                    {
                        **source,
                        "id": fact["source_id"],
                        "url": fact["source_url"],
                        "type": "event",
                    }
                )
        # Two restricted searches; discovering a result does not make it a verified fact.
        for source in [s for s in SOURCES if s["type"] == "search"]:
            try:
                period = today().strftime("%B %Y")
                result = api(
                    "/v2/search",
                    {
                        "query": f"site:{source['host']} events {period} Luxembourg",
                        "limit": 3,
                    },
                )
                for item in result.get("web", []):
                    url = item.get("url", "")
                    if allowed(url, source["host"]) and (
                        "/event/" in url or "/agenda/evenements/" in url
                    ):
                        queue.append(
                            {
                                **source,
                                "url": url,
                                "id": source["id"]
                                + ":"
                                + hashlib.sha256(url.encode()).hexdigest()[:12],
                                "type": "event",
                            }
                        )
            except Exception:
                failures.append(
                    {
                        "source": source["id"],
                        "error": "Search unavailable; configured sources retained",
                    }
                )
        # Source discovery goes first so Crush events cannot be displaced by search results.
        count = 0
        while queue and count < MAX_PAGES:
            source = queue.pop(0)
            if source["url"] in visited:
                continue
            visited.add(source["url"])
            count += 1
            try:
                data = scrape(source)
                if source["type"] == "directory":
                    queue[0:0] = discover(source, data)
                    continue
                fact = fact_from(source, data)
                with database() as db:
                    db.execute(
                        "DELETE FROM facts WHERE json_extract(record,'$.source_id')=? AND id!=?",
                        (source["id"], fact["id"] if fact else ""),
                    )
                    if not fact:
                        continue
                    old = db.execute(
                        "SELECT record FROM facts WHERE id=?", (fact["id"],)
                    ).fetchone()
                    if old and json.loads(old[0])["priority"] > fact["priority"]:
                        continue
                    db.execute(
                        "INSERT OR REPLACE INTO facts VALUES (?,?)",
                        (fact["id"], json.dumps(fact)),
                    )
                stored.append(fact["id"])
            except Exception:
                failures.append(
                    {
                        "source": source["id"],
                        "error": "Read failed; last successful facts retained subject to freshness limits",
                    }
                )
        result = {
            "status": "ok" if stored else "no_fresh_facts",
            "checked_at": datetime.now(TZ).isoformat(),
            "pages_read": count,
            "facts_refreshed": len(set(stored)),
            "failures": failures,
        }
        with database() as db:
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('refresh',?)",
                (json.dumps(result),),
            )
        return result
    finally:
        REFRESH_LOCK.release()


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("posting_date must be YYYY-MM-DD")
    result = date.fromisoformat(value)
    if not today() <= result <= today() + timedelta(days=90):
        raise ValueError("posting_date must be today or within the next 90 days")
    return result


def candidates(fact, kind):
    if fact["source_type"] == "context":
        angles = [
            (
                "language",
                "Opening a conversation across languages",
                "icebreakers",
                "ghost_scene_chatting_pair.svg",
            )
        ]
    elif fact["first_party"]:
        angles = [
            (
                "prepare",
                "Arriving at a Crush event with one easy conversation starter",
                "icebreakers",
                "ghost_scene_party.svg",
            ),
            (
                "expect",
                "What to expect at this upcoming Crush event",
                "first_date_lux",
                "ghost_scene_party.svg",
            ),
        ]
    else:
        angles = [
            (
                "conversation",
                "Let your surroundings start the conversation",
                "icebreakers",
                "ghost_scene_chatting_pair.svg",
            ),
            (
                "date",
                "Plan a relaxed first meeting around a local place or activity",
                "first_date_lux",
                "ghost_scene_discover.svg",
            ),
            (
                "fresh",
                "Try a new setting when you are starting fresh",
                "confidence_reset",
                "ghost_scene_unlock.svg",
            ),
        ]
    output = []
    for angle, name, theme, scene in angles:
        name = f"{name}: {fact['title']}"[:200]
        facts = {
            k: fact[k]
            for k in [
                "title",
                "source_url",
                "region",
                "event_date",
                "checked_at",
                "first_party",
                "data_period",
            ]
        }
        facts["details"] = fact.get("details", {})
        output.append(
            {
                "id": f"{fact['id']}:{angle}",
                "fact_id": fact["id"],
                "name": name,
                "theme": theme,
                "ghostScene": scene,
                "hubPillar": (
                    "promo"
                    if fact["first_party"] and kind == "editorial"
                    else "dating_tip"
                ),
                "description": "Create practical, respectful advice for adults in Luxembourg. The suggested angle is editorial guidance. Only the supplied factual fields are verified. Do not invent opening hours, prices, accessibility, popularity or event activities. Third-party activities are independent suggestions, never Crush events or partnerships.",
                "mascotAction": f"Crushy in a welcoming scene inspired by {fact['region']}; no text or event branding.",
                "facts": facts,
            }
        )
    return output


def select(body):
    posting = valid_date(body.get("posting_date"))
    kind = body.get("kind")
    run_key = body.get("run_key")
    if (
        kind not in {"carousel", "editorial"}
        or not isinstance(run_key, str)
        or not 1 <= len(run_key) <= 200
    ):
        raise ValueError("Invalid selection request")
    fallback = {
        "idea": None,
        "posting_date": posting.isoformat(),
        "status": "evergreen_fallback",
    }
    if body.get("skip_feed") is True:
        return {**fallback, "status": "explicit_topic"}
    now = time.time()
    with LOCK, database() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute(
            "SELECT record FROM selections WHERE run_key=?", (run_key,)
        ).fetchone()
        if old:
            return json.loads(old[0])
        recent = db.execute(
            "SELECT fact_id,idea_id,completed,record FROM selections WHERE (state='completed' AND completed>?) OR (state='reserved' AND selected>?)",
            (now - 14 * 86400, now - 3600),
        ).fetchall()
        used_ids = {r["idea_id"] for r in recent}
        used_facts = {
            r["fact_id"]
            for r in recent
            if not r["completed"] or r["completed"] > now - 7 * 86400
        }
        last = max(recent, key=lambda r: r["completed"] or now, default=None)
        last_region = (
            json.loads(last["record"])["idea"]["facts"]["region"] if last else None
        )
        available = []
        for row in db.execute("SELECT record FROM facts"):
            fact = json.loads(row[0])
            age = (
                datetime.now(TZ) - datetime.fromisoformat(fact["checked_at"])
            ).total_seconds()
            ttl = 48 * 3600 if fact["event_date"] else 14 * 86400
            if age > ttl or fact["id"] in used_facts:
                continue
            if fact["event_date"]:
                days = (date.fromisoformat(fact["event_date"]) - posting).days
                if not 1 <= days <= 21:
                    continue  # Same-day promotion could be after registration closes.
            for idea in candidates(fact, kind):
                if idea["id"] in used_ids:
                    continue
                score = fact["priority"] + (
                    20 if fact["first_party"] and kind == "editorial" else 0
                )
                score -= 35 if fact["region"] == last_region else 0
                score += 5 if fact["event_date"] else 0
                available.append((score, idea["id"], idea))
        if not available:
            return fallback
        idea = max(available, key=lambda item: (item[0], item[1]))[2]
        result = {
            "idea": idea,
            "posting_date": posting.isoformat(),
            "status": "sourced",
        }
        db.execute(
            "INSERT INTO selections VALUES (?,?,?,'reserved',?,NULL,?)",
            (run_key, idea["fact_id"], idea["id"], now, json.dumps(result)),
        )
        return result


def complete(body):
    if not isinstance(body.get("run_key"), str):
        raise ValueError("Invalid run key")
    with LOCK, database() as db:
        row = db.execute(
            "SELECT state FROM selections WHERE run_key=?", (body["run_key"],)
        ).fetchone()
        if not row:
            return {"status": "fallback_no_selection"}
        db.execute(
            "UPDATE selections SET state='completed',completed=COALESCE(completed,?) WHERE run_key=?",
            (time.time(), body["run_key"]),
        )
    return {"status": "completed"}


def inventory():
    with database() as db:
        facts = [json.loads(r[0]) for r in db.execute("SELECT record FROM facts")]
        meta = db.execute("SELECT record FROM meta WHERE key='refresh'").fetchone()
        return {
            "facts": facts,
            "last_refresh": json.loads(meta[0]) if meta else None,
            "completed_selections": db.execute(
                "SELECT COUNT(*) FROM selections WHERE state='completed'"
            ).fetchone()[0],
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def authorized(self):
        return bool(TOKEN) and hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + TOKEN
        )

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"status": "ok"})
        if not self.authorized():
            return self.reply(401, {"error": "Unauthorized"})
        if self.path == "/ideas":
            return self.reply(200, inventory())
        self.reply(404, {"error": "Not found"})

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {"error": "Unauthorized"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                return self.reply(413, {"error": "Invalid request size"})
            body = json.loads(self.rfile.read(size))
            action = {
                "/refresh": lambda _body: refresh(),
                "/select": select,
                "/complete": complete,
            }.get(self.path)
            if not action:
                return self.reply(404, {"error": "Not found"})
            self.reply(200, action(body))
        except (ValueError, TypeError, KeyError):
            self.reply(
                400, {"error": "Invalid request; check posting_date and run_key"}
            )
        except Exception:
            self.reply(503, {"error": "Idea feed unavailable"})


if __name__ == "__main__":
    if len(TOKEN) < 32:
        raise SystemExit("Configure IDEA_TOKEN")
    initialize()
    ThreadingHTTPServer(("0.0.0.0", 8094), Handler).serve_forever()
