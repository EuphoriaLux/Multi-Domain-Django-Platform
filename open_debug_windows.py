"""
Open one *already-logged-in* Chromium window per debug_* account for manual QA.

Why a script (and not just tabs): every tab in one Chrome profile shares a single
session cookie, so you can only be one user at a time. This script gives each
account its own **isolated browser context** (separate cookie jar), so all the
debug accounts stay logged in *simultaneously* in their own windows.

How it logs in: it mints a Django session per account server-side and injects the
`sessionid` cookie straight into each browser context. This bypasses the login
form entirely — no cookie-consent banner, no allauth rate limit, no filling
credentials. (Playwright can set HttpOnly cookies; `document.cookie` cannot.)

Each window opens in a **mobile viewport** (iPhone-sized) by default, gets a pink
badge (top-left) showing which account it is, and lands on that account's most
useful page. Windows stay open until you press Enter here. Pass --desktop for
wide windows.

Prereqs:
  - Dev server running:      python manage.py runserver   (http://localhost:8000)
  - Accounts seeded:         python manage.py seed_debug_profiles --reset
  - Playwright Chromium:     python -m playwright install chromium   (once)
  - Run with the project venv python (it needs Django + the .env DB settings).

Usage (run in your OWN terminal so you can press Enter to close):
    python open_debug_windows.py                 # all 10 accounts
    python open_debug_windows.py receiver cand_1 # only these (by suffix)
    python open_debug_windows.py --stages        # only the 5 signup-stage accounts
    python open_debug_windows.py --connect        # only the Connect cast
    python open_debug_windows.py --desktop        # wide windows instead of mobile
"""

import math
import os
import sys

# --- Django bootstrap (so we can mint sessions) -------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "azureproject.settings")

import django  # noqa: E402

django.setup()

from importlib import import_module  # noqa: E402

from django.conf import settings  # noqa: E402
from django.contrib.auth import (  # noqa: E402
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
)
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://localhost:8000"

# Mobile emulation (iPhone-ish) so each window previews the mobile layout. The
# real rendering viewport is set, so Tailwind `sm:`/`md:` breakpoints render
# their MOBILE side (unlike the claude-in-chrome extension, which can't). Pass
# --desktop for a normal wide window instead.
MOBILE_CONTEXT = dict(
    viewport={"width": 390, "height": 844},
    device_scale_factor=3,
    is_mobile=True,
    has_touch=True,
    user_agent=(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    ),
)
DESKTOP_CONTEXT = dict(viewport={"width": 1280, "height": 900})

# (suffix, badge label, landing path)
ACCOUNTS = [
    ("new", "signup: Welcome", "/en/onboarding/"),
    ("phone", "signup: Verify number", "/en/onboarding/"),
    ("draft", "signup: Build profile", "/en/onboarding/"),
    ("pending", "signup: Pending review", "/en/onboarding/"),
    ("approved", "member: Dashboard", "/en/dashboard/"),
    ("receiver", "Connect: receiver (drop+match+spark)", "/en/crush-connect/home/"),
    ("cand_1", "Connect: candidate (matched)", "/en/dashboard/"),
    ("cand_2", "Connect: candidate", "/en/dashboard/"),
    ("cand_3", "Connect: candidate", "/en/dashboard/"),
    ("sender", "Connect: sender (sent spark)", "/en/crush-connect/home/"),
]

_STAGE_SUFFIXES = {"new", "phone", "draft", "pending", "approved"}
_CONNECT_SUFFIXES = {"receiver", "cand_1", "cand_2", "cand_3", "sender"}


def _select(argv):
    args = set(argv)
    if "--stages" in args:
        return [a for a in ACCOUNTS if a[0] in _STAGE_SUFFIXES]
    if "--connect" in args:
        return [a for a in ACCOUNTS if a[0] in _CONNECT_SUFFIXES]
    wanted = {a for a in args if not a.startswith("--")}
    return [a for a in ACCOUNTS if not wanted or a[0] in wanted]


def _mint_session(user):
    """Create a logged-in Django session for `user` and return its key."""
    engine = import_module(settings.SESSION_ENGINE)
    session = engine.SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    return session.session_key


def _badge_script(email, label):
    """Inject a persistent identifying badge on every page in this context."""
    text = f"{email}  ·  {label}"
    return (
        "window.addEventListener('DOMContentLoaded', () => {"
        "  const b = document.createElement('div');"
        f"  b.textContent = {text!r};"
        "  b.style.cssText = 'position:fixed;top:0;left:0;z-index:2147483647;"
        "background:#e6006e;color:#fff;font:600 12px/1.7 system-ui,sans-serif;"
        "padding:2px 12px;border-bottom-right-radius:8px;pointer-events:none;"
        "box-shadow:0 1px 6px rgba(0,0,0,.3);opacity:.94';"
        "  document.documentElement.appendChild(b);"
        "});"
    )


def _dismiss_consent(page):
    """Best-effort: clear the cookie-consent banner so the window looks clean."""
    try:
        page.click("#cookie-btn-decline", timeout=1500)
    except Exception:
        pass


def _screen_size():
    """Screen work-area in logical pixels (Windows), with a sane fallback."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return 1920, 1080


def _grid(n, win_w, win_h):
    """Tile n windows edge-to-edge left→right; cascade extra rows to fit height."""
    screen_w, screen_h = _screen_size()
    cols = max(1, screen_w // win_w)
    rows = max(1, math.ceil(n / cols))
    if rows > 1 and rows * win_h > screen_h:
        y_step = max(70, (screen_h - win_h) // (rows - 1))
    else:
        y_step = win_h
    return [((i % cols) * win_w, (i // cols) * y_step, win_w, win_h) for i in range(n)]


def _position_window(page, x, y, w, h):
    """Move/size this context's OS window via CDP (Chromium only)."""
    try:
        cdp = page.context.new_cdp_session(page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": x,
                    "top": y,
                    "width": w,
                    "height": h,
                    "windowState": "normal",
                },
            },
        )
    except Exception:
        pass


def main():
    selected = _select(sys.argv[1:])
    if not selected:
        print("No matching accounts. Suffixes:", ", ".join(a[0] for a in ACCOUNTS))
        return

    User = get_user_model()
    prepared = []
    for suffix, label, landing in selected:
        email = f"debug_{suffix}@crush.lu"
        try:
            user = User.objects.get(username=f"debug_{suffix}")
        except User.DoesNotExist:
            print(
                f"  MISSING {email} — run: python manage.py seed_debug_profiles --reset"
            )
            continue
        prepared.append((email, label, landing, _mint_session(user)))

    if not prepared:
        print(
            "No debug accounts found. Seed them: python manage.py seed_debug_profiles --reset"
        )
        return

    mobile = "--desktop" not in sys.argv[1:]
    ctx_opts = MOBILE_CONTEXT if mobile else DESKTOP_CONTEXT
    # Chromium enforces a ~515px minimum window width, so tile ≥520 wide even
    # though the emulated mobile viewport itself is only 390px.
    win_w, win_h = (520, 860) if mobile else (960, 900)
    positions = _grid(len(prepared), win_w, win_h)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        view = "mobile" if mobile else "desktop"
        print(f"Opening {len(prepared)} logged-in windows ({view} view, tiled)...\n")
        for (email, label, landing, key), (x, y, w, h) in zip(prepared, positions):
            context = browser.new_context(
                **ctx_opts,
                service_workers="block",  # stop the PWA SW hijacking navigations
            )
            context.add_cookies(
                [
                    {
                        "name": settings.SESSION_COOKIE_NAME,
                        "value": key,
                        "domain": "localhost",
                        "path": "/",
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                ]
            )
            page = context.new_page()
            _position_window(page, x, y, w, h)
            page.add_init_script(_badge_script(email, label))
            try:
                page.goto(f"{BASE}{landing}", wait_until="commit")
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass  # tolerate redirect chains / SW aborts
            _dismiss_consent(page)
            print(f"  OK   {email:<26} @({x},{y}) -> {page.url}")

        print(
            f"\n{len(prepared)} windows open (each a different logged-in account).\n"
            "Arrange them as you like. Press Enter here to close them all..."
        )
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        browser.close()


if __name__ == "__main__":
    main()
