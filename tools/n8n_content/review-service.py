"""Durable Telegram review coordinator; publication only follows a valid callback.

Uses a dedicated bot and long polling, because Hermes has no public webhook URL.
Spec and rollout checklist: ai-memory-hub/reviews/2026-10-01-n8n-content-pipeline.md
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

HUB = "https://crush.lu/hub/social/posts/"
HUB_UI = "https://hub.crush.lu/marketing/social"
LOCK = threading.RLock()
DB = Path(os.environ.get("REVIEW_DB", "/data/reviews.sqlite"))
TOKEN = os.environ.get("REVIEW_TOKEN", "")
BOT = os.environ.get("TELEGRAM_BOT_TOKEN", "")
HUB_KEY = os.environ.get("HUB_ADMIN_API_KEY", "")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
REVIEWERS = {
    int(v) for v in os.environ.get("TELEGRAM_REVIEWER_IDS", "").split(",") if v
}
REGENERATE = os.environ.get(
    "REGENERATE_URL", "http://n8n:5678/webhook/crush-visual-regenerate-v2"
)
ENABLE_PUBLISH = os.environ.get("ENABLE_PUBLISH", "false").lower() == "true"
PREVIEW_ONLY = os.environ.get("PREVIEW_ONLY", "false").lower() == "true"
POLL_CALLBACKS = os.environ.get("POLL_CALLBACKS", "true").lower() == "true"


@contextmanager
def database():
    connection = sqlite3.connect(DB)
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
            "CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, run_key TEXT UNIQUE, record TEXT NOT NULL)"
        )
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS errors (id TEXT PRIMARY KEY, record TEXT, notified INTEGER DEFAULT 0)"
        )


def load(review_id):
    if not re.fullmatch(r"[0-9a-f]{32}", review_id):
        raise ValueError("Invalid review identifier")
    with database() as db:
        row = db.execute(
            "SELECT record FROM reviews WHERE id=?", (review_id,)
        ).fetchone()
    if not row:
        raise ValueError("Unknown review")
    return json.loads(row[0])


def save(record):
    with database() as db:
        db.execute(
            "UPDATE reviews SET record=? WHERE id=?", (json.dumps(record), record["id"])
        )


def fingerprint(post):
    return hashlib.sha256(
        json.dumps(
            {
                k: post.get(k)
                for k in ("content", "media_urls", "media_url", "platforms", "language")
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def hub(method, post_id="", **kwargs):
    response = requests.request(
        method,
        HUB + (f"{post_id}/" if post_id else ""),
        headers={"Authorization": f"Bearer {HUB_KEY}"},
        timeout=(5, 75),
        **kwargs,
    )
    if not response.ok:
        raise RuntimeError(
            f"Hub returned HTTP {response.status_code}; inspect the post before retrying"
        )
    return response.json()["post"]


def telegram(method, payload):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT}/{method}", json=payload, timeout=(5, 40)
    )
    if not response.ok or not response.json().get("ok"):
        raise RuntimeError(f"Telegram {method} failed; HTTP {response.status_code}")
    return response.json()["result"]


def notify_review(record):
    script, post = record["script"], record["post"]
    source = (script.get("source_idea") or {}).get("facts", {})
    provenance = (
        f'\n\nPosting date: {script.get("posting_date", "today")}\n'
        f'Source: {source["source_url"]}\nChecked: {source["checked_at"][:10]}'
        if source.get("source_url") and source.get("checked_at")
        else ""
    )
    album_caption = (
        record.get("comparison_label")
        if record.get("visual_only")
        else script["slides"][0]["title"]
    )
    if record.get("preview_only"):
        with ExitStack() as handles:
            files = {
                f"slide{i}": (
                    Path(path).name,
                    handles.enter_context(open(path, "rb")),
                    "image/png",
                )
                for i, path in enumerate(record["preview_files"])
            }
            if len(files) == 5:
                method = "sendMediaGroup"
                payload = {
                    "chat_id": CHAT,
                    "media": json.dumps(
                        [
                            {
                                "type": "photo",
                                "media": f"attach://slide{i}",
                                **({"caption": album_caption} if i == 0 else {}),
                            }
                            for i in range(5)
                        ]
                    ),
                }
            else:
                method = "sendPhoto"
                payload = {
                    "chat_id": CHAT,
                    "photo": "attach://slide0",
                    "caption": album_caption,
                }
            response = requests.post(
                f"https://api.telegram.org/bot{BOT}/{method}",
                data=payload,
                files=files,
                timeout=(5, 75),
            )
            if not response.ok or not response.json().get("ok"):
                raise RuntimeError(
                    f"Telegram preview upload failed; HTTP {response.status_code}"
                )
            if record.get("visual_only"):
                sent = response.json()["result"]
                record["message_id"] = (
                    sent[0]["message_id"]
                    if isinstance(sent, list)
                    else sent["message_id"]
                )
                return  # Visual comparisons send pictures only, without post captions or buttons.
    elif len(post["media_urls"] or [post["media_url"]]) == 5:
        urls = post["media_urls"]
        telegram(
            "sendMediaGroup",
            {
                "chat_id": CHAT,
                "media": [
                    {
                        "type": "photo",
                        "media": url,
                        **({"caption": script["slides"][0]["title"]} if i == 0 else {}),
                    }
                    for i, url in enumerate(urls)
                ],
            },
        )
    else:
        telegram(
            "sendPhoto",
            {
                "chat_id": CHAT,
                "photo": post["media_url"],
                "caption": script["slides"][0]["title"],
            },
        )
    data = f'{record["id"]}:{record["revision"]}'
    buttons = [
        [{"text": "🔄 Regenerate visual", "callback_data": f"r:{data}"}],
    ]
    if not POLL_CALLBACKS:
        buttons = []
    if not record.get("preview_only"):
        buttons.append([{"text": "📝 Open in Hub CRM", "url": HUB_UI}])
    if POLL_CALLBACKS and ENABLE_PUBLISH and not record.get("preview_only"):
        buttons.insert(
            0, [{"text": "🚀 Publish to Buffer now", "callback_data": f"p:{data}"}]
        )
    message = telegram(
        "sendMessage",
        {
            "chat_id": CHAT,
            "text": f'FR\n{script["caption_fr"]}\n\nEN\n{script["caption_en"]}\n\n'
            + (
                "PREVIEW · Hub and publishing disabled"
                if record.get("preview_only")
                else f'Post {post["id"]}'
            )
            + f' · review {record["revision"]}'
            + provenance,
            "reply_markup": {"inline_keyboard": buttons},
        },
    )
    record["message_id"] = message["message_id"]


def deliver(body):
    script, images = body["script"], body["images"]
    if body.get("visual_only") is True and not PREVIEW_ONLY:
        raise ValueError("Visual-only delivery requires preview mode")
    if len(images) != len(script["slides"]) or len(images) not in {1, 5}:
        raise ValueError("A complete ordered image set is required")
    for key in ("caption_en", "caption_fr"):
        if not isinstance(script.get(key), str) or not 0 < len(script[key]) <= 1500:
            raise ValueError("Caption exceeds the Hub limit")
    run_key = str(body["run_key"])
    if len(run_key) > 200:
        raise ValueError("Invalid run key")
    with LOCK:
        if body.get("review_id"):
            record = load(body["review_id"])
            if record["state"] != "regenerating" or record["script"] != script:
                raise ValueError(
                    "Regeneration is no longer pending or copy was changed"
                )
            current = (
                record["post"]
                if record.get("preview_only")
                else hub("GET", record["post"]["id"])
            )
            if (
                current["status"] not in {"draft", "pending_review", "approved"}
                or current["buffer_id"]
            ):
                raise ValueError("A published or scheduled post cannot be regenerated")
            if fingerprint(current) != fingerprint(record["post"]):
                raise ValueError("Hub copy changed; regenerate from Hub instead")
            record["state"] = "upload_unknown"
            save(record)
        else:
            with database() as db:
                previous = db.execute(
                    "SELECT record FROM reviews WHERE run_key=?", (run_key,)
                ).fetchone()
                if previous:
                    old = json.loads(previous[0])
                    if old["state"] == "pending_review":
                        return {
                            "review_id": old["id"],
                            "post_id": old["post"]["id"],
                            "cached": True,
                        }
                    raise ValueError(
                        "Delivery already attempted; reconcile the saved review before retrying"
                    )
                record = {
                    "id": uuid.uuid4().hex,
                    "script": script,
                    "state": "upload_unknown",
                    "revision": 0,
                    "created": time.time(),
                    "preview_only": PREVIEW_ONLY,
                    "visual_only": body.get("visual_only") is True,
                    "comparison_label": str(
                        body.get("comparison_label", "Visual comparison")
                    )[:100],
                }
                db.execute(
                    "INSERT INTO reviews VALUES (?,?,?)",
                    (record["id"], run_key, json.dumps(record)),
                )
        files = []
        for i, image in enumerate(images):
            raw = base64.b64decode(image["image_base64"], validate=True)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Image exceeds 8 MiB")
            files.append(
                ("images", (f"slide-{i+1:02d}.png", io.BytesIO(raw), "image/png"))
            )
        if record.get("preview_only"):
            folder = DB.parent / "previews" / record["id"] / str(record["revision"] + 1)
            folder.mkdir(parents=True, exist_ok=False)
            record["preview_files"] = []
            for i, (_field, (_name, content, _mime)) in enumerate(files):
                path = folder / f"slide-{i+1:02d}.png"
                path.write_bytes(content.getvalue())
                record["preview_files"].append(str(path))
            record["post"] = {
                "id": "preview-" + record["id"],
                "status": "pending_review",
                "buffer_id": "",
            }
        elif record.get("post"):
            record["post"] = hub(
                "PATCH",
                record["post"]["id"],
                files=files,
                data={
                    "status": "pending_review",
                    "review_fingerprint": fingerprint(record["post"]),
                },
            )
        else:
            record["post"] = hub(
                "POST",
                files=files,
                data={
                    "hook": script["slides"][0]["title"],
                    "content": script[f'caption_{script["language"]}'],
                    "pillar": script["pillar"],
                    "language": script["language"],
                    "status": "pending_review",
                    "platforms": json.dumps(["instagram", "facebook"]),
                },
            )
        record["state"] = "delivery_unknown"
        record["revision"] += 1
        record["created"] = time.time()
        save(record)  # Hub identity is durable before any Telegram send.
        notify_review(record)
        record["state"] = "pending_review"
        save(record)
        return {"review_id": record["id"], "post_id": record["post"]["id"]}


def callback(query):
    if (
        query.get("from", {}).get("id") not in REVIEWERS
        or str(query.get("message", {}).get("chat", {}).get("id")) != CHAT
    ):
        return "This review is restricted to the configured reviewers."
    match = re.fullmatch(r"([pr]):([0-9a-f]{32}):(\d{1,6})", query.get("data", ""))
    if not match:
        return "Invalid action."
    action, review_id, revision = match.groups()
    return perform_action(
        action, review_id, int(revision), query["message"].get("message_id")
    )


def perform_action(action, review_id, revision, message_id=None):
    with LOCK:
        record = load(review_id)
        if record["state"] != "pending_review" or record["revision"] != revision:
            return "This review has already been used. Open the latest review or Hub."
        if message_id is not None and message_id != record.get("message_id"):
            return "This button belongs to an older review."
        if time.time() - record["created"] > 7 * 86400:
            return "Review expired. Open Hub to review current copy."
        if action == "p" and (not ENABLE_PUBLISH or record.get("preview_only")):
            return "Publishing is disabled. Use Hub."
        post = (
            record["post"]
            if record.get("preview_only")
            else hub("GET", record["post"]["id"])
        )
        if (
            post["status"] not in {"draft", "pending_review", "approved"}
            or post["buffer_id"]
        ):
            return "Post already dispatched or requires reconciliation in Hub."
        if fingerprint(post) != fingerprint(record["post"]):
            return "Post changed since this review. Review the current version in Hub."
        record["state"] = "publishing" if action == "p" else "regenerating"
        save(record)  # Consume the action BEFORE external writes; never auto-replay.
    if action == "p":
        profiles_response = requests.get(
            "https://crush.lu/hub/social/buffer-profiles/",
            headers={"Authorization": f"Bearer {HUB_KEY}"},
            timeout=(5, 30),
        )
        profiles_response.raise_for_status()
        profiles = profiles_response.json().get("items", [])
        selected = [
            profile
            for profile in profiles
            if profile.get("service") in {"instagram", "facebook"}
        ]
        if {profile["service"] for profile in selected} != {
            "instagram",
            "facebook",
        } or len(selected) != 2:
            raise ValueError(
                "Expected exactly one Instagram and one Facebook channel; configure in Hub"
            )
        record["post"] = hub(
            "PATCH",
            post["id"],
            json={
                "status": "scheduled",
                "review_fingerprint": fingerprint(post),
                "scheduled_for": (
                    datetime.now(timezone.utc) + timedelta(minutes=2)
                ).isoformat(),
                "buffer_profile_ids": [p["id"] for p in selected],
                "buffer_profile_platforms": {p["id"]: p["service"] for p in selected},
            },
        )
        record["state"] = "scheduled"
        save(record)
        return "Scheduled in Buffer for approximately two minutes from now."
    response = requests.post(
        REGENERATE,
        headers={"X-Review-Key": TOKEN},
        json={"review_id": review_id},
        timeout=(5, 15),
    )
    response.raise_for_status()
    return "Regenerating the cover; the copy and four advice cards stay the same."


def redact(value):
    text = str(value)
    for secret in (TOKEN, BOT, HUB_KEY):
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(Bearer\s+|api[_-]?key[=:]\s*)[^\s\"']+", r"\1[redacted]", text)
    text = re.sub(r"https?://\S+", "[URL omitted]", text)
    text = re.sub(r"(?:AQ\.|AIza)[A-Za-z0-9_-]+", "[redacted]", text)
    return text[:3000]


def dead_letter(body):
    record = {
        "workflow": redact(body.get("workflow", "unknown")),
        "execution": redact(body.get("execution", "unknown")),
        "node": redact(body.get("node", "unknown")),
        "message": redact(body.get("message", "error")),
        "stack": redact(body.get("stack", "")),
    }
    key = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    with database() as db:
        db.execute(
            "INSERT OR IGNORE INTO errors(id, record) VALUES (?,?)",
            (key, json.dumps(record)),
        )
    return {"stored": True, "id": key}


def flush_errors():
    with database() as db:
        rows = db.execute(
            "SELECT id, record FROM errors WHERE notified=0 LIMIT 5"
        ).fetchall()
    for key, raw in rows:
        record = json.loads(raw)
        telegram(
            "sendMessage",
            {
                "chat_id": CHAT,
                "text": f'Content pipeline failed\nWorkflow: {record["workflow"]}\nExecution: {record["execution"]}\nNode: {record["node"]}\n{record["message"]}\n{record["stack"]}'[
                    :3900
                ],
            },
        )
        with database() as db:
            db.execute("UPDATE errors SET notified=1 WHERE id=?", (key,))


def poll():
    # Never replace an existing webhook: this bot must have one consumer.
    if telegram("getWebhookInfo", {}).get("url"):
        raise RuntimeError(
            "The review bot already has a webhook; provision a dedicated bot"
        )
    while True:
        try:
            with database() as db:
                row = db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
            updates = telegram(
                "getUpdates",
                {
                    "offset": int(row[0]) if row else 0,
                    "timeout": 25,
                    "allowed_updates": ["callback_query"],
                },
            )
            for update in updates:
                query = update.get("callback_query")
                if query:
                    try:
                        result = callback(query)
                    except Exception as error:
                        result = "Action stopped. Inspect Hub before retrying."
                        dead_letter(
                            {
                                "workflow": "Telegram review",
                                "node": "callback",
                                "message": redact(error),
                                "execution": update["update_id"],
                            }
                        )
                    telegram(
                        "answerCallbackQuery",
                        {"callback_query_id": query["id"], "text": result[:190]},
                    )
                    telegram("sendMessage", {"chat_id": CHAT, "text": result})
                with database() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO meta VALUES ('offset',?)",
                        (str(update["update_id"] + 1),),
                    )
            flush_errors()
        except Exception as error:
            print(
                json.dumps({"component": "telegram-poller", "error": redact(error)}),
                flush=True,
            )
            time.sleep(5)


def poll_errors_only():
    while True:
        try:
            flush_errors()
        except Exception as error:
            print(
                json.dumps({"component": "error-alerts", "error": redact(error)}),
                flush=True,
            )
        time.sleep(10)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Access logs must not expose callback IDs or request bodies.

    def reply(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def authorized(self):
        return hmac.compare_digest(
            self.headers.get("Authorization", ""), f"Bearer {TOKEN}"
        )

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"status": "ok"})
        if not self.authorized():
            return self.reply(401, {"error": "Unauthorized"})
        try:
            if not self.path.startswith("/reviews/"):
                return self.reply(404, {"error": "Not found"})
            record = load(self.path.split("/")[-1])
            if record["state"] != "regenerating":
                raise ValueError("No regeneration is pending")
            self.reply(200, {"script": record["script"], "review_id": record["id"]})
        except ValueError as error:
            self.reply(409, {"error": str(error)})

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {"error": "Unauthorized"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 50 * 1024 * 1024:
                return self.reply(413, {"error": "Invalid request size"})
            body = json.loads(self.rfile.read(size))
            if self.path == "/deliver":
                return self.reply(200, deliver(body))
            if self.path == "/errors":
                return self.reply(200, dead_letter(body))
            if self.path == "/regenerate":
                with LOCK:
                    record = load(body["review_id"])
                    result = perform_action("r", record["id"], record["revision"])
                return self.reply(200, {"result": result})
            self.reply(404, {"error": "Not found"})
        except Exception as error:
            self.reply(409, {"error": redact(error)})


if __name__ == "__main__":
    if not all((len(TOKEN) >= 32, BOT, HUB_KEY, CHAT, REVIEWERS)):
        raise SystemExit(
            "Configure REVIEW_TOKEN, TELEGRAM_BOT_TOKEN, HUB_ADMIN_API_KEY, TELEGRAM_CHAT_ID and TELEGRAM_REVIEWER_IDS"
        )
    initialize()
    if POLL_CALLBACKS and telegram("getWebhookInfo", {}).get("url"):
        raise SystemExit(
            "Bot webhook exists; use a dedicated bot without changing the existing consumer"
        )
    if POLL_CALLBACKS:
        threading.Thread(target=poll, daemon=True).start()
    else:
        threading.Thread(target=poll_errors_only, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8093), Handler).serve_forever()
