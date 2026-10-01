import json
import re
import sqlite3
from pathlib import Path

source = Path("/home/svc/scripts/crush_card_engine.py").read_text()
source = re.sub(r"AQ\.[A-Za-z0-9_-]+", "[REDACTED]", source)
source = re.sub(r"AIza[A-Za-z0-9_-]+", "[REDACTED]", source)
print(source)
db = sqlite3.connect(
    "file:/home/svc/.local/share/docker/volumes/n8n_n8n_data/_data/database.sqlite?mode=ro",
    uri=True,
)
raw = db.execute("SELECT data FROM execution_data WHERE executionId=30").fetchone()[0]
table = json.loads(raw)


def resolve(value, depth=0):
    if depth > 12:
        return "[depth]"
    if isinstance(value, str) and value.isdigit() and int(value) < len(table):
        return resolve(table[int(value)], depth + 1)
    if isinstance(value, dict):
        return {k: resolve(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, depth + 1) for v in value]
    return value


root = resolve(table[0])
result = root.get("resultData", {})
error = result.get("error", {})
print(
    "FAILURE SUMMARY",
    json.dumps(
        {k: error.get(k) for k in ["name", "message", "description"]},
        ensure_ascii=False,
    ),
)
print("LAST NODE", result.get("lastNodeExecuted"))
