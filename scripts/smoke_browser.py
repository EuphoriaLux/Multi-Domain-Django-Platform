"""Playwright headless smoke for the post-event/coach-tooling roadmap.

Logs in as coach.marie via session injection and captures full-page
screenshots of every new page so the human reviewer can do a visual pass.

Run: .venv-1/Scripts/python.exe scripts/smoke_browser.py
Screenshots land in /tmp/crush_screenshots/.
"""
import os
import sys
from pathlib import Path

# Bootstrap Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "azureproject.settings")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from importlib import import_module  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://localhost:8765"
OUT = Path("/tmp/crush_screenshots")
OUT.mkdir(parents=True, exist_ok=True)


def make_session_for(user):
    """Mint a session cookie for the given user."""
    SessionStore = import_module(settings.SESSION_ENGINE).SessionStore
    s = SessionStore()
    s[SESSION_KEY] = user.pk
    s[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    s[HASH_SESSION_KEY] = user.get_session_auth_hash()
    s.save()
    return s.session_key


def main():
    user = User.objects.get(username="coach.marie")
    session_key = make_session_for(user)

    pages = [
        ("01_my_events", "/en/my-events/"),
        ("02_coach_dashboard_with_sla", "/en/coach/dashboard/"),
        ("03_coach_action_queue", "/en/coach/queue/"),
        ("04_notifications_full_page", "/en/notifications/"),
        ("05_dashboard_with_bell", "/en/dashboard/"),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        ctx.add_cookies([
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": session_key,
                "url": BASE,
                "httpOnly": True,
            },
            {
                "name": "crushlu_consent_given",
                "value": "true",
                "url": BASE,
            },
        ])
        page = ctx.new_page()

        results = []
        for label, path in pages:
            url = BASE + path
            try:
                resp = page.goto(url, wait_until="networkidle", timeout=30_000)
                status = resp.status if resp else 0
                # Open the bell on the dashboard so we capture the dropdown too
                if label == "05_dashboard_with_bell":
                    bell = page.locator('button[aria-label="Notifications"]').first
                    if bell.count():
                        bell.click()
                        page.wait_for_timeout(400)
                shot = OUT / f"{label}.png"
                page.screenshot(path=str(shot), full_page=True)
                results.append((label, path, status, shot.stat().st_size))
            except Exception as e:
                results.append((label, path, 0, f"ERR: {e}"))

        browser.close()

    print()
    print("LABEL                                PATH                              STATUS  FILE")
    print("-" * 100)
    for label, path, status, info in results:
        print(f"{label:36s} {path:32s} {str(status):6s}  {info}")


if __name__ == "__main__":
    main()
