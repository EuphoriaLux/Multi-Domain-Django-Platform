"""Playwright: axe-core accessibility smoke gate for ~10 key crush.lu pages.

Issue #1117 guardrail. The page-level axe tests elsewhere in this package
skip silently when axe-core is missing; this one never does (axe-core is a
devDependency, ``npm ci`` installs it) and it guards the whole journey:

    home, events, event detail, login, signup, dashboard, connections (the
    ``/matches/`` URL only redirects to the dashboard), Connect hub, account
    drill-down (GDPR), advent calendar -- each in light and dark mode at 390px.

It fails only on NEW critical or serious violations. What is tolerated today
lives in ``axe_baseline.json`` (``page/theme`` -> ``rule id`` -> node count).
A page fails when a rule is not in its baseline entry, or has more nodes than
the entry allows. Fewer nodes than the baseline is a pass with a ratchet hint.

Ratchet the baseline down (or record a deliberate exception):

    AXE_UPDATE_BASELINE=1 pytest -m "playwright and axe_smoke" -n 0 --create-db

That rewrites ``axe_baseline.json`` from the current results; review the diff
-- it should only ever shrink (a growing baseline needs a reason in the PR).
Fixing violations is tracked separately; do not raise counts to make CI green.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m "playwright and axe_smoke" -n 0 --create-db
"""

import json
import os
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

pytestmark = [
    pytest.mark.playwright,
    pytest.mark.axe_smoke,
    pytest.mark.django_db(transaction=True),
]

PHONE = {"width": 390, "height": 844}
REPO_ROOT = Path(__file__).resolve().parents[2]
AXE_PATH = REPO_ROOT / "node_modules" / "axe-core" / "axe.min.js"
BASELINE_PATH = Path(__file__).with_name("axe_baseline.json")
GATED_IMPACTS = ("critical", "serious")
UPDATE = os.environ.get("AXE_UPDATE_BASELINE") == "1"
ADVENT_NOW = "crush_lu.models.advent.timezone.now"
DEC_5 = datetime(2024, 12, 5, 12, 0, tzinfo=dt_timezone.utc)

# slug -> (who is logged in, path; "{event}" is the seeded event's id)
PAGES = {
    "home": ("anon", "/en/"),
    "events": ("anon", "/en/events/"),
    "event-detail": ("anon", "/en/events/{event}/"),
    "login": ("anon", "/en/login/"),
    "signup": ("anon", "/en/signup/"),
    "dashboard": ("member", "/en/dashboard/"),
    "connections": ("member", "/en/connections/"),
    "connect-hub": ("member", "/en/crush-connect/week/"),
    "account-drilldown": ("member", "/en/account/gdpr/"),
    "advent": ("advent", "/en/advent/"),
}
CASES = [(slug, theme) for slug in PAGES for theme in ("light", "dark")]

AXE_JS = """
async () => {
    const r = await axe.run(document, {resultTypes: ['violations']});
    return r.violations.map(v => ({
        id: v.id,
        impact: v.impact,
        nodes: v.nodes.length,
        targets: v.nodes.slice(0, 3).map(n => n.target.join(' ')),
    }));
}
"""


def _load_baseline():
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _save_baseline(data):
    ordered = {key: dict(sorted(data[key].items())) for key in sorted(data)}
    BASELINE_PATH.write_text(
        json.dumps(ordered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@pytest.fixture
def world(transactional_db, settings):
    """A member, an upcoming event and an advent user, plus their sessions."""
    from datetime import timedelta

    from django.utils import timezone

    from crush_lu.models import MeetupEvent
    from crush_lu.tests.test_crush_connect import _grant_consent, _make_user
    from crush_lu.tests.test_ux_wave4_advent import make_advent_user

    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="axe_smoke")
    _grant_consent(member)
    event = MeetupEvent.objects.create(
        title="Axe Smoke Mixer",
        description="An evening of wine and conversation.",
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=10),
        location="Luxembourg City",
        address="1 Rue de la Gare, Luxembourg",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(days=8),
        registration_fee=0,
        is_published=True,
        profile_requirement="none",
    )

    class _Holder:
        client = Client()

    advent = _Holder()
    make_advent_user(advent)
    return {"member": member, "event": event, "advent": advent.user}


def _session_cookie(user):
    client = Client()
    client.force_login(user)
    return client.cookies[settings.SESSION_COOKIE_NAME].value


@pytest.mark.skipif(
    not AXE_PATH.is_file(), reason="axe-core missing: run `npm ci` (devDependency)"
)
@pytest.mark.parametrize("slug,theme", CASES, ids=[f"{s}-{t}" for s, t in CASES])
def test_no_new_serious_axe_violations(browser, live_server, world, slug, theme):
    who, path = PAGES[slug]
    context = browser.new_context(viewport=PHONE, color_scheme=theme)
    # Never reach out to Google Fonts from CI.
    context.route(
        "**://fonts.g*.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    cookies = [
        {"name": name, "value": "decline", "url": live_server.url}
        for name in ("cookie_consent_analytics", "cookie_consent_marketing")
    ]
    if who != "anon":
        cookies.append(
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": _session_cookie(world[who]),
                "url": live_server.url,
            }
        )
    context.add_cookies(cookies)
    # Persist the theme like the drawer does, so dark-only CSS applies.
    context.add_init_script(f"localStorage.setItem('theme', '{theme}');")
    page = context.new_page()
    try:
        with patch(ADVENT_NOW, return_value=DEC_5):
            response = page.goto(
                f"{live_server.url}{path.format(event=world['event'].id)}"
            )
            assert response is not None and response.ok, (path, response)
            page.wait_for_load_state("load")
            page.wait_for_timeout(500)
            page.evaluate(AXE_PATH.read_text(encoding="utf-8"))
            violations = page.evaluate(AXE_JS)
    finally:
        context.close()

    found = {v["id"]: v for v in violations if v["impact"] in GATED_IMPACTS}
    key = f"{slug}/{theme}"

    if UPDATE:
        data = _load_baseline()
        data[key] = {rule: v["nodes"] for rule, v in found.items()}
        if not data[key]:
            data.pop(key)
        _save_baseline(data)
        return

    allowed = _load_baseline().get(key, {})
    regressions = [
        f"{rule}: {v['nodes']} node(s), baseline {allowed.get(rule, 0)} "
        f"({v['impact']}) e.g. {v['targets']}"
        for rule, v in sorted(found.items())
        if v["nodes"] > allowed.get(rule, 0)
    ]
    assert not regressions, (
        f"New critical/serious axe violations on {key} ({path}):\n  "
        + "\n  ".join(regressions)
        + "\nFix them; do not raise axe_baseline.json."
    )
    improved = [
        f"{rule}: {allowed[rule]} -> {found.get(rule, {}).get('nodes', 0)}"
        for rule in sorted(allowed)
        if found.get(rule, {}).get("nodes", 0) < allowed[rule]
    ]
    if improved:
        print(
            f"axe ratchet: {key} improved ({', '.join(improved)}); "
            "re-run with AXE_UPDATE_BASELINE=1 to lock it in."
        )
