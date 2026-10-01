"""Install preview sidecars and inactive imports; run only on authorized Hermes."""

import json
import os
import secrets
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

os.umask(0o077)
ROOT = Path("/home/svc/stacks/crush-content-v2")
assert Path.cwd().resolve() == ROOT
DB = Path("/home/svc/.local/share/docker/volumes/n8n_n8n_data/_data/database.sqlite")
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
originals = ["RW5a8YsiCoIP28aq", "c0ach1ngCar0usel"]
for raw, active in db.execute("SELECT nodes,active FROM workflow_entity"):
    if active and any(
        n["type"] == "n8n-nodes-base.telegramTrigger" for n in json.loads(raw)
    ):
        raise SystemExit("Existing Telegram trigger found; use a dedicated bot.")
project = db.execute(
    "SELECT projectId FROM shared_workflow WHERE workflowId=?", (originals[0],)
).fetchone()[0]
nodes = json.loads(
    db.execute(
        "SELECT nodes FROM workflow_entity WHERE id=?", (originals[0],)
    ).fetchone()[0]
)
key = next(
    h["value"].removeprefix("Bearer ")
    for n in nodes
    for h in n["parameters"].get("headerParameters", {}).get("parameters", [])
    if h.get("name", "").lower() == "authorization"
)


def run(*args):
    output = subprocess.check_output(list(args), stderr=subprocess.STDOUT, text=True)
    # CLI command output contains counts and IDs, never credentials.
    print(output.strip(), flush=True)


def export_credential(credential_id):
    path = "/tmp/crush-v2-credential.json"
    subprocess.run(
        [
            "docker",
            "exec",
            "n8n",
            "n8n",
            "export:credentials",
            f"--id={credential_id}",
            "--decrypted",
            f"--output={path}",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        return json.loads(
            subprocess.check_output(["docker", "exec", "n8n", "cat", path])
        )[0]
    finally:
        subprocess.run(["docker", "exec", "n8n", "rm", path], check=True)


bot = export_credential("JdXZV4rEcDwiotqx")["data"]["accessToken"]
# Another local container must not be consuming the same bot.
for container in subprocess.check_output(
    ["docker", "ps", "--format", "{{.Names}}"], text=True
).splitlines():
    environment = json.loads(subprocess.check_output(["docker", "inspect", container]))[
        0
    ]["Config"].get("Env", [])
    if any(bot in entry for entry in environment) and not container.startswith(
        "crush-content-v2-content-review"
    ):
        raise SystemExit(
            f"Existing bot configured in {container}; verify ownership first."
        )
backup = ROOT / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup.mkdir(parents=True)
destination = sqlite3.connect(backup / "database.sqlite")
db.backup(destination)
destination.close()
run(
    "docker",
    "exec",
    "n8n",
    "n8n",
    "export:workflow",
    "--all",
    "--output=/tmp/crush-v2-backup.json",
)
run("docker", "cp", "n8n:/tmp/crush-v2-backup.json", str(backup / "workflows.json"))
os.chmod(backup / "workflows.json", 0o600)
run("docker", "exec", "n8n", "rm", "/tmp/crush-v2-backup.json")
env_path = ROOT / ".env"
if env_path.exists():
    values = dict(
        line.split("=", 1) for line in env_path.read_text().splitlines() if "=" in line
    )
else:
    values = {
        "RENDERER_TOKEN": secrets.token_urlsafe(48),
        "REVIEW_TOKEN": secrets.token_urlsafe(48),
        "GENERATION_TOKEN": secrets.token_urlsafe(48),
        "TELEGRAM_BOT_TOKEN": bot,
        "HUB_ADMIN_API_KEY": key,
        "TELEGRAM_CHAT_ID": "-1004468501326",
        "TELEGRAM_REVIEWER_IDS": "7483594372",
        "ENABLE_PUBLISH": "false",
        "PREVIEW_ONLY": "true",
        "POLL_CALLBACKS": "false",
    }
    env_path.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
    os.chmod(env_path, 0o600)
assert values["PREVIEW_ONLY"] == "true" and values["ENABLE_PUBLISH"] == "false"
mapping = {
    "Crush Subscription Bridge": "i8FK7dr4ix27nNa9",
    "Crush Gemini API": "XNa8UVTFu5pnNCj6",
}
credentials = []
for name, cid, header, value in [
    (
        "Crush Renderer Bearer",
        "CrushRenderCredV2",
        "Authorization",
        "Bearer " + values["RENDERER_TOKEN"],
    ),
    (
        "Crush Review Bearer",
        "CrushReviewCredV2",
        "Authorization",
        "Bearer " + values["REVIEW_TOKEN"],
    ),
    (
        "Crush Generation Webhook",
        "CrushGenerateV2",
        "X-Generation-Key",
        values["GENERATION_TOKEN"],
    ),
    (
        "Crush Regeneration Webhook",
        "CrushRegenCredV2",
        "X-Review-Key",
        values["REVIEW_TOKEN"],
    ),
]:
    mapping[name] = cid
    credentials.append(
        {
            "id": cid,
            "name": name,
            "type": "httpHeaderAuth",
            "data": {"name": header, "value": value},
        }
    )
path = ROOT / "credentials.import.json"
path.write_text(json.dumps(credentials))
try:
    run("docker", "cp", str(path), "n8n:/tmp/crush-v2-credentials.json")
    run(
        "docker",
        "exec",
        "-u",
        "0",
        "n8n",
        "chown",
        "node:node",
        "/tmp/crush-v2-credentials.json",
    )
    run(
        "docker",
        "exec",
        "n8n",
        "n8n",
        "import:credentials",
        "--input=/tmp/crush-v2-credentials.json",
        f"--projectId={project}",
    )
finally:
    path.unlink(missing_ok=True)
    run("docker", "exec", "n8n", "rm", "-f", "/tmp/crush-v2-credentials.json")
ids = {
    "errors": "CrushErrorsV2",
    "regenerate": "CrushRegenerateV2",
    "editorial": "CrushEditorialV2",
    "carousel": "CrushCarouselV2",
}
workflows = []
for kind, workflow_id in ids.items():
    wf = json.loads((ROOT / f"{kind}.workflow.json").read_text())
    wf.update(id=workflow_id, active=False)
    for node in wf["nodes"]:
        if node["type"] == "n8n-nodes-base.scheduleTrigger":
            node["disabled"] = True
        if node["type"] == "n8n-nodes-base.webhook":
            node["webhookId"] = str(uuid.uuid4())
        for credential in node.get("credentials", {}).values():
            credential["id"] = mapping[credential["name"]]
    if kind != "errors":
        wf["settings"]["errorWorkflow"] = ids["errors"]
    workflows.append(wf)
path = ROOT / "workflows.import.json"
path.write_text(json.dumps(workflows))
run("docker", "cp", str(path), "n8n:/tmp/crush-v2-workflows.json")
run(
    "docker",
    "exec",
    "-u",
    "0",
    "n8n",
    "chown",
    "node:node",
    "/tmp/crush-v2-workflows.json",
)
run(
    "docker",
    "exec",
    "n8n",
    "n8n",
    "import:workflow",
    "--input=/tmp/crush-v2-workflows.json",
    f"--projectId={project}",
)
run("docker", "exec", "n8n", "rm", "/tmp/crush-v2-workflows.json")
(ROOT / "deployment.json").write_text(
    json.dumps(
        {
            "workflow_ids": ids,
            "project_id": project,
            "backup": str(backup),
            "mode": "preview",
            "schedules_enabled": False,
        },
        indent=2,
    )
)
run("docker", "compose", "--project-name", "crush-content-v2", "build")
run("docker", "compose", "--project-name", "crush-content-v2", "up", "-d")
print(
    json.dumps(
        {
            "prepared": True,
            "workflow_ids": ids,
            "backup": str(backup),
            "mode": "preview",
            "schedules_enabled": False,
        }
    )
)
