"""Validate imports inside an ephemeral copy of the installed n8n image.

No production volume, credentials, network, ports, or database is attached.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

workflows = json.load(sys.stdin)
for index, workflow in enumerate(workflows):
    workflow["id"] = f"CrushV2Validate{index}"
    workflow["settings"].pop("errorWorkflow", None)
    for node in workflow["nodes"]:
        for credential in node.get("credentials", {}).values():
            credential["id"] = "validationOnlyNoSecrets"
with tempfile.TemporaryDirectory(prefix="crush-import-validation-") as directory:
    os.chmod(directory, 0o755)
    source = Path(directory) / "workflows.json"
    source.write_text(json.dumps(workflows))
    os.chmod(source, 0o644)
    image = subprocess.check_output(
        ["docker", "inspect", "n8n", "--format", "{{.Image}}"], text=True
    ).strip()
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "768m",
            "--cpus",
            "1",
            "-e",
            "N8N_DIAGNOSTICS_ENABLED=false",
            "-e",
            "N8N_VERSION_NOTIFICATIONS_ENABLED=false",
            "-v",
            f"{directory}:/imports:ro",
            image,
            "import:workflow",
            "--input=/imports/workflows.json",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    for line in (result.stdout + result.stderr).splitlines():
        if (
            "import" in line.lower()
            or "error" in line.lower()
            or "success" in line.lower()
        ):
            print(line)
    print("ISOLATED IMPORT EXIT", result.returncode)
    sys.exit(result.returncode)
