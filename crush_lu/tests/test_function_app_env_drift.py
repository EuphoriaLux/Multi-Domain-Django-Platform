"""Guard the hybrid-maintenance Function App against configuration drift.

Every scheduled job on `crush-hybrid-maintenance` depends on a chain of four
things staying in agreement:

    timer in function_app.py
        -> DJANGO_*_URL env var
            -> provisioning scripts / local.settings.json.example
                -> a real route in azureproject/urls_crush.py

Nothing enforced that agreement, and on 2026-07-30 four of eight URLs were
found unset in production. The root cause was documentation drift: the module
docstring's "Environment Variables Required" list named only four of the
eight, so the other four were never noticed as missing — and re-provisioning
from the scripts would have reproduced the gap, because they were incomplete
too.

The failure is invisible at runtime in both directions: a missing env var makes
`_call_admin_endpoint` return while the timer still reports *Success*, and a
missing Django route only 404s in production. So this has to be caught at
review time. These tests live in `crush_lu/tests` (a `pytest.ini` testpath)
specifically so they run on every PR.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from django.urls import Resolver404, resolve

# These are pure file/AST comparisons and touch no models, but conftest.py's
# session-scoped Site seeding is autouse, so the DB has to be available.
pytestmark = pytest.mark.django_db

FUNCTION_APP_DIR = (
    Path(__file__).resolve().parents[2] / "azure-functions" / "hybrid-maintenance"
)
FUNCTION_APP_PY = FUNCTION_APP_DIR / "function_app.py"
PROVISION_PS1 = FUNCTION_APP_DIR / "provision.ps1"
PROVISION_SH = FUNCTION_APP_DIR / "provision.sh"
LOCAL_SETTINGS_EXAMPLE = FUNCTION_APP_DIR / "local.settings.json.example"


def _declared_env_vars() -> set[str]:
    """Every `url_env_var` passed to `_call_admin_endpoint`, via the AST.

    Parsed rather than grepped so a renamed variable, a new timer, or a call
    spread over several lines is all picked up identically.
    """
    tree = ast.parse(FUNCTION_APP_PY.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "_call_admin_endpoint":
            continue
        # Signature: _call_admin_endpoint(name, url_env_var, timeout=...)
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            found.add(node.args[1].value)
        for kw in node.keywords:
            if kw.arg == "url_env_var" and isinstance(kw.value, ast.Constant):
                found.add(kw.value.value)
    return found


def _env_vars_in(path: Path) -> set[str]:
    return set(
        re.findall(r"\bDJANGO_[A-Z0-9_]*_URL\b", path.read_text(encoding="utf-8"))
    )


def _docstring_env_vars() -> set[str]:
    tree = ast.parse(FUNCTION_APP_PY.read_text(encoding="utf-8"))
    return set(re.findall(r"\bDJANGO_[A-Z0-9_]*_URL\b", ast.get_docstring(tree) or ""))


@pytest.fixture(scope="module")
def declared() -> set[str]:
    found = _declared_env_vars()
    assert found, "No _call_admin_endpoint calls found — has the helper been renamed?"
    return found


def test_every_timer_env_var_is_documented(declared):
    """The docstring list is what a human reads when provisioning by hand.

    This is the exact check that would have caught the 2026-07-30 outage: the
    list named 4 of 8, and the 4 it omitted were the 4 found unset in prod.
    """
    missing = declared - _docstring_env_vars()
    assert not missing, (
        "These timer env vars are missing from function_app.py's "
        f"'Environment Variables Required' docstring: {sorted(missing)}. "
        "Add a new timer's variable there in the same commit that adds the timer."
    )


@pytest.mark.parametrize(
    "path", [PROVISION_PS1, PROVISION_SH, LOCAL_SETTINGS_EXAMPLE], ids=lambda p: p.name
)
def test_provisioning_sources_cover_every_timer_env_var(declared, path):
    """Re-provisioning must not silently recreate the gap it is meant to fix."""
    missing = declared - _env_vars_in(path)
    assert not missing, (
        f"{path.name} does not set these timer env vars: {sorted(missing)}. "
        "Provisioning from it would leave those timers reporting Success "
        "while doing nothing."
    )


def test_provisioning_sources_declare_no_unknown_env_vars(declared):
    """The reverse drift: a URL left behind after its timer was deleted.

    Harmless at runtime, but it makes the real inventory unknowable, which is
    how the original list went stale.
    """
    for path in (PROVISION_PS1, PROVISION_SH, LOCAL_SETTINGS_EXAMPLE):
        stale = _env_vars_in(path) - declared
        assert not stale, (
            f"{path.name} sets {sorted(stale)}, which no timer uses. "
            "Remove it, or add the timer it belongs to."
        )


def _assigned_paths(path: Path) -> dict[str, str]:
    """Map each DJANGO_*_URL to the URL *path* the source assigns it.

    Handles all three shapes: `"VAR=https://$DJANGO_HOST/api/..."` (ps1),
    `"VAR=https://crush.lu/api/..."` (sh) and `"VAR": "http://localhost:8000/api/..."`
    (json). The host differs per source and per slot by design, so only the
    path after it is compared.
    """
    text = path.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    pattern = r"(DJANGO_[A-Z0-9_]*_URL)\"?\s*[=:]\s*\"?https?://[^/\"]+(/[^\"',\s]*)"
    for var, url_path in re.findall(pattern, text):
        found[var] = url_path
    return found


def test_provisioning_sources_agree_on_each_url_path(declared):
    """Matching variable *names* is not enough.

    A script can assign a typo'd path, or another timer's perfectly valid
    endpoint, and every name-level check still passes — while re-running that
    script would deploy the wrong mapping. Compare the values too, against
    local.settings.json.example as the canonical map.
    """
    canonical = _assigned_paths(LOCAL_SETTINGS_EXAMPLE)
    missing_canonical = declared - canonical.keys()
    assert not missing_canonical, (
        f"local.settings.json.example has no parseable URL for {sorted(missing_canonical)} "
        "— it is the canonical map, so it must define every timer's path."
    )
    for source in (PROVISION_PS1, PROVISION_SH):
        for var, url_path in _assigned_paths(source).items():
            if var not in declared:
                continue
            assert url_path == canonical[var], (
                f"{source.name} points {var} at {url_path!r}, but "
                f"local.settings.json.example says {canonical[var]!r}. "
                "Provisioning from that script would deploy the wrong endpoint "
                "for this timer."
            )


def test_every_timer_url_resolves_to_a_real_django_route():
    """The far end of the chain.

    A URL can be set correctly on the Function App and still 404, because the
    route lives on `urls_crush` and only reaches production at a slot swap.
    Resolving the paths here catches a typo or a deleted view at review time
    instead of at 3am. Paths are taken from local.settings.json.example, the
    one source that stores whole URLs rather than shell-interpolated ones.
    """
    values = json.loads(LOCAL_SETTINGS_EXAMPLE.read_text(encoding="utf-8"))["Values"]
    checked = 0
    for key, url in values.items():
        if not re.fullmatch(r"DJANGO_[A-Z0-9_]*_URL", key):
            continue
        path = "/" + url.split("/", 3)[3] if "://" in url else url
        try:
            resolve(path, urlconf="azureproject.urls_crush")
        except Resolver404:  # pragma: no cover - only on real drift
            pytest.fail(
                f"{key} points at {path}, which does not resolve in "
                "azureproject.urls_crush. The timer would 404 in production."
            )
        checked += 1
    assert checked, "No DJANGO_*_URL entries found in local.settings.json.example"
