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

# URLs the provisioning scripts must NOT set. Only for a timer that ships
# dormant (``dormant_if_unset=True``) before its Django route reaches
# production: a provisioning re-run in that gap would set the URL early and
# the timer would 404. Such a URL is set by hand after the swap, so it is still
# required in the docstring and local.settings.json.example, and the tests
# below prove each entry really is dormant-capable and really is absent from
# both scripts. Every other timer keeps the full check.
DEFERRED_URLS = {"DJANGO_SUMUP_RECONCILIATION_URL"}


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


def _dormant_env_vars() -> set[str]:
    """`url_env_var`s whose `_call_admin_endpoint` call passes dormant_if_unset=True."""
    tree = ast.parse(FUNCTION_APP_PY.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "_call_admin_endpoint" or len(node.args) < 2:
            continue
        dormant = any(
            kw.arg == "dormant_if_unset"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        if dormant and isinstance(node.args[1], ast.Constant):
            found.add(node.args[1].value)
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
    expected = declared if path == LOCAL_SETTINGS_EXAMPLE else declared - DEFERRED_URLS
    missing = expected - _env_vars_in(path)
    assert not missing, (
        f"{path.name} does not set these timer env vars: {sorted(missing)}. "
        "Provisioning from it would leave those timers reporting Success "
        "while doing nothing."
    )


def test_deferred_urls_are_dormant_timers_kept_out_of_provisioning(declared):
    """The allow-list above cannot become a way to skip the check.

    Each deferred URL must belong to a real timer that ships dormant, and must
    not appear in either provisioning script at all — a script that sets it
    re-opens the 404 gap the deferral exists to close.
    """
    assert DEFERRED_URLS <= declared, sorted(DEFERRED_URLS - declared)
    not_dormant = DEFERRED_URLS - _dormant_env_vars()
    assert not not_dormant, (
        f"{sorted(not_dormant)} are deferred but their timer does not pass "
        "dormant_if_unset=True — an unset URL would fail it every run."
    )
    for path in (PROVISION_PS1, PROVISION_SH):
        present = DEFERRED_URLS & _env_vars_in(path)
        assert not present, (
            f"{path.name} sets {sorted(present)}, which must be set by hand only "
            "after the slot swap that ships its route."
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


# ---------------------------------------------------------------------------
# Inventory guard: EVERY env var each Function App reads, not just the
# DJANGO_*_URL ones above. ADMIN_API_KEY, the *_ENABLED kill switches and the
# finops/contact-sync apps were outside the checks above, so a variable added
# to a function_app.py could be missing from the example settings and
# provisioning with no test failing - the timer then just logs and returns.
# ---------------------------------------------------------------------------

FUNCTIONS_ROOT = FUNCTION_APP_DIR.parent
DEPLOY_FINOPS_SH = FUNCTIONS_ROOT.parent / "scripts" / "deploy-finops-function.sh"

# Known gap, deliberately recorded rather than hidden: the finops deploy script
# predates the retail-price timer and never sets these two, so re-running it
# leaves that timer dormant (RETAIL_PRICE_SYNC_ENABLED defaults to false). They
# are set by hand. Remove an entry here when the script learns to set it.
FINOPS_DEPLOY_SCRIPT_KNOWN_GAPS = {
    "DJANGO_RETAIL_PRICE_WEBHOOK_URL",
    "RETAIL_PRICE_SYNC_ENABLED",
}


def _env_vars_read(function_app_py: Path) -> set[str]:
    """Literal names passed to os.environ.get / os.getenv / os.environ[...]."""
    tree = ast.parse(function_app_py.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            is_getenv = f.attr == "getenv" and getattr(f.value, "id", "") == "os"
            is_environ_get = (
                f.attr == "get"
                and isinstance(f.value, ast.Attribute)
                and f.value.attr == "environ"
            )
            if (is_getenv or is_environ_get) and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            found.add(node.slice.value)
    return found


def _assigned_setting_names(path: Path) -> set[str]:
    """Names assigned as quoted ``"NAME=value"`` strings in a provisioning script.

    That is the shape of every entry in the SETTINGS array / ``$settings`` list
    and of inline ``--settings "NAME=value"`` arguments, so a name that merely
    appears in a comment, echo or query is not counted as set.
    """
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        names.update(re.findall(r"[\"']([A-Z][A-Z0-9_]*)=", line))
    return names


def _example_keys(app_dir: Path) -> set[str]:
    path = app_dir / "local.settings.json.example"
    return set(json.loads(path.read_text(encoding="utf-8"))["Values"])


@pytest.mark.parametrize(
    "app", ["hybrid-maintenance", "contact-sync", "finops-daily-sync"]
)
def test_every_env_var_a_function_reads_is_in_its_example_settings(app):
    app_dir = FUNCTIONS_ROOT / app
    read = _env_vars_read(app_dir / "function_app.py")
    # hybrid-maintenance reads its URLs through a variable, so only the
    # literal reads are visible here; the declared URLs are checked above.
    if app == "hybrid-maintenance":
        read |= _declared_env_vars()
    assert read, f"{app}: no environment reads found - has the AST pattern drifted?"
    missing = read - _example_keys(app_dir)
    assert not missing, (
        f"{app}/local.settings.json.example lacks {sorted(missing)}, which "
        "function_app.py reads. Document every setting a timer depends on."
    )


def test_hybrid_provisioning_sets_the_non_url_settings_too():
    """ADMIN_API_KEY and HYBRID_MAINTENANCE_ENABLED gate every timer."""
    non_url = {
        v
        for v in _env_vars_read(FUNCTION_APP_PY)
        if not re.fullmatch(r"DJANGO_.*_URL", v)
    }
    assert {"ADMIN_API_KEY", "HYBRID_MAINTENANCE_ENABLED"} <= non_url
    for path in (PROVISION_PS1, PROVISION_SH):
        missing = non_url - _assigned_setting_names(path)
        assert not missing, f"{path.name} never assigns {sorted(missing)}"


def test_finops_deploy_script_sets_every_setting_except_known_gaps():
    read = _env_vars_read(FUNCTIONS_ROOT / "finops-daily-sync" / "function_app.py")
    missing = read - _assigned_setting_names(DEPLOY_FINOPS_SH)
    assert missing == FINOPS_DEPLOY_SCRIPT_KNOWN_GAPS, (
        f"deploy-finops-function.sh misses {sorted(missing)}; recorded known "
        f"gaps are {sorted(FINOPS_DEPLOY_SCRIPT_KNOWN_GAPS)}. Update the script "
        "or the allow-list so the two agree."
    )
