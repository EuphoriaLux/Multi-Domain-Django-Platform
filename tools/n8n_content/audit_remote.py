"""Read-only, secret-redacted n8n inventory; pipe to SSH python3 on Hermes."""

import json
import re
import sqlite3
import subprocess


def docker(*args):
    return subprocess.check_output(["docker", "exec", "n8n", *args], text=True)


db_path = "/home/svc/.local/share/docker/volumes/n8n_n8n_data/_data/database.sqlite"
db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
workflows = []
for row in db.execute("SELECT * FROM workflow_entity"):
    if row["id"] not in {"RW5a8YsiCoIP28aq", "c0ach1ngCar0usel"}:
        continue
    item = dict(row)
    for key in ("nodes", "connections", "settings", "staticData"):
        if item.get(key):
            item[key] = json.loads(item[key])
    for node in item["nodes"]:
        p = node["parameters"]
        for header in p.get("headerParameters", {}).get("parameters", []):
            if header.get("name", "").lower() in {
                "authorization",
                "x-api-key",
                "x-n8n-api-key",
            }:
                header["value"] = "REDACTED_USE_CREDENTIAL"
        if "jsCode" in p:
            p["jsCode"] = re.sub(
                r"(?<=Bearer )[A-Za-z0-9_.-]{12,}", "REDACTED", p["jsCode"]
            )
    workflows.append(item)

executions = [
    dict(row)
    for row in db.execute(
        "SELECT id, workflowId, mode, status, startedAt, stoppedAt "
        "FROM execution_entity ORDER BY id DESC LIMIT 20"
    )
]
env_keys = docker("sh", "-c", "env | cut -d= -f1 | sort").splitlines()
assets = docker("sh", "-c", "ls -1 /home/node/.n8n/assets").splitlines()
print(
    json.dumps(
        {
            "workflows": workflows,
            "executions": executions,
            "environment_names": env_keys,
            "assets": assets,
        },
        ensure_ascii=False,
    )
)
