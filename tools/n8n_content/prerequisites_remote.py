"""Read-only checks; secrets remain inside the remote process."""

import json
import sqlite3
import subprocess
import urllib.request
import urllib.error

DB = "/home/svc/.local/share/docker/volumes/n8n_n8n_data/_data/database.sqlite"
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
nodes = json.loads(
    db.execute(
        "SELECT nodes FROM workflow_entity WHERE id=?", ("RW5a8YsiCoIP28aq",)
    ).fetchone()[0]
)
key = next(
    h["value"].removeprefix("Bearer ")
    for n in nodes
    for h in n["parameters"].get("headerParameters", {}).get("parameters", [])
    if h.get("name", "").lower() == "authorization"
)
path = "/tmp/crush-review-telegram-prerequisite.json"
subprocess.run(
    [
        "docker",
        "exec",
        "n8n",
        "n8n",
        "export:credentials",
        "--id=JdXZV4rEcDwiotqx",
        "--decrypted",
        f"--output={path}",
    ],
    check=True,
    stdout=subprocess.DEVNULL,
)
try:
    credential = json.loads(
        subprocess.check_output(["docker", "exec", "n8n", "cat", path])
    )[0]
finally:
    subprocess.run(["docker", "exec", "n8n", "rm", path], check=True)
bot = credential["data"]["accessToken"]


def request(url, payload=None, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as e:
        return e.code, {}


status, data = request(
    "https://crush.lu/hub/social/posts/?limit=1",
    headers={"Authorization": f"Bearer {key}"},
)
posts = data.get("items", [])
print(
    json.dumps(
        {
            "hub_list_status": status,
            "carousel_field": bool(posts and "media_urls" in posts[0]),
            "list_keys": list(data),
        }
    )
)
if posts:
    status, data = request(
        f"https://crush.lu/hub/social/posts/{posts[0]['id']}/",
        headers={"Authorization": f"Bearer {key}"},
    )
    print(
        json.dumps(
            {
                "hub_detail_status": status,
                "detail_carousel_field": "media_urls" in data.get("post", {}),
            }
        )
    )
status, data = request(
    "https://crush.lu/hub/social/buffer-profiles/",
    headers={"Authorization": f"Bearer {key}"},
)
print(
    json.dumps(
        {
            "buffer_status": status,
            "channels": [p.get("service") for p in data.get("items", [])],
        }
    )
)
for method, payload in [
    ("getMe", {}),
    ("getWebhookInfo", {}),
    ("getChatAdministrators", {"chat_id": "-1004468501326"}),
]:
    status, data = request(
        f"https://api.telegram.org/bot{bot}/{method}",
        payload,
        {"Content-Type": "application/json"},
    )
    result = data.get("result", {})
    if method == "getMe":
        value = {"bot_username": result.get("username"), "bot_id": result.get("id")}
    elif method == "getWebhookInfo":
        value = {
            "has_webhook": bool(result.get("url")),
            "pending_updates": result.get("pending_update_count"),
        }
    else:
        value = (
            {
                "human_admins": [
                    {"id": a["user"]["id"], "first_name": a["user"].get("first_name")}
                    for a in result
                    if not a["user"].get("is_bot")
                ]
            }
            if isinstance(result, list)
            else {}
        )
    print(json.dumps({"method": method, "status": status, **value}))
print(
    json.dumps(
        {
            "credentials": [
                {"id": r[0], "name": r[1], "type": r[2]}
                for r in db.execute("SELECT id,name,type FROM credentials_entity")
            ]
        }
    )
)
print(
    json.dumps(
        {
            "projects": [
                list(r)
                for r in db.execute(
                    "SELECT projectId FROM shared_workflow WHERE workflowId=?",
                    ("RW5a8YsiCoIP28aq",),
                )
            ]
        }
    )
)
