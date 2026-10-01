"""Resolve n8n IDs without secrets. Outputs API payloads with only writable fields."""

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument(
    "--mapping",
    required=True,
    help="JSON mapping of credential NAME -> ID, plus error_workflow_id",
)
parser.add_argument("--output", default="bound")
args = parser.parse_args()
root = Path(__file__).parent
mapping = json.loads(Path(args.mapping).read_text())
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
for kind in ("editorial", "carousel", "regenerate", "errors", "ideas"):
    workflow = json.loads((root / f"{kind}.workflow.json").read_text(encoding="utf-8"))
    for node in workflow["nodes"]:
        for credential in node.get("credentials", {}).values():
            credential["id"] = mapping[credential["name"]]
    if "errorWorkflow" in workflow["settings"]:
        workflow["settings"]["errorWorkflow"] = mapping["error_workflow_id"]
    workflow.pop("active")  # POST /api/v1/workflows does not accept this field.
    (output / f"{kind}.workflow.json").write_text(
        json.dumps(workflow, indent=2), encoding="utf-8"
    )
print(
    "Resolved all credential references. POST creates inactive workflows; activation is a separate operation."
)
