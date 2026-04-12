#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Why this exists:
#   The web sandbox image ships with Python 3.11 and Django 5.2 pre-installed,
#   but this project requires Python 3.12+ and Django 6.0.4 (see requirements.txt).
#   Without this hook, `python`, `pytest`, `django-admin`, etc. all resolve to
#   the wrong stack and the test suite cannot run.
#
#   This hook builds a .venv from the system /usr/bin/python3.12, installs the
#   project's pinned requirements, and prepends the venv to PATH for the rest
#   of the session via $CLAUDE_ENV_FILE.
#
# Local devcontainer / laptop runs are unaffected: the script no-ops unless
# CLAUDE_CODE_REMOTE=true.

set -euo pipefail

# Only run inside the Claude Code on the web sandbox.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_BIN="/usr/bin/python3.12"
UV_BIN="${UV_BIN:-/root/.local/bin/uv}"

cd "$PROJECT_DIR"

# Sanity check: Python 3.12 must be present in the sandbox image.
if [ ! -x "$PYTHON_BIN" ]; then
    echo "session-start hook: $PYTHON_BIN not found; cannot bootstrap Python 3.12 venv" >&2
    exit 1
fi

# Decide whether the existing .venv is reusable (idempotency).
needs_create=1
if [ -x "$VENV_DIR/bin/python" ]; then
    existing_version="$("$VENV_DIR/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")"
    if [ "$existing_version" = "3.12" ]; then
        needs_create=0
    else
        echo "session-start hook: existing .venv is Python '$existing_version', recreating with 3.12"
        rm -rf "$VENV_DIR"
    fi
fi

# Create the venv with uv if available, otherwise fall back to stdlib venv.
if [ "$needs_create" = "1" ]; then
    if [ -x "$UV_BIN" ]; then
        "$UV_BIN" venv --python "$PYTHON_BIN" "$VENV_DIR"
    else
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
fi

# Install / sync dependencies. uv pip install is much faster than plain pip.
# We also install the same test extras CI uses (see .github/workflows/test-and-validate.yml).
if [ -x "$UV_BIN" ]; then
    VIRTUAL_ENV="$VENV_DIR" "$UV_BIN" pip install -r "$PROJECT_DIR/requirements.txt"
    VIRTUAL_ENV="$VENV_DIR" "$UV_BIN" pip install pytest-cov pytest-xdist
else
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
    "$VENV_DIR/bin/pip" install pytest-cov pytest-xdist
fi

# Persist environment for the rest of the session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    {
        echo "export VIRTUAL_ENV=\"$VENV_DIR\""
        echo "export PATH=\"$VENV_DIR/bin:\$PATH\""
        echo 'export DJANGO_SETTINGS_MODULE="azureproject.settings"'
        echo 'export SECRET_KEY="ci-test-key"'
        echo 'export USE_AZURE_STORAGE="False"'
    } >> "$CLAUDE_ENV_FILE"
fi

echo "session-start hook: ready ($("$VENV_DIR/bin/python" --version), Django $("$VENV_DIR/bin/python" -c 'import django; print(django.get_version())'))"
