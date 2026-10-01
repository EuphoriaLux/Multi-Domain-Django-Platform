"""UX Wave 5 · WP14 "settings-and-journey".

8-08   "Unsubscribe from ALL emails" became a "Pause all emails" switch after
       the granular list; the WhatsApp preference saves instantly (Alpine).
R15    journey/gift pages follow the theme (see test_contrast_tokens.py);
       advent snowflakes drift behind the page content.
#1115  leftover "Connections" wording, Crush Connect listed once per surface,
       off-canon buttons, Answer buttons on btn-crush-solid (6-13).
#1064  platform-aware "notifications blocked" copy and an OS-settings link in
       the native shells.

Literal ``/en/...`` paths; testserver resolves to crush.lu.
"""

import html
import re
from pathlib import Path

import pytest
from django.core.cache import cache

from crush_lu.tests.test_crush_connect import _login_eligible, _make_user

pytestmark = pytest.mark.urls("azureproject.urls_crush")

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "crush_lu"
NOTIFICATIONS = "/en/profile/edit/?section=account&sub=notifications"
HUB_HREF = 'href="/en/crush-connect/home/"'


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _member(client, settings, username):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username=username)
    _login_eligible(client, me)
    return me


def _body(response):
    assert response.status_code == 200, response.status_code
    return html.unescape(response.content.decode())


def _source(*parts):
    return TEMPLATES.joinpath(*parts).read_text(encoding="utf-8")


# --- 8-08 Pause all emails ---------------------------------------------------


@pytest.mark.django_db
def test_pause_all_emails_switch_sits_after_the_granular_list(client, settings):
    _member(client, settings, "w5_pause")

    body = _body(client.get(NOTIFICATIONS))

    assert "Unsubscribe from ALL emails" not in body
    assert "Pause all emails" in body
    # Still the same switch and field: the Alpine handler and the API key.
    assert '@change="toggleUnsubscribe"' in body
    marketing = body.index('data-pref-key="email_marketing"')
    assert body.index("Pause all emails") > marketing
    assert body.index("toggleUnsubscribe") > marketing


@pytest.mark.django_db
def test_paused_state_message_points_at_the_new_switch(client, settings):
    me = _member(client, settings, "w5_paused")
    from crush_lu.models import EmailPreference

    prefs = EmailPreference.get_or_create_for_user(me)
    prefs.unsubscribed_all = True
    prefs.save()

    body = _body(client.get(NOTIFICATIONS))

    assert "All emails are paused" in body
    assert "Toggle the switch below" not in body


# --- 8-08 WhatsApp saves instantly ------------------------------------------


@pytest.mark.django_db
def test_whatsapp_switch_is_an_instant_save_component(client, settings):
    me = _member(client, settings, "w5_wa")
    from crush_lu.models import CrushProfile

    CrushProfile.objects.filter(user=me).update(
        phone_number="+352621123456", phone_verified=True
    )

    body = _body(client.get(NOTIFICATIONS))

    form = body[body.index('x-data="whatsappPreference"') :]
    form = form[: form.index("</form>")]
    assert 'data-saved-message="WhatsApp notification preference updated."' in form
    assert '@change="save"' in form
    # The save button only survives as the no-JS fallback.
    assert re.search(r"<noscript>.*Save WhatsApp preferences.*</noscript>", form, re.S)
    core = (ROOT / "static/crush_lu/js/alpine/core.js").read_text(encoding="utf-8")
    assert 'Alpine.data("whatsappPreference"' in core
    assert "Object.assign" not in core[core.index('"whatsappPreference"') :][:1500]


# --- #1064 blocked-notification copy ---------------------------------------


@pytest.mark.django_db
def test_blocked_copy_has_one_paragraph_per_platform(client, settings):
    _member(client, settings, "w5_blocked")

    body = _body(client.get(NOTIFICATIONS))

    for getter in ("blockedOnDesktop", "blockedOnAndroid", "blockedOnIos"):
        assert f'x-show="{getter}"' in body
    assert "open Settings, then Notifications, choose Crush.lu" in body
    core = (ROOT / "static/crush_lu/js/alpine/core.js").read_text(encoding="utf-8")
    assert "get blockedOnIos()" in core and 'blockedPlatform = "ios"' in core


IOS_UA = "CrushLUApp/1.0 Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
ANDROID_UA = "CrushLUAndroid/1.0 Mozilla/5.0 (Linux; Android 14; Pixel 8)"


@pytest.mark.django_db
def test_native_shells_get_an_os_settings_link(client, settings):
    _member(client, settings, "w5_native")

    web = _body(client.get(NOTIFICATIONS))
    ios = _body(client.get(NOTIFICATIONS, HTTP_USER_AGENT=IOS_UA))
    android = _body(client.get(NOTIFICATIONS, HTTP_USER_AGENT=ANDROID_UA))

    assert "data-os-settings-link" not in web
    assert 'href="app-settings:"' in ios
    assert "Open notification settings" in ios
    assert (
        "intent:#Intent;action=android.settings.APP_NOTIFICATION_SETTINGS;"
        "S.android.provider.extra.APP_PACKAGE=lu.crush.app;end"
    ) in android
    assert "app-settings:" not in android


# --- #1115 naming / nav ------------------------------------------------------


@pytest.mark.django_db
def test_crush_connect_is_listed_once_per_surface(client, settings):
    _member(client, settings, "w5_nav")

    body = _body(client.get("/en/dashboard/"))

    # The desktop bar link stays; the dropdown and drawer copies are gone.
    assert 'data-nav="crush-connect"' in body
    assert f'class="dropdown-item" {HUB_HREF}' not in body
    assert f'class="nav-link block" {HUB_HREF}' not in body
    # The profile editor entry is a different destination and stays.
    assert "Edit your Connect profile" in body


@pytest.mark.django_db
def test_matches_wording_replaces_connections(client, settings):
    _member(client, settings, "w5_matches")

    matches = _body(client.get("/en/connections/"))

    assert "Track your match requests and active matches" in matches
    assert "Track your connection requests" not in matches
    dashboard = _source("dashboard.html")
    assert '{% trans "Connections" %}</p>' not in dashboard
    assert '{% translate "Matches" context "member connections nav" %}</p>' in dashboard
    assert '{% trans "Active Connections" %}' not in _source("my_connections.html")
    assert "My Connections" not in _source("includes", "event_card.html")
    assert "My Matches" in _source("includes", "event_card.html")


def test_matches_wording_is_translated():
    from django.utils import translation

    expected = {
        "de": ("Aktive Matches", "Verfolge deine Match-Anfragen und aktiven Matches"),
        "fr": (
            "Affinités actives",
            "Suivez vos demandes d'affinité et vos affinités actives",
        ),
    }
    for lang, (heading, subtitle) in expected.items():
        with translation.override(lang):
            assert translation.gettext("Active Matches") == heading
            assert (
                translation.gettext("Track your match requests and active matches")
                == subtitle
            )


# --- off-canon buttons / Answer ---------------------------------------------


def test_off_canon_buttons_use_the_style_variants():
    home = _source("home.html")
    assert re.search(
        r'crush_lu:how_it_works\' %\}" class="btn-crush-outline">\s*'
        r'\{% trans "Learn More About Our Process"',
        home,
    )
    how = _source("how_it_works.html")
    assert 'class="btn-crush-outline btn-sm"' in how
    assert "btn-crush-primary btn-lg w-full" in how
    assert "py-4 px-12" not in how and "border-purple-500" not in how


@pytest.mark.django_db
def test_how_it_works_has_a_single_primary_cta(client):
    body = _body(client.get("/en/how-it-works/"))

    assert body.count("btn-crush-primary") == 1


def test_connect_answer_button_is_solid_not_gradient():
    card = _source("crush_connect", "_cycle_card.html")
    button = card[card.index("data-gate-submit") :]
    button = button[: button.index("</button>")]
    assert "btn-crush-solid" in button
    assert "btn-crush-primary" not in button


# --- advent snow -------------------------------------------------------------


def test_snowflakes_sit_behind_the_page_content():
    base = _source("advent", "advent_base.html")
    snow = base[base.index(".snowflakes {") :]
    snow = snow[: snow.index("}")]
    assert "pointer-events: none" in snow
    assert "z-index: 0" in snow
    lift = base[base.index(".advent-container > :where(:not(.snowflakes))") :]
    assert "z-index: 1" in lift[: lift.index("}")]


# --- Codex review follow-ups ---------------------------------------------------

CSS_SRC = ROOT.parent / "tailwind-src" / "crush_lu" / "tailwind-input.css"
LIGHT = "html:not(.dark) body.journey-bg #main-content"


def _light_rules():
    css = CSS_SRC.read_text(encoding="utf-8")
    return re.findall(re.escape(LIGHT) + r"([^{]*)\{([^}]*)\}", css)


def test_light_video_reward_copy_is_dark_ink():
    colour_rules = [
        sel
        for sel, body in _light_rules()
        if re.search(r"(^|;)\s*color:\s*var\(--color-crush-dark\)", body)
    ]
    assert any(".video-intro" in sel for sel in colour_rules)
    surfaces = {sel.strip(): body for sel, body in _light_rules()}
    for cls in (".video-card", ".video-media-container", ".video-text-message"):
        assert cls in surfaces, cls
    # The 0.9-alpha white instruction must not survive on the lavender page.
    assert "rgb(255 255 255 / 0.7)" in surfaces[".video-card"]


def test_light_selector_card_hover_keeps_dark_text():
    hover = {sel.strip(): body for sel, body in _light_rules()}
    rule = hover.get(".journey-selector-card:hover")
    assert rule is not None
    assert "color: var(--color-crush-dark)" in rule


def test_android_settings_intent_is_handled_natively():
    java = (
        ROOT.parent
        / "mobile/android/CrushLU/app/src/main/java/lu/crush/app/MainActivity.java"
    ).read_text(encoding="utf-8")
    # openExternal() adds CATEGORY_BROWSABLE, which the system settings
    # activity lacks, so the shell must start this intent itself.
    handler = java[java.index("boolean openAppNotificationSettings(") :]
    handler = handler[: handler.index("private void openExternal")]
    assert "Settings.ACTION_APP_NOTIFICATION_SETTINGS" in handler
    assert "Settings.EXTRA_APP_PACKAGE, getPackageName()" in handler
    assert "CATEGORY_BROWSABLE" not in handler
    nav = java[java.index("private boolean handleNavigation") :]
    assert nav.index("openAppNotificationSettings(uri)") < nav.index(
        "openExternal(uri)"
    )


def test_whatsapp_saves_are_serialised_in_click_order():
    core = (ROOT / "static/crush_lu/js/alpine/core.js").read_text(encoding="utf-8")
    block = core[core.index('"whatsappPreference"') :]
    block = block[: block.index('"profileSectionAutosave"')]
    assert "this.queue = this.queue.then(" in block


# Round-2 review: the light conversion was incomplete. The exhaustive guard is
# test_journey_light_contrast_playwright.py (it measures every template); these
# source checks keep the eight reported surfaces pinned in the default run.


def _light(selector_fragment, declaration):
    return any(
        selector_fragment in sel and declaration in body for sel, body in _light_rules()
    )


def test_light_certificate_surfaces_and_nav_button():
    assert _light(
        ".certificate-card {", "background: rgb(255 255 255 / 0.8)"
    ) or _light(".certificate-card", "background: rgb(255 255 255 / 0.8)")
    assert _light(".certificate-card .certificate-title", "var(--color-crush-dark)")
    assert _light(".certificate-text", "color: var(--color-crush-dark)")
    assert _light(".nav-btn", "color: var(--color-crush-dark)")


def test_light_gift_alerts_and_landing_headings():
    for kind in ("success", "error", "warning", "info"):
        assert _light(f".gift-message-{kind}", "color: #")
    sel = next(
        sel
        for sel, body in _light_rules()
        if "background-image" in body and ".gift-landing .hero-title" in sel
    )
    assert ".gift-landing .hero-recipient" in sel


def test_light_slideshow_counter_difficulty_and_progress_label():
    assert _light(".slideshow-counter", "color: #6b4800")
    for level in ("easy", "hard"):
        assert _light(f".journey-difficulty-{level}", "color: #")
    assert _light(".journey-progress-label", "background: rgb(255 255 255")


def test_light_gradient_surfaces_keep_white_text():
    css = CSS_SRC.read_text(encoding="utf-8")
    reset = css[css.index(".journey-btn-primary,") :]
    reset = reset[: reset.index("--color-white: #fff")]
    for cls in (".reveal-piece-number", ".reveal-complete-message", ".timeline-number"):
        assert cls in reset, cls
    assert _light(".timeline-number", "color: #fff")


# Round-3 review: pale palette tokens, placeholders, coach blocked guidance.


def test_light_theme_overrides_the_pale_emerald_token():
    css = CSS_SRC.read_text(encoding="utf-8")
    block = css[css.index(LIGHT + " {") :]
    block = block[: block.index("}")]
    # .step-indicator.completed and .file-name use text-emerald-300.
    assert "--color-emerald-300: #047857" in block


def test_light_theme_placeholders_are_opaque_dark_ink():
    assert _light(":is(input, textarea)::placeholder", "opacity: 1")
    assert _light(":is(input, textarea)::placeholder", "color: #3b4856")


def test_coach_push_card_shares_the_platform_detection():
    core = (ROOT / "static/crush_lu/js/alpine/core.js").read_text(encoding="utf-8")
    assert "function makeBlockedPlatform()" in core
    for name in ("pushPreferences", "coachPushPreferences"):
        start = core.index(f'Alpine.data("{name}"')
        assert "makeBlockedPlatform()" in core[start : start + 200], name
    template = _source("partials", "edit_account_notifications.html")
    coach = template[template.index("Coach Push Notifications Card") :]
    denied = coach[coach.index("showPermissionDenied") :][:2500]
    for getter in ("blockedOnDesktop", "blockedOnAndroid", "blockedOnIos"):
        assert f'x-show="{getter}"' in denied
    assert "Allow notifications in your browser settings" not in denied
