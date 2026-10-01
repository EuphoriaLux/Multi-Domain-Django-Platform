"""UX Wave 5 · WP9 "a11y-structure" (review findings R5, R7, R8).

* R5: the tabbed auth page and every allauth account page carry an ``<h1>``
  (the account pages used to open on an h5 followed by an h6).
* R7: pages whose headline was an h2 (profile_submitted) and pages that skipped
  a heading level (how-it-works, about, events, notifications, create_profile)
  step down one level at a time.
* R8: the lobby / recap tile grids are not ``role="list"`` without listitem
  children, and the canton map's options carry accessible names.

Paths are literal: ``reverse("crush_lu:...")`` builds ``/crush/...`` paths that
404 under ``HTTP_HOST=crush.lu``.
"""

import re

import pytest
from django.utils import timezone
from django.core.cache import cache
from django.template.loader import render_to_string

from crush_lu.models import CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent
from crush_lu.tests.test_event_lobby import (
    _end_event,
    _join,
    _login,
    _make_event,
    _make_member,
    lobby_flags,  # noqa: F401 (autouse)
)

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

HOST = "crush.lu"


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


def _headings(html):
    """(level, text) of every h1-h6 on the page, in document order."""
    return [
        (int(level), re.sub(r"<[^>]+>|\s+", " ", text).strip())
        for level, text in re.findall(r"<h([1-6])\b[^>]*>(.*?)</h\1>", html, re.S)
    ]


def _assert_no_skipped_level(headings):
    assert headings, "no headings rendered"
    for (prev_level, prev_text), (level, text) in zip(headings, headings[1:]):
        assert level <= prev_level + 1, (
            f"heading outline skips a level: h{prev_level} {prev_text!r} -> "
            f"h{level} {text!r}"
        )


def _body(response):
    return response.content.decode()


class TestAuthPagesHaveAnH1:
    @pytest.mark.parametrize("path", ["/en/login/", "/en/signup/"])
    def test_tabbed_auth_page(self, client, path):
        html = _body(client.get(path, HTTP_HOST=HOST))
        levels = [level for level, _ in _headings(html)]
        assert 1 in levels
        # Both tab headings ship; Alpine shows the active one.
        assert "Login to your Crush.lu account" in html
        assert "Create your Crush.lu account" in html

    @pytest.mark.parametrize("lang,text", [("de", "Erstelle dein Crush.lu-Konto")])
    def test_signup_heading_is_translated(self, client, lang, text):
        html = _body(client.get(f"/{lang}/signup/", HTTP_HOST=HOST))
        assert text in html

    @pytest.mark.parametrize(
        "path",
        [
            "/accounts/password/reset/",
            "/accounts/password/reset/done/",
            "/accounts/password/reset/key/done/",
            "/accounts/password/reset/key/1-abc-def/",
        ],
    )
    def test_password_reset_pages(self, client, path):
        response = client.get(path, HTTP_HOST=HOST)
        assert response.status_code == 200
        headings = _headings(_body(response))
        page = [h for h in headings if h[1] not in {"Cookie Preferences"}]
        assert page[0][0] == 1, page
        levels = [level for level, _ in page]
        assert 5 not in levels and 6 not in levels, page
        _assert_no_skipped_level(page)


class TestHeadingOutlines:
    @pytest.mark.parametrize("path", ["/en/how-it-works/", "/en/about/"])
    def test_public_pages_do_not_skip_levels(self, client, path):
        html = _body(client.get(path, HTTP_HOST=HOST))
        start = html.index("<h1")
        end = html.index("<footer", start)
        _assert_no_skipped_level(_headings(html[start:end]))

    def test_events_list_cards_follow_the_h1(self, client):

        _make_event(starts_in_minutes=60 * 24 * 3)
        html = _body(client.get("/en/events/", HTTP_HOST=HOST))
        start = html.index("<h1")
        end = html.index("<footer", start)
        headings = _headings(html[start:end])
        assert (2, "Lobby Test Event") in headings
        _assert_no_skipped_level(headings)

    def test_notifications_empty_state_is_not_an_h4(self, client):
        user = _make_member("wp9_notify", membership=False)
        _login(client, user)
        html = _body(client.get("/en/notifications/", HTTP_HOST=HOST))
        assert not re.search(r"<h4\b[^>]*>\s*No notifications yet", html)

    def test_profile_submitted_headline_is_the_h1(self, client):
        user = _make_member("wp9_submitted", membership=False, luxid=False)
        CrushProfile.objects.filter(user=user).update(
            is_approved=False, verification_status="pending"
        )
        _login(client, user)
        html = _body(client.get("/en/profile-submitted/", HTTP_HOST=HOST))
        headings = _headings(html)
        page_h1 = [text for level, text in headings if level == 1]
        assert any(text.startswith("Your profile is ready") for text in page_h1)
        assert "data-status-line" in html
        assert not re.search(r"<h2[^>]*data-status-line", html)

    def test_paused_submission_still_has_an_h1(self, client):
        # The paused state skips the whole status block, so its banner is the
        # page title (it used to be an h2 and the page had no h1 at all).
        user = _make_member("wp9_paused", membership=False, luxid=False)
        profile = CrushProfile.objects.get(user=user)
        CrushProfile.objects.filter(user=user).update(
            is_approved=False, verification_status="pending"
        )
        ProfileSubmission.objects.create(
            profile=profile, status="pending", is_paused=True
        )
        _login(client, user)
        response = client.get("/en/profile-submitted/", HTTP_HOST=HOST)
        assert response.status_code == 200
        headings = _headings(_body(response))
        assert [text for level, text in headings if level == 1] == ["Profile paused"]
        _assert_no_skipped_level(headings)


class TestJourneyCreateProfileTitle:
    def test_h1_is_a_real_title_not_tiny_metadata(self, client):
        # In the journey flow the visible caption is a 10px label; the page
        # title must not be that tiny h1 (it is screen-reader-only text).
        user = _make_member("wp9_journey_title", membership=False, luxid=False)
        CrushProfile.objects.filter(user=user).update(
            is_approved=False,
            welcome_seen_at=timezone.now(),
            coach_intro_seen_at=timezone.now(),
            phone_verified=True,
            verification_status="incomplete",
        )
        _login(client, user)
        html = _body(client.get("/en/create-profile/", HTTP_HOST=HOST))
        assert not re.search(r"<h1[^>]*text-\[10px\]", html)
        assert re.search(r'<h1 class="sr-only">\s*Build your profile\s*</h1>', html)


class TestCantonMapKeyboardAnnouncements:
    def test_focused_option_becomes_the_active_descendant(self):
        # DOM focus stays on the listbox <svg>, so arrow-key navigation must
        # move aria-activedescendant or the new option labels are never read.
        from pathlib import Path

        from django.conf import settings

        js = (
            Path(settings.BASE_DIR)
            / "crush_lu"
            / "static"
            / "crush_lu"
            / "js"
            / "alpine"
            / "core.js"
        ).read_text(encoding="utf-8")
        start = js.index("_applyFocus: function")
        end = js.index("selectRegion: function", start)
        block = js[start:end]
        assert (
            'setAttribute(\n                            "aria-activedescendant"'
            in block
        )
        assert 'removeAttribute("aria-activedescendant")' in block

    def test_refocus_reapplies_the_active_descendant(self):
        # blur clears aria-activedescendant but keeps focusedIndex, so the
        # focus handler must re-apply it even when the index is already set.
        from pathlib import Path

        from django.conf import settings

        js = (
            Path(settings.BASE_DIR)
            / "crush_lu"
            / "static"
            / "crush_lu"
            / "js"
            / "alpine"
            / "core.js"
        ).read_text(encoding="utf-8")
        start = js.index('svg.addEventListener("focus"')
        end = js.index('svg.addEventListener("blur"', start)
        block = js[start:end]
        assert "if (self.focusedIndex < 0 && self.regions.length > 0)" not in block
        assert "self._applyFocus();" in block


class TestLobbyGridsAreNotBrokenLists:
    def test_lobby_and_recap_grids_have_no_role_list(self, client):
        event = _make_event(starts_in_minutes=-30, duration=120)
        alice = _make_member("wp9_alice")
        ben = _make_member("wp9_ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _login(client, alice)
        live = _body(client.get(f"/en/events/{event.pk}/lobby/", HTTP_HOST=HOST))
        assert 'id="lobby-grid"' in live
        assert not re.search(r'id="lobby-grid"[^>]*role="list"', live)

        _end_event(event)
        recap = _body(client.get(f"/en/events/{event.pk}/lobby/", HTTP_HOST=HOST))
        assert 'id="recap-grid"' in recap
        assert not re.search(r'id="recap-grid"[^>]*role="list"', recap)


class TestCantonMapAccessibleNames:
    def test_every_option_has_a_name_inside_a_group(self):
        html = render_to_string("crush_lu/partials/canton_map_svg.html")
        options = re.findall(r"<path\b[^>]*role=\"option\"[^>]*>", html, re.S)
        assert len(options) == 15
        for tag in options:
            name = re.search(r'data-region-name="([^"]+)"', tag).group(1)
            assert f'aria-label="{name}"' in tag
        assert html.count('role="group"') == 2

    def test_locked_phone_input_is_labelled(self, client):
        user = _make_member("wp9_contact", membership=False)
        CrushProfile.objects.filter(user=user).update(
            phone_number="+352661000111", phone_verified=True
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        _login(client, user)
        html = _body(client.get("/en/profile/edit/?section=contact", HTTP_HOST=HOST))
        label_for = re.search(r'<label for="([^"]+)"[^>]*>\s*Phone Number', html)
        assert label_for, "phone label not rendered"
        assert re.search(
            rf'<input type="text" id="{re.escape(label_for.group(1))}"[^>]*readonly',
            html,
        )


def _page_headings(html):
    """Headings inside the page body, skipping the cookie banner / footer."""
    start = html.index("<h1")
    end = html.find("<footer", start)
    return _headings(html[start : end if end != -1 else None])


class TestMemberPagesKeepOneH1AndNoSkips:
    """Onboarding phone + create-profile were h4/h3 step titles under no h1."""

    @pytest.mark.parametrize(
        "path,state",
        [
            ("/en/onboarding/phone/", {"phone_verified": False}),
            (
                "/en/create-profile/",
                {"phone_verified": True, "verification_status": "incomplete"},
            ),
        ],
    )
    def test_onboarding_steps_have_one_h1_and_no_skipped_level(
        self, client, path, state
    ):
        user = _make_member("wp9_steps", membership=False, luxid=False)
        CrushProfile.objects.filter(user=user).update(
            is_approved=False,
            welcome_seen_at=timezone.now(),
            coach_intro_seen_at=timezone.now(),
            **state,
        )
        _login(client, user)
        response = client.get(path, HTTP_HOST=HOST)
        assert response.status_code == 200, response.get("Location")
        headings = _page_headings(_body(response))
        assert [level for level, _ in headings].count(1) == 1, headings
        _assert_no_skipped_level(headings)

    def test_password_change_page_has_an_h1(self, client):
        user = _make_member("wp9_account", membership=False)
        _login(client, user)
        response = client.get("/accounts/password/change/", HTTP_HOST=HOST)
        assert response.status_code == 200
        headings = _headings(_body(response))
        assert 1 in [level for level, _ in headings], headings

    def test_email_management_template_has_an_h1(self, rf):
        # /accounts/email/ redirects on crush.lu, so render the template itself.
        user = _make_member("wp9_email", membership=False)
        request = rf.get("/accounts/email/", HTTP_HOST=HOST)
        request.user = user
        html = render_to_string(
            "account/email_crush.html",
            {"emailaddresses": [], "request": request, "user": user},
            request=request,
        )
        assert 1 in [level for level, _ in _headings(html)]


class TestLoginLogoutPagesHaveAnH1:
    def test_login_page_has_an_h1(self, client):
        response = client.get("/accounts/login/", HTTP_HOST=HOST)
        assert response.status_code == 200
        assert 1 in [level for level, _ in _headings(_body(response))]

    def test_logout_page_has_an_h1(self, client):
        # Anonymous visitors are bounced to the home page, so sign in first.
        user = _make_member("wp9_logout", membership=False)
        _login(client, user)
        response = client.get("/accounts/logout/", HTTP_HOST=HOST)
        assert response.status_code == 200
        assert 1 in [level for level, _ in _headings(_body(response))]
