"""Run prepare before copying files, then install on the authorized Hermes host."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.umask(0o077)
ROOT = Path("/home/svc/stacks/crush-content-v2")
assert Path.cwd().resolve() == ROOT
STATE = ROOT / "ideas-deployment.json"
DB = Path("/home/svc/.local/share/docker/volumes/n8n_n8n_data/_data/database.sqlite")
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
key = db.execute(
    "SELECT apiKey FROM user_api_keys WHERE audience='public-api' LIMIT 1"
).fetchone()[0]


def api(path, method="GET", body=None):
    request = Request(
        "http://127.0.0.1:5678/api/v1" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-N8N-API-KEY": key, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"n8n API {method} {path}: HTTP {error.code}") from None


def signature(wf):
    return hashlib.sha256(
        json.dumps(
            {k: wf.get(k) for k in ["nodes", "connections", "settings", "active"]},
            sort_keys=True,
        ).encode()
    ).hexdigest()


def preview_guard():
    env = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text().splitlines()
        if "=" in line
    )
    assert env.get("PREVIEW_ONLY") == "true"
    assert env.get("ENABLE_PUBLISH") == "false"
    assert env.get("POLL_CALLBACKS") == "false"
    return env


def run(*args):
    subprocess.run(args, check=True)


if sys.argv[1] == "prepare":
    preview_guard()
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    backup = (
        ROOT / "backups" / datetime.now(timezone.utc).strftime("ideas-%Y%m%dT%H%M%SZ")
    )
    backup.mkdir(parents=True)
    for filename in [
        "compose.yaml",
        "review-service.py",
        "editorial.workflow.json",
        "carousel.workflow.json",
        "idea-feed.py",
        "idea-sources.json",
        "Dockerfile.ideas",
        "ideas.workflow.json",
    ]:
        if (ROOT / filename).exists():
            shutil.copy2(ROOT / filename, backup / filename)
    snapshots = {}
    for wid in [
        "CrushEditorialV2",
        "CrushCarouselV2",
        "RW5a8YsiCoIP28aq",
        "c0ach1ngCar0usel",
    ]:
        wf = api("/workflows/" + wid)
        (backup / (wid + ".json")).write_text(json.dumps(wf))
        snapshots[wid] = signature(wf)
    STATE.write_text(
        json.dumps(
            {
                "backup": str(backup),
                "before": snapshots,
                "installed": previous.get("installed", {}),
            }
        )
    )
    print(json.dumps({"prepared": True, "backup": str(backup)}))
elif sys.argv[1] == "install":
    preview_guard()
    state = json.loads(STATE.read_text())
    for wid, before in state["before"].items():
        assert (
            signature(api("/workflows/" + wid)) == before
        ), f"Workflow changed since backup: {wid}"
    run(
        "docker",
        "compose",
        "--project-name",
        "crush-content-v2",
        "build",
        "content-ideas",
        "content-review",
    )
    run(
        "docker",
        "compose",
        "--project-name",
        "crush-content-v2",
        "up",
        "-d",
        "content-ideas",
        "content-review",
    )
    probe = "import urllib.request;print(urllib.request.urlopen('http://localhost:8094/healthz',timeout=5).status)"
    for attempt in range(15):
        check = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                "crush-content-v2",
                "exec",
                "-T",
                "content-ideas",
                "python",
                "-c",
                probe,
            ],
            capture_output=True,
            text=True,
        )
        if check.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError(
            "Idea service did not start; workflow definitions were not changed"
        )
    mapping = {
        "Crush Subscription Bridge": "i8FK7dr4ix27nNa9",
        "Crush Gemini API": "XNa8UVTFu5pnNCj6",
        "Crush Review Bearer": "CrushReviewCredV2",
        "Crush Renderer Bearer": "CrushRenderCredV2",
        "Crush Generation Webhook": "CrushGenerateV2",
    }
    installed = {}
    for kind, wid in [
        ("editorial", "CrushEditorialV2"),
        ("carousel", "CrushCarouselV2"),
        ("ideas", state.get("installed", {}).get("ideas")),
    ]:
        old = api("/workflows/" + wid) if wid else None
        wf = json.loads((ROOT / (kind + ".workflow.json")).read_text())
        for node in wf["nodes"]:
            for cred in node.get("credentials", {}).values():
                cred["id"] = mapping[cred["name"]]
            if node["type"] == "n8n-nodes-base.scheduleTrigger" and kind != "ideas":
                node["disabled"] = True
            if node["type"] == "n8n-nodes-base.webhook":
                previous = next(
                    (
                        n
                        for n in (old or {}).get("nodes", [])
                        if n["name"] == node["name"]
                    ),
                    None,
                )
                node["webhookId"] = (
                    previous.get("webhookId") if previous else str(uuid.uuid4())
                )
        wf["settings"]["errorWorkflow"] = "CrushErrorsV2"
        payload = {k: wf[k] for k in ["name", "nodes", "connections", "settings"]}
        result = (
            api("/workflows/" + wid, "PUT", payload)
            if wid
            else api("/workflows", "POST", payload)
        )
        wid = result["id"]
        # Refresh published webhook versions; both generator schedules remain disabled.
        assert kind == "ideas" or all(
            n.get("disabled")
            for n in result["nodes"]
            if n["type"] == "n8n-nodes-base.scheduleTrigger"
        )
        preview_guard()
        api(
            "/workflows/" + wid + "/activate",
            "POST",
            {"versionId": result["versionId"]},
        )
        installed[kind] = wid
    for wid in ["RW5a8YsiCoIP28aq", "c0ach1ngCar0usel"]:
        assert (
            signature(api("/workflows/" + wid)) == state["before"][wid]
        ), "Original workflow changed"
    state.update(
        installed=installed,
        mode="preview",
        collector_schedule="daily 08:00 Europe/Luxembourg",
        generator_schedules=False,
    )
    STATE.write_text(json.dumps(state, indent=2))
    print(
        json.dumps(
            {
                "installed": installed,
                "mode": "preview",
                "collector_schedule": state["collector_schedule"],
                "generator_schedules": False,
            }
        )
    )
else:
    raise SystemExit("Use prepare or install")
