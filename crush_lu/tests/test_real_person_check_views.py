"""Coach views of the Connect real-person check: queue, claim, check page, log,
decide, release, and the coach surfaces behind the flag.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import json
from datetime import timedelta
from unittest import mock
from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from crush_lu import views_coach_real_person
from crush_lu.models import (
    CallAttempt,
    ConnectRealPersonBadge,
    ConnectRealPersonCheck,
    CrushCoach,
    CrushProfile,
    EmailPreference,
    ProfilePhotoReviewState,
    PublishedProfilePhoto,
    UserBlock,
    UserDataConsent,
    UserReport,
)
from crush_lu.real_person_check_rules import (
    CLAIM_TTL,
    DECLINE_COOLDOWN,
    REQUIRED_CHECKLIST_KEYS,
)
from crush_lu.services import real_person_check as rpc
from crush_lu.services.real_person_check import (
    RealPersonCheckError,
    claim_check,
    decide_check,
    revoke_check,
)
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
)
from crush_lu.tests.test_crush_connect import _grant_consent

User = get_user_model()

BASE = "/en/coach/real-person-checks/"
VOICE = {key: True for key in REQUIRED_CHECKLIST_KEYS["voice"]}
VIDEO = {key: True for key in REQUIRED_CHECKLIST_KEYS["video"]}

LIVE_FLAGS = {
    "ROOT_URLCONF": "azureproject.urls_crush",
    "CONNECT_REAL_PERSON_CHECK_ENABLED": True,
    "CONNECT_REAL_PERSON_BADGE_VISIBLE": True,
}


def _flash(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


@override_settings(**LIVE_FLAGS)
class RealPersonViewTestCase(TestCase):
    """Two coaches (both with Crush.lu consent, which the coach panel needs),
    one catalogue member with a verified phone, logged in as the first coach."""

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.coach = make_coach("rpcv_coach")
        self.other = make_coach("rpcv_other")
        for coach in (self.coach, self.other):
            _grant_consent(coach.user)
        self.member = make_member("rpcv_member")
        CrushProfile.objects.filter(user=self.member).update(
            phone_number="+352 621 123 456", phone_verified=True
        )
        self.client.force_login(self.coach.user)

    # -- helpers ----------------------------------------------------------

    def claim(self, coach=None, member=None):
        return claim_check(coach or self.coach, profile_of(member or self.member).pk)

    def url(self, check, suffix=""):
        return f"{BASE}{check.pk}/{suffix}"

    def photo_key(self, member=None):
        return profile_of(member or self.member).photo_1.name

    def decide(self, check, **overrides):
        data = {
            "action": "pass_voice",
            "checklist": json.dumps(VOICE),
            "confirm": "yes",
            "notes": "",
            "photo_key": self.photo_key(),
            "failure_reason": "no_answer",
        }
        data.update(overrides)
        data = {key: value for key, value in data.items() if value is not None}
        return self.client.post(self.url(check, "decide/"), data)

    def log(self, check, htmx=True, **data):
        extra = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(self.url(check, "log/"), data, **extra)

    def expire(self, check):
        ConnectRealPersonCheck.objects.filter(pk=check.pk).update(
            lease_expires_at=timezone.now() - timedelta(minutes=1)
        )

    def opt_in_whatsapp(self, member=None, **overrides):
        """The member's WhatsApp consent (campaigns' rule; spec decision D11)."""
        user = member or self.member
        EmailPreference.objects.update_or_create(
            user=user, defaults={"whatsapp_opt_in": True, **overrides}
        )

    def new_member(self, name, **kwargs):
        user = make_member(name, **kwargs)
        # phone_number is unique, so each member gets their own.
        CrushProfile.objects.filter(user=user).update(
            phone_number=f"+35269{user.pk:07d}", phone_verified=True
        )
        return user


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


class FlagAndAccessTests(RealPersonViewTestCase):
    def all_requests(self, check):
        client = self.client
        return [
            ("get", BASE),
            ("post", f"{BASE}claim/{self.member.pk}/"),
            ("get", self.url(check)),
            ("post", self.url(check, "log/")),
            ("post", self.url(check, "decide/")),
            ("post", self.url(check, "release/")),
        ], client

    def test_flag_off_is_404_on_every_url_for_an_active_coach(self):
        check = self.claim()
        requests, client = self.all_requests(check)
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            for method, url in requests:
                with self.subTest(method=method, url=url):
                    self.assertEqual(getattr(client, method)(url).status_code, 404)
        check.refresh_from_db()
        self.assertEqual(check.status, "open")

    def test_the_badge_flag_alone_does_not_open_the_tooling(self):
        with override_settings(
            CONNECT_REAL_PERSON_CHECK_ENABLED=False,
            CONNECT_REAL_PERSON_BADGE_VISIBLE=True,
        ):
            self.assertEqual(self.client.get(BASE).status_code, 404)

    def test_a_non_coach_is_sent_to_the_dashboard_before_the_flag_matters(self):
        check = self.claim()
        requests, _client = self.all_requests(check)
        _grant_consent(self.member)
        self.client.force_login(self.member)
        for method, url in requests:
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 302)
                self.assertNotIn("real-person", response["Location"])

    def test_an_anonymous_visitor_is_sent_to_login(self):
        self.client.logout()
        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_an_inactive_coach_is_refused(self):
        CrushCoach.objects.filter(pk=self.coach.pk).update(is_active=False)
        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 302)

    def test_another_coachs_check_id_is_a_404_on_every_check_view(self):
        check = self.claim(coach=self.other)
        for method, suffix in (
            ("get", ""),
            ("post", "log/"),
            ("post", "decide/"),
            ("post", "release/"),
        ):
            with self.subTest(method=method, suffix=suffix):
                response = getattr(self.client, method)(
                    self.url(check, suffix),
                    {"result": "sms_sent", "action": "declined"},
                )
                self.assertEqual(response.status_code, 404)
        check.refresh_from_db()
        self.assertEqual(check.status, "open")
        self.assertEqual(check.coach_id, self.other.pk)
        self.assertFalse(CallAttempt.objects.filter(real_person_check=check).exists())

    def test_an_unknown_check_id_is_a_404(self):
        self.assertEqual(self.client.get(f"{BASE}999999/").status_code, 404)

    def test_a_check_about_the_coach_themself_is_refused_everywhere(self):
        mine = make_member("rpcv_coach_member")
        self_coach = CrushCoach.objects.create(user=mine, bio="coach and member")
        _grant_consent(mine)
        now = timezone.now()
        # claim_check never creates this row; the view must still refuse it.
        row = ConnectRealPersonCheck.objects.create(
            profile=profile_of(mine),
            coach=self_coach,
            claimed_at=now,
            lease_expires_at=now + CLAIM_TTL,
        )
        self.client.force_login(mine)
        for method, suffix in (
            ("get", ""),
            ("post", "log/"),
            ("post", "decide/"),
            ("post", "release/"),
        ):
            with self.subTest(method=method, suffix=suffix):
                response = getattr(self.client, method)(
                    self.url(row, suffix), {"result": "sms_sent", "action": "declined"}
                )
                self.assertEqual(response.status_code, 403)
        row.refresh_from_db()
        self.assertEqual(row.status, "open")
        self.assertFalse(CallAttempt.objects.filter(real_person_check=row).exists())

    def test_claiming_yourself_is_refused_with_a_message(self):
        mine = make_member("rpcv_claim_self")
        CrushCoach.objects.create(user=mine, bio="coach and member")
        _grant_consent(mine)
        self.client.force_login(mine)
        response = self.client.post(f"{BASE}claim/{mine.pk}/")
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        self.assertIn("You cannot check your own profile.", _flash(response))
        self.assertFalse(ConnectRealPersonCheck.objects.exists())

    def test_every_check_view_is_get_or_post_only_as_intended(self):
        check = self.claim()
        self.assertEqual(self.client.post(self.url(check)).status_code, 405)
        self.assertEqual(self.client.get(self.url(check, "decide/")).status_code, 405)
        self.assertEqual(self.client.get(self.url(check, "log/")).status_code, 405)
        self.assertEqual(self.client.get(self.url(check, "release/")).status_code, 405)
        self.assertEqual(
            self.client.get(f"{BASE}claim/{self.member.pk}/").status_code, 405
        )


# ---------------------------------------------------------------------------
# Queue and claim
# ---------------------------------------------------------------------------


class QueueTests(RealPersonViewTestCase):
    def test_a_waiting_member_is_listed_with_the_fields_a_coach_triages_on(self):
        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        profile = profile_of(self.member)
        self.assertIn(self.member.first_name, html)
        self.assertIn(profile.age_range, html)
        self.assertIn("last login", html)
        self.assertIn(profile.get_preferred_language_display(), html)
        self.assertIn(f"{BASE}claim/{self.member.pk}/", html)
        # The coach sees the live upload through the photo route.
        self.assertIn(
            reverse(
                "crush_lu:serve_profile_photo",
                kwargs={"user_id": self.member.pk, "photo_field": "photo_1"},
            ),
            html,
        )
        # No phone number and no last name on the list.
        self.assertNotIn("621 123 456", html)
        self.assertEqual(response.context["total_waiting"], 1)

    def test_own_claims_are_listed_once_above_the_queue(self):
        check = self.claim()
        html = self.client.get(BASE).content.decode()
        self.assertIn(f"{BASE}{check.pk}/", html)
        self.assertNotIn(f"{BASE}claim/{self.member.pk}/", html)
        self.assertIn("Your claim ends in", html)

    def test_someone_elses_claim_is_not_on_my_list(self):
        self.claim(coach=self.other)
        response = self.client.get(BASE)
        self.assertEqual(response.context["total_waiting"], 0)
        self.assertEqual(response.context["my_checks"], [])
        self.assertNotIn(f"{BASE}claim/{self.member.pk}/", response.content.decode())

    def test_a_long_queue_is_paged_with_a_visible_count(self):
        for name in ("rpcv_q2", "rpcv_q3"):
            self.new_member(name)
        with mock.patch.object(views_coach_real_person, "QUEUE_PAGE_SIZE", 2):
            first = self.client.get(BASE)
            second = self.client.get(BASE + "?page=2")
        first_html = first.content.decode()
        self.assertIn("Showing 1–2 of 3", first_html)
        self.assertIn("Page 1 of 2", first_html)
        self.assertEqual(len(first.context["page"].object_list), 2)
        self.assertIn("Showing 3–3 of 3", second.content.decode())
        self.assertEqual(len(second.context["page"].object_list), 1)

    def test_claim_buttons_are_disabled_at_capacity(self):
        extra = self.new_member("rpcv_cap")
        self.claim()
        with mock.patch.object(views_coach_real_person, "MAX_OPEN_CLAIMS_PER_COACH", 1):
            response = self.client.get(BASE)
        self.assertTrue(response.context["at_capacity"])
        html = response.content.decode()
        self.assertIn(f"{BASE}claim/{extra.pk}/", html)
        self.assertIn("disabled", html)
        self.assertIn("Release or finish one before claiming another.", html)

    def test_an_empty_queue_says_so(self):
        ConnectRealPersonCheck.objects.all().delete()
        UserDataConsent.objects.filter(user=self.member).update(crushlu_banned=True)
        response = self.client.get(BASE)
        self.assertEqual(response.context["total_waiting"], 0)
        self.assertIn("Nobody is waiting", response.content.decode())

    def test_the_flag_off_hides_nothing_it_just_404s(self):
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            self.assertEqual(self.client.get(BASE).status_code, 404)


class ClaimTests(RealPersonViewTestCase):
    def test_claim_opens_a_check_and_redirects_to_its_page(self):
        response = self.client.post(f"{BASE}claim/{self.member.pk}/")
        check = ConnectRealPersonCheck.objects.get()
        self.assertEqual(check.coach_id, self.coach.pk)
        self.assertEqual(check.status, "open")
        self.assertRedirects(response, self.url(check), fetch_redirect_response=False)

    def test_claiming_again_goes_to_the_same_check(self):
        first = self.client.post(f"{BASE}claim/{self.member.pk}/")
        again = self.client.post(f"{BASE}claim/{self.member.pk}/")
        self.assertEqual(first["Location"], again["Location"])
        self.assertEqual(ConnectRealPersonCheck.objects.count(), 1)

    def test_an_unknown_user_is_a_404(self):
        self.assertEqual(self.client.post(f"{BASE}claim/999999/").status_code, 404)

    def assert_refused(self, member, message):
        response = self.client.post(f"{BASE}claim/{member.pk}/")
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        # The test client keeps unread messages between requests: the last
        # one is this request's.
        self.assertEqual(_flash(response)[-1], message)
        return response

    def test_each_refusal_becomes_a_message_and_a_redirect_to_the_queue(self):
        cases = []

        taken = self.new_member("rpcv_taken")
        self.claim(coach=self.other, member=taken)
        cases.append((taken, "Another coach has already claimed this member."))

        no_consent = self.new_member("rpcv_noconsent")
        UserDataConsent.objects.filter(user=no_consent).update(
            crushlu_consent_given=False
        )
        cases.append((no_consent, "This member has not consented to Crush.lu."))

        banned = self.new_member("rpcv_banned")
        UserDataConsent.objects.filter(user=banned).update(crushlu_banned=True)
        cases.append((banned, "This member is banned from Crush.lu."))

        inactive = self.new_member("rpcv_inactive")
        CrushProfile.objects.filter(user=inactive).update(is_active=False)
        cases.append((inactive, "This member's account is not active."))

        no_photo = self.new_member("rpcv_nophoto")
        CrushProfile.objects.filter(user=no_photo).update(photo_1="")
        cases.append(
            (no_photo, "This member has no photo yet, so it cannot be checked here.")
        )

        # A block is private and a report is not the coach's to read: both
        # get the same vague text.
        vague = "This member is not available for a check right now."
        blocked = self.new_member("rpcv_blocked")
        UserBlock.objects.create(blocker=blocked, blocked=self.coach.user)
        cases.append((blocked, vague))
        reported = self.new_member("rpcv_reported")
        UserReport.objects.create(
            reporter=self.other.user,
            reported_user=reported,
            reason="fake_profile",
            status="open",
        )
        cases.append((reported, vague))

        checked = self.new_member("rpcv_checked")
        decide_check(
            self.other,
            self.claim(coach=self.other, member=checked).pk,
            "pass_voice",
            checklist=VOICE,
            confirmed=True,
        )
        cases.append((checked, "This member already has a real-person badge."))

        for member, message in cases:
            with self.subTest(member=member.username):
                self.assert_refused(member, message)
        # None of them opened a check for this coach.
        self.assertFalse(
            ConnectRealPersonCheck.objects.filter(coach=self.coach).exists()
        )

    def test_a_recent_decline_is_refused_as_a_cooldown(self):
        declined = self.new_member("rpcv_declined")
        decide_check(
            self.other,
            self.claim(coach=self.other, member=declined).pk,
            "declined",
        )
        self.assert_refused(
            declined, "This member declined a check recently and is left alone for now."
        )
        # ... and it ends.
        ConnectRealPersonCheck.objects.filter(profile__user=declined).update(
            decided_at=timezone.now() - DECLINE_COOLDOWN - timedelta(days=1)
        )
        response = self.client.post(f"{BASE}claim/{declined.pk}/")
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response["Location"], BASE)

    def test_the_capacity_limit_is_a_message_with_the_number(self):
        self.claim()
        extra = self.new_member("rpcv_capacity")
        with mock.patch.object(rpc, "MAX_OPEN_CLAIMS_PER_COACH", 1), mock.patch.object(
            views_coach_real_person, "MAX_OPEN_CLAIMS_PER_COACH", 1
        ):
            self.assert_refused(
                extra,
                "You already hold 1 open checks. Release one before claiming another.",
            )

    def test_another_coachs_premium_member_is_refused(self):
        premium = self.new_member("rpcv_premium", premium=True)
        # make_member(premium=True) assigns a coach; the other coach is not it.
        self.assertNotEqual(profile_of(premium).assigned_coach_id, self.coach.pk)
        response = self.client.post(f"{BASE}claim/{premium.pk}/")
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        self.assertFalse(ConnectRealPersonCheck.objects.exists())


# ---------------------------------------------------------------------------
# The check page
# ---------------------------------------------------------------------------


class CheckPageTests(RealPersonViewTestCase):
    def test_the_page_shows_both_photos_the_links_and_the_decision_form(self):
        check = self.claim()
        self.opt_in_whatsapp()
        response = self.client.get(self.url(check))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        photo_url = reverse(
            "crush_lu:serve_profile_photo",
            kwargs={"user_id": self.member.pk, "photo_field": "photo_1"},
        )
        # The live upload and what other members see, side by side.
        self.assertIn(f'src="{photo_url}"', html)
        self.assertIn(f'src="{photo_url}?view=public"', html)
        # The outreach links, on the coach's own phone.
        self.assertIn('href="tel:+352621123456"', html)
        self.assertIn('href="sms:+352621123456?body=', html)
        self.assertIn('href="https://wa.me/352621123456?text=', html)
        self.assertIn("+352 621 123 456", html)
        # The decision form posts the photo the coach was looking at.
        self.assertIn(
            f'name="photo_key" value="{self.photo_key()}"',
            html,
        )
        self.assertIn(f'action="{self.url(check, "decide/")}"', html)
        self.assertIn(f'action="{self.url(check, "release/")}"', html)
        self.assertIn(f'hx-post="{self.url(check, "log/")}"', html)
        # The coach Alpine bundle carries the checklist component.
        self.assertIn('x-data="realPersonCheckChecklist"', html)
        self.assertRegex(html, r"alpine/coach(\.min)?\.js")

    def test_whatsapp_is_offered_only_to_members_who_opted_in(self):
        check = self.claim()
        # Nothing on file is not a yes: SMS and the call link only.
        EmailPreference.objects.filter(user=self.member).update(whatsapp_opt_in=False)
        response = self.client.get(self.url(check))
        html = response.content.decode()
        self.assertIn('href="tel:+352621123456"', html)
        self.assertIn('href="sms:+352621123456?body=', html)
        self.assertNotIn("wa.me", html)
        self.assertNotIn("Open WhatsApp", html)
        self.assertNotIn("I sent a WhatsApp message", html)
        self.assertIn("I sent an SMS", html)
        self.assertEqual(response.context["whatsapp_url"], "")
        # Two buttons fill the row, not three with a hole.
        self.assertIn("grid grid-cols-1 sm:grid-cols-2 gap-2 mb-1", html)
        self.assertNotIn("grid grid-cols-1 sm:grid-cols-3 gap-2 mb-1", html)
        self.opt_in_whatsapp()
        html = self.client.get(self.url(check)).content.decode()
        self.assertIn('href="https://wa.me/352621123456?text=', html)
        self.assertIn("Open WhatsApp", html)
        self.assertIn("I sent a WhatsApp message", html)
        self.assertIn("grid grid-cols-1 sm:grid-cols-3 gap-2 mb-1", html)

    def test_no_whatsapp_for_a_member_who_is_not_on_it_or_unsubscribed(self):
        check = self.claim()
        self.opt_in_whatsapp()
        for name, change in (
            ("not on WhatsApp", {"profile": {"not_on_whatsapp": True}}),
            ("unsubscribed from everything", {"prefs": {"unsubscribed_all": True}}),
        ):
            with self.subTest(name):
                CrushProfile.objects.filter(user=self.member).update(
                    not_on_whatsapp=False
                )
                self.opt_in_whatsapp(unsubscribed_all=False)
                CrushProfile.objects.filter(user=self.member).update(
                    **change.get("profile", {})
                )
                EmailPreference.objects.filter(user=self.member).update(
                    **change.get("prefs", {})
                )
                response = self.client.get(self.url(check))
                html = response.content.decode()
                self.assertNotIn("wa.me", html)
                self.assertNotIn("Open WhatsApp", html)
                # SMS and the call link are unaffected.
                self.assertIn('href="tel:+352621123456"', html)
                self.assertIn('href="sms:+352621123456?body=', html)

    def test_the_invitation_does_not_promise_video_while_only_voice_is_possible(self):
        check = self.claim()
        response = self.client.get(self.url(check))
        sms = unquote(response.context["sms_href"].split("?body=", 1)[1])
        self.assertIn("video call", sms)
        # The member replaced photo 1: the new file waits for review while an
        # earlier one stays published, so they remain on the list but no
        # coach-approved face matches what the coach would be looking at.
        ProfilePhotoReviewState.objects.filter(profile__user=self.member).update(
            status="pending"
        )
        PublishedProfilePhoto.objects.create(
            profile=profile_of(self.member),
            photo_field="photo_1",
            photo_key="users/earlier/photos/earlier.jpg",
        )
        response = self.client.get(self.url(check))
        self.assertFalse(response.context["video_available"])
        self.assertFalse(response.context["links_blocked"])
        sms = unquote(response.context["sms_href"].split("?body=", 1)[1])
        self.assertIn("quick 5-minute call", sms)
        self.assertNotIn("video", sms.lower())
        self.assertNotIn("verified by a coach", sms)

    def test_the_confirmation_promises_a_visible_badge_only_when_badges_are_on(self):
        check = self.claim()
        html = self.client.get(self.url(check)).content.decode()
        self.assertIn("Other members will see the badge.", html)
        self.assertNotIn("once badges are switched on", html)
        with override_settings(CONNECT_REAL_PERSON_BADGE_VISIBLE=False):
            html = self.client.get(self.url(check)).content.decode()
        self.assertNotIn("Other members will see the badge.", html)
        self.assertIn(
            "other members will see it once badges are switched on.",
            html,
        )

    def test_the_badges_off_confirmation_is_translated(self):
        check = self.claim()
        with override_settings(CONNECT_REAL_PERSON_BADGE_VISIBLE=False):
            for lang, text in (
                (
                    "de",
                    "andere Mitglieder sehen es, sobald Abzeichen freigeschaltet sind",
                ),
                (
                    "fr",
                    "les autres membres le verront dès que les badges seront activés",
                ),
            ):
                with self.subTest(lang):
                    html = self.client.get(
                        f"/{lang}/coach/real-person-checks/{check.pk}/"
                    ).content.decode()
                    self.assertIn(text, html)
                    self.assertNotIn("once badges are switched on", html)

    def test_the_decision_heading_and_the_dialog_name_are_translated(self):
        check = self.claim()
        for lang, heading in (("de", "Entscheidung"), ("fr", "Décision")):
            with self.subTest(lang):
                html = self.client.get(
                    f"/{lang}/coach/real-person-checks/{check.pk}/"
                ).content.decode()
                self.assertRegex(
                    html, rf'id="rpc-decision-heading"[^>]*>{heading}</h3>'
                )
                self.assertNotIn(">Version:<", html)
        # The dialog is named by whichever sentence it asks.
        html = self.client.get(self.url(check)).content.decode()
        self.assertIn('id="rpc-confirm-pass"', html)
        self.assertIn('id="rpc-confirm-fake"', html)
        self.assertIn('x-bind:aria-labelledby="confirmLabelId"', html)
        self.assertIn('x-ref="confirmPanel"', html)
        self.assertIn('x-ref="confirmButton"', html)

    def test_required_keys_render_from_the_server_constant(self):
        check = self.claim()
        html = self.client.get(self.url(check)).content.decode()
        self.assertIn(
            'data-required-voice="%s"' % ",".join(REQUIRED_CHECKLIST_KEYS["voice"]),
            html,
        )
        self.assertIn(
            'data-required-video="%s"' % ",".join(REQUIRED_CHECKLIST_KEYS["video"]),
            html,
        )
        self.assertIn('data-video-available="true"', html)
        # One checkbox per distinct key, labelled.
        for key in REQUIRED_CHECKLIST_KEYS["video"]:
            self.assertEqual(html.count(f'data-key="{key}"'), 1, key)
        for label in (
            "I spoke with them live",
            "Name matches the profile",
            "Age looks consistent with the profile",
            "I explained the badge and they agreed",
            "Their face matches photo 1",
            "Live camera, not a recording",
        ):
            self.assertIn(label, html)

    def test_every_checklist_key_has_a_label(self):
        for key in REQUIRED_CHECKLIST_KEYS["video"]:
            self.assertIn(key, views_coach_real_person.CHECKLIST_LABELS)

    def test_the_page_names_the_member_and_never_exposes_other_coaches_notes(self):
        check = self.claim()
        html = self.client.get(self.url(check)).content.decode()
        self.assertIn(self.member.first_name, html)
        self.assertIn("Contact attempts", html)

    def test_a_banned_member_blanks_every_contact_link_and_says_why(self):
        check = self.claim()
        UserDataConsent.objects.filter(user=self.member).update(crushlu_banned=True)
        response = self.client.get(self.url(check))
        html = response.content.decode()
        for needle in ("tel:", "sms:", "wa.me", "621 123 456"):
            self.assertNotIn(needle, html)
        self.assertIn("Contact links are switched off for this member.", html)
        self.assertIn("This member is banned from Crush.lu.", html)
        self.assertTrue(response.context["links_blocked"])

    def test_a_pending_fake_report_blanks_the_links_without_naming_the_report(self):
        check = self.claim()
        UserReport.objects.create(
            reporter=self.other.user,
            reported_user=self.member,
            reason="fake_profile",
            status="open",
        )
        html = self.client.get(self.url(check)).content.decode()
        for needle in ("tel:", "sms:", "wa.me"):
            self.assertNotIn(needle, html)
        self.assertIn("This member is not available for a check right now.", html)
        self.assertNotIn("report", html.lower().split("contact links")[1][:400])

    def test_a_blocked_pair_blanks_the_links(self):
        check = self.claim()
        UserBlock.objects.create(blocker=self.member, blocked=self.coach.user)
        html = self.client.get(self.url(check)).content.decode()
        for needle in ("tel:", "sms:", "wa.me"):
            self.assertNotIn(needle, html)
        # A block is private: the text never says "blocked".
        self.assertNotIn("blocked", html.lower())

    def test_an_unverified_phone_has_no_links_and_no_guard_noise(self):
        check = self.claim()
        CrushProfile.objects.filter(user=self.member).update(phone_verified=False)
        response = self.client.get(self.url(check))
        html = response.content.decode()
        for needle in ("tel:", "sms:", "wa.me"):
            self.assertNotIn(needle, html)
        self.assertIn("no verified phone number", html)
        self.assertFalse(response.context["links_blocked"])

    def test_a_non_public_main_photo_makes_video_unavailable(self):
        check = self.claim()
        ProfilePhotoReviewState.objects.filter(profile__user=self.member).update(
            status="pending"
        )
        response = self.client.get(self.url(check))
        html = response.content.decode()
        self.assertFalse(response.context["video_available"])
        self.assertIn('data-video-available="false"', html)
        self.assertIn("Video is unavailable", html)

    def test_a_lapsed_claim_is_read_only(self):
        check = self.claim()
        self.expire(check)
        response = self.client.get(self.url(check))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("has lapsed", html)
        self.assertNotIn('x-data="realPersonCheckChecklist"', html)
        for needle in ("tel:", "sms:", "wa.me"):
            self.assertNotIn(needle, html)

    def test_a_closed_check_shows_its_outcome_and_the_coachs_own_notes(self):
        check = self.claim()
        decide_check(self.coach, check.pk, "declined", notes="rpcv-private-coach-note")
        html = self.client.get(self.url(check)).content.decode()
        self.assertIn("This check is closed: Declined.", html)
        self.assertIn("rpcv-private-coach-note", html)
        self.assertNotIn('x-data="realPersonCheckChecklist"', html)

    def test_release_is_offered_on_a_live_check_only(self):
        check = self.claim()
        self.assertIn(
            self.url(check, "release/"),
            self.client.get(self.url(check)).content.decode(),
        )
        self.expire(check)
        self.assertNotIn(
            self.url(check, "release/"),
            self.client.get(self.url(check)).content.decode(),
        )


# ---------------------------------------------------------------------------
# Logging contact attempts (HTMX)
# ---------------------------------------------------------------------------


class LogTests(RealPersonViewTestCase):
    def test_logging_an_sms_returns_the_partial_and_renews_the_lease(self):
        check = self.claim()
        soon = timezone.now() + timedelta(hours=1)
        ConnectRealPersonCheck.objects.filter(pk=check.pk).update(lease_expires_at=soon)
        response = self.log(check, result="sms_sent")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="rpc-outreach"', html)
        self.assertNotIn("<html", html)  # a partial, not the page
        self.assertIn("SMS template sent", html)
        self.assertIn("Contact attempt logged.", html)
        check.refresh_from_db()
        self.assertGreater(check.lease_expires_at, soon + timedelta(hours=60))
        attempt = CallAttempt.objects.get(real_person_check=check)
        self.assertEqual(attempt.result, "sms_sent")
        self.assertEqual(attempt.coach_id, self.coach.pk)
        self.assertEqual(attempt.profile_id, profile_of(self.member).pk)
        self.assertIsNone(attempt.submission_id)

    def test_logging_a_whatsapp_message(self):
        check = self.claim()
        response = self.log(check, result="whatsapp_sent")
        self.assertIn("WhatsApp template sent", response.content.decode())
        self.assertEqual(
            CallAttempt.objects.get(real_person_check=check).result, "whatsapp_sent"
        )

    def test_a_failed_attempt_needs_a_reason_and_keeps_its_notes(self):
        check = self.claim()
        refused = self.log(check, result="failed")
        self.assertEqual(refused.status_code, 200)
        self.assertIn("Choose why the attempt failed.", refused.content.decode())
        self.assertFalse(CallAttempt.objects.filter(real_person_check=check).exists())

        logged = self.log(
            check, result="failed", failure_reason="voicemail", notes="left a message"
        )
        self.assertIn("Failed: Voicemail left", logged.content.decode())
        attempt = CallAttempt.objects.get(real_person_check=check)
        self.assertEqual(attempt.failure_reason, "voicemail")
        self.assertEqual(attempt.notes, "left a message")

    def test_a_bad_reason_or_result_is_refused_with_a_message(self):
        check = self.claim()
        bad_reason = self.log(check, result="failed", failure_reason="nonsense")
        self.assertIn("Choose why the attempt failed.", bad_reason.content.decode())
        bad_result = self.log(check, result="success")
        self.assertIn(
            "Choose what you did to reach this member.", bad_result.content.decode()
        )
        self.assertFalse(CallAttempt.objects.filter(real_person_check=check).exists())

    def test_notes_over_the_limit_are_refused(self):
        check = self.claim()
        response = self.log(check, result="sms_sent", notes="x" * 1001)
        self.assertIn(
            "Notes can be at most 1000 characters.", response.content.decode()
        )
        self.assertFalse(CallAttempt.objects.filter(real_person_check=check).exists())

    def test_a_failing_guard_refuses_the_log_and_leaves_the_links_blank(self):
        check = self.claim()
        UserDataConsent.objects.filter(user=self.member).update(crushlu_banned=True)
        response = self.log(check, result="sms_sent")
        html = response.content.decode()
        self.assertIn("This member is banned from Crush.lu.", html)
        for needle in ("tel:", "sms:", "wa.me"):
            self.assertNotIn(needle, html)
        self.assertFalse(CallAttempt.objects.filter(real_person_check=check).exists())

    def test_a_lapsed_claim_redirects_htmx_to_the_queue_and_releases_it(self):
        check = self.claim()
        self.expire(check)
        response = self.log(check, result="sms_sent")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], BASE)
        check.refresh_from_db()
        self.assertEqual(check.status, "released")
        self.assertFalse(CallAttempt.objects.filter(real_person_check=check).exists())

    def test_a_plain_post_redirects_back_to_the_check_page(self):
        check = self.claim()
        response = self.log(check, htmx=False, result="sms_sent")
        self.assertRedirects(response, self.url(check), fetch_redirect_response=False)
        self.assertIn("Contact attempt logged.", _flash(response)[0])
        self.assertTrue(CallAttempt.objects.filter(real_person_check=check).exists())

    def test_logging_never_touches_the_screening_call_history(self):
        check = self.claim()
        self.log(check, result="sms_sent")
        attempt = CallAttempt.objects.get(real_person_check=check)
        self.assertIsNone(attempt.submission_id)
        self.assertEqual(
            CallAttempt.objects.filter(real_person_check__isnull=True).count(), 0
        )


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


class DecideTests(RealPersonViewTestCase):
    def test_a_video_pass_grants_the_badge_and_returns_to_the_queue(self):
        check = self.claim()
        response = self.decide(
            check,
            action="pass_video",
            checklist=json.dumps(VIDEO),
            notes="looked right",
        )
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        check.refresh_from_db()
        self.assertEqual((check.status, check.medium), ("passed", "video"))
        self.assertEqual(check.photo_key, self.photo_key())
        self.assertEqual(check.checklist, VIDEO)
        self.assertEqual(check.notes, "looked right")
        badge = ConnectRealPersonBadge.objects.get(profile__user=self.member)
        self.assertEqual((badge.medium, badge.source_check_id), ("video", check.pk))
        self.assertTrue(
            CallAttempt.objects.filter(
                real_person_check=check, result="success"
            ).exists()
        )
        self.assertIn("Video check passed for", _flash(response)[0])

    def test_a_voice_pass_needs_no_photo_key(self):
        check = self.claim()
        response = self.decide(check, photo_key=None)
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        check.refresh_from_db()
        self.assertEqual(
            (check.status, check.medium, check.photo_key), ("passed", "voice", "")
        )
        self.assertIn("Voice check passed for", _flash(response)[0])

    def test_a_stale_photo_key_does_not_block_outcomes_that_compare_no_face(self):
        for action, checklist in (
            ("pass_voice", json.dumps(VOICE)),
            ("declined", ""),
        ):
            with self.subTest(action=action):
                check = self.claim()
                response = self.decide(
                    check,
                    action=action,
                    checklist=checklist,
                    photo_key="users/1/photos/older.jpg",
                )
                self.assertRedirects(response, BASE, fetch_redirect_response=False)
                check.refresh_from_db()
                self.assertNotEqual(check.status, "open")
                ConnectRealPersonBadge.objects.all().delete()
                ConnectRealPersonCheck.objects.filter(pk=check.pk).update(
                    status="released"
                )

    def test_unreachable_logs_a_failed_attempt_and_keeps_the_member_queued(self):
        check = self.claim()
        response = self.decide(
            check,
            action="unreachable",
            checklist="",
            confirm="",
            failure_reason="voicemail",
        )
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        check.refresh_from_db()
        self.assertEqual(check.status, "unreachable")
        attempt = CallAttempt.objects.get(real_person_check=check)
        self.assertEqual(
            (attempt.result, attempt.failure_reason), ("failed", "voicemail")
        )
        self.assertIn("unreachable", _flash(response)[0])
        self.assertEqual(
            self.client.get(BASE).context["total_waiting"],
            1,
            "an unreachable member stays on the list",
        )

    def test_unreachable_rejects_an_unknown_reason(self):
        check = self.claim()
        response = self.decide(
            check, action="unreachable", checklist="", failure_reason="nonsense"
        )
        self.assertEqual(response.status_code, 400)
        check.refresh_from_db()
        self.assertEqual(check.status, "open")

    def test_declined_starts_the_cooldown(self):
        check = self.claim()
        response = self.decide(check, action="declined", checklist="", confirm="")
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        check.refresh_from_db()
        self.assertEqual(check.status, "declined")
        self.assertEqual(self.client.get(BASE).context["total_waiting"], 0)
        self.assertIn("declined", _flash(response)[0])

    def test_suspected_fake_opens_a_report_that_names_only_the_check(self):
        check = self.claim()
        response = self.decide(
            check,
            action="suspected_fake",
            checklist="",
            notes="rpcv-secret-reason",
        )
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        check.refresh_from_db()
        self.assertEqual(check.status, "suspected_fake")
        report = UserReport.objects.get(reported_user=self.member)
        self.assertEqual(report.status, "open")
        self.assertEqual(report.reason, "fake_profile")
        self.assertEqual(report.reporter_id, self.coach.user_id)
        self.assertEqual(report.details, f"Real-person check #{check.pk}")
        self.assertNotIn("rpcv-secret-reason", report.details)
        self.assertIn("suspected fake", _flash(response)[0])
        # ... and the member leaves everyone's list until a superuser acts.
        self.assertEqual(self.client.get(BASE).context["total_waiting"], 0)

    def test_a_suspected_fake_needs_the_confirmation_too(self):
        check = self.claim()
        response = self.decide(check, action="suspected_fake", checklist="", confirm="")
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Confirm your decision before saving it.", response.content.decode()
        )
        self.assertFalse(UserReport.objects.exists())
        check.refresh_from_db()
        self.assertEqual(check.status, "open")

    def test_a_pass_without_the_confirmation_is_a_400_and_changes_nothing(self):
        check = self.claim()
        for confirm in ("", "no", None):
            with self.subTest(confirm=confirm):
                response = self.decide(check, confirm=confirm)
                self.assertEqual(response.status_code, 400)
                self.assertIn(
                    "Confirm your decision before saving it.",
                    response.content.decode(),
                )
        check.refresh_from_db()
        self.assertEqual(check.status, "open")
        self.assertFalse(ConnectRealPersonBadge.objects.exists())

    def test_a_bad_checklist_is_a_400_with_the_page_back(self):
        check = self.claim()
        incomplete = dict(VOICE)
        incomplete.pop("badge_explained")
        cases = {
            "incomplete": (json.dumps(incomplete), "Tick every item on the checklist"),
            "video keys on a voice pass": (
                json.dumps(VIDEO),
                "The checklist could not be read.",
            ),
            "not json": ("{nope", "The checklist could not be read."),
            "a list": ("[]", "The checklist could not be read."),
            "string values": (
                json.dumps({key: "true" for key in VOICE}),
                "The checklist could not be read.",
            ),
        }
        for name, (payload, message) in cases.items():
            with self.subTest(name):
                response = self.decide(check, checklist=payload)
                self.assertEqual(response.status_code, 400)
                html = response.content.decode()
                self.assertIn(message, html)
                self.assertIn('x-data="realPersonCheckChecklist"', html)
        check.refresh_from_db()
        self.assertEqual(check.status, "open")
        self.assertFalse(ConnectRealPersonBadge.objects.exists())

    def test_a_crafted_checklist_is_a_400_never_a_500(self):
        check = self.claim()
        cases = {
            # json.loads recurses until it raises RecursionError (not a ValueError).
            "deeply nested list": "[" * 100000,
            "deeply nested object": '{"a":' * 50000,
            "a valid but huge object": json.dumps({"k" * 5000: True}),
            "just over the cap": json.dumps({"spoke_live": True, "pad": "x" * 2100}),
        }
        for name, payload in cases.items():
            with self.subTest(name):
                response = self.decide(check, checklist=payload)
                self.assertEqual(response.status_code, 400)
                html = response.content.decode()
                self.assertIn("The checklist could not be read.", html)
                self.assertIn('x-data="realPersonCheckChecklist"', html)
        check.refresh_from_db()
        self.assertEqual(check.status, "open")
        self.assertFalse(ConnectRealPersonBadge.objects.exists())

    def test_a_recursion_error_from_the_parser_is_a_clean_refusal(self):
        # Defence in depth: a payload under the size cap that still recurses.
        with mock.patch.object(
            views_coach_real_person.json, "loads", side_effect=RecursionError
        ):
            with self.assertRaises(RealPersonCheckError) as caught:
                views_coach_real_person._parse_checklist('{"a": 1}')
        self.assertEqual(
            (caught.exception.code, caught.exception.status), ("invalid_checklist", 400)
        )

    def test_a_real_checklist_fits_well_inside_the_size_cap(self):
        self.assertLess(
            len(json.dumps(VIDEO)), views_coach_real_person.CHECKLIST_MAX_BYTES // 4
        )
        check = self.claim()
        response = self.decide(check, action="pass_voice", checklist=json.dumps(VOICE))
        self.assertRedirects(response, BASE, fetch_redirect_response=False)

    def test_a_video_pass_with_a_voice_checklist_is_a_400(self):
        check = self.claim()
        response = self.decide(check, action="pass_video", checklist=json.dumps(VOICE))
        self.assertEqual(response.status_code, 400)
        self.assertIn("Tick every item on the checklist", response.content.decode())

    def test_a_stale_or_missing_photo_key_is_a_400_and_shows_the_new_photo(self):
        check = self.claim()
        for photo_key in ("users/1/photos/older.jpg", "", None):
            with self.subTest(photo_key=photo_key):
                response = self.decide(
                    check,
                    action="pass_video",
                    checklist=json.dumps(VIDEO),
                    photo_key=photo_key,
                )
                self.assertEqual(response.status_code, 400)
                html = response.content.decode()
                self.assertIn(escape("This member's photo changed."), html)
                # The re-rendered form carries the key to compare now.
                self.assertIn(f'name="photo_key" value="{self.photo_key()}"', html)
        check.refresh_from_db()
        self.assertEqual(check.status, "open")

    def test_a_video_pass_needs_the_photo_other_members_see(self):
        check = self.claim()
        ProfilePhotoReviewState.objects.filter(profile__user=self.member).update(
            status="pending"
        )
        response = self.decide(check, action="pass_video", checklist=json.dumps(VIDEO))
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            escape("No coach has approved this member's current main photo"),
            response.content.decode(),
        )
        check.refresh_from_db()
        self.assertEqual(check.status, "open")

    def test_a_banned_member_cannot_be_passed_but_the_check_can_be_closed(self):
        check = self.claim()
        UserDataConsent.objects.filter(user=self.member).update(crushlu_banned=True)
        refused = self.decide(check)
        self.assertEqual(refused.status_code, 403)
        self.assertIn("This member is banned from Crush.lu.", refused.content.decode())
        self.assertFalse(ConnectRealPersonBadge.objects.exists())
        # A stale check can still be closed without a badge.
        closed = self.decide(check, action="declined", checklist="", confirm="")
        self.assertRedirects(closed, BASE, fetch_redirect_response=False)

    def test_notes_over_the_limit_are_a_400(self):
        check = self.claim()
        response = self.decide(check, notes="x" * 1001)
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Notes can be at most 1000 characters.", response.content.decode()
        )
        check.refresh_from_db()
        self.assertEqual(check.status, "open")

    def test_an_unknown_action_is_a_400(self):
        check = self.claim()
        for action in ("", "approve"):
            with self.subTest(action=action):
                response = self.decide(check, action=action)
                self.assertEqual(response.status_code, 400)
                self.assertIn(
                    "Choose what to do with this check.", response.content.decode()
                )

    def test_a_lapsed_claim_is_released_and_the_coach_is_told(self):
        check = self.claim()
        self.expire(check)
        response = self.decide(check)
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        self.assertIn("has lapsed", _flash(response)[0])
        check.refresh_from_db()
        self.assertEqual(check.status, "released")
        self.assertFalse(ConnectRealPersonBadge.objects.exists())

    def test_deciding_twice_is_refused_the_second_time(self):
        check = self.claim()
        self.decide(check)
        again = self.decide(check)
        self.assertRedirects(again, BASE, fetch_redirect_response=False)
        self.assertEqual(_flash(again)[-1], "This check is already closed.")
        self.assertEqual(ConnectRealPersonBadge.objects.count(), 1)

    def test_a_failed_decision_keeps_the_typed_notes(self):
        check = self.claim()
        response = self.decide(check, confirm="", notes="remember me")
        self.assertEqual(response.status_code, 400)
        self.assertIn("remember me", response.content.decode())


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


class ReleaseTests(RealPersonViewTestCase):
    def test_release_gives_the_member_back_to_every_coach(self):
        check = self.claim()
        response = self.client.post(self.url(check, "release/"))
        self.assertRedirects(response, BASE, fetch_redirect_response=False)
        check.refresh_from_db()
        self.assertEqual(check.status, "released")
        self.assertIn("You released", _flash(response)[0])
        self.assertTrue(self.claim(coach=self.other).pk)

    def test_releasing_a_closed_check_says_so(self):
        check = self.claim()
        self.client.post(self.url(check, "release/"))
        again = self.client.post(self.url(check, "release/"))
        self.assertRedirects(again, BASE, fetch_redirect_response=False)
        self.assertEqual(_flash(again)[-1], "This check is already closed.")


# ---------------------------------------------------------------------------
# Coach surfaces behind the flag
# ---------------------------------------------------------------------------


class NavigationTests(RealPersonViewTestCase):
    def test_nav_dashboard_and_drawer_link_to_the_queue_only_with_the_flag(self):
        for url in ("/en/coach/dashboard/", "/en/coach/queue/"):
            with self.subTest(url=url, flag="on"):
                html = self.client.get(url).content.decode()
                self.assertIn(BASE, html)
                self.assertIn("Real-person checks", html)
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            for url in ("/en/coach/dashboard/", "/en/coach/queue/"):
                with self.subTest(url=url, flag="off"):
                    html = self.client.get(url).content.decode()
                    self.assertNotIn(BASE, html)
                    self.assertNotIn("Real-person checks", html)

    def test_the_link_appears_in_the_dropdown_the_nav_and_the_dashboard(self):
        html = self.client.get("/en/coach/dashboard/").content.decode()
        # dropdown + collapsed nav + mobile drawer + dashboard tile
        self.assertGreaterEqual(html.count(f'href="{BASE}"'), 3)


class ActionQueueTests(RealPersonViewTestCase):
    def items(self, response, kind="real_person_check"):
        return [item for item in response.context["items"] if item["kind"] == kind]

    def test_my_live_claim_is_in_the_inbox_with_its_own_links(self):
        check = self.claim()
        response = self.client.get("/en/coach/queue/")
        self.assertEqual(response.status_code, 200)
        (item,) = self.items(response)
        self.assertEqual(item["url_kwargs"], {"check_id": check.pk})
        self.assertEqual(response.context["counts"]["real_person_check"], 1)
        html = response.content.decode()
        # Both the title link and the Open button reverse the check page.
        self.assertEqual(html.count(f'href="{self.url(check)}"'), 2)
        self.assertIn("claim ends in", html)
        self.assertNotIn("/coach/connections/0/", html)

    def test_the_total_matches_the_rows_and_the_check_count_is_shown(self):
        self.claim()
        response = self.client.get("/en/coach/queue/")
        counts = response.context["counts"]
        self.assertEqual(counts["total"], len(response.context["items"]))
        self.assertEqual((counts["total"], counts["real_person_check"]), (1, 1))
        # The checks are part of Total, so they are counted where a coach looks.
        self.assertIn("Real-person checks: 1", response.content.decode())

    def test_the_count_line_is_absent_with_no_claim_or_the_flag_off(self):
        html = self.client.get("/en/coach/queue/").content.decode()
        self.assertNotIn("Real-person checks:", html)
        self.claim()
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            html = self.client.get("/en/coach/queue/").content.decode()
        self.assertNotIn("Real-person checks", html)

    def test_the_claim_is_urgent_as_its_lease_runs_down(self):
        check = self.claim()
        ConnectRealPersonCheck.objects.filter(pk=check.pk).update(
            lease_expires_at=timezone.now() + timedelta(hours=3)
        )
        (item,) = self.items(self.client.get("/en/coach/queue/"))
        self.assertEqual(item["sla_state"], "urgent")

    def test_a_lapsed_or_foreign_claim_is_not_listed(self):
        mine = self.claim()
        self.expire(mine)
        other_member = self.new_member("rpcv_aq_other")
        self.claim(coach=self.other, member=other_member)
        response = self.client.get("/en/coach/queue/")
        self.assertEqual(self.items(response), [])
        self.assertEqual(response.context["counts"]["real_person_check"], 0)

    def test_with_the_flag_off_the_inbox_is_unchanged(self):
        self.claim()
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            response = self.client.get("/en/coach/queue/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.items(response), [])
        self.assertEqual(response.context["counts"]["real_person_check"], 0)
        self.assertEqual(response.context["counts"]["total"], 0)
        self.assertNotIn("real-person", response.content.decode())


class MemberOverviewCardTests(RealPersonViewTestCase):
    def overview(self, user=None):
        return self.client.get(f"/en/coach/member/{(user or self.member).pk}/")

    def test_no_card_with_the_flag_off(self):
        self.claim()
        with override_settings(CONNECT_REAL_PERSON_CHECK_ENABLED=False):
            html = self.overview().content.decode()
        self.assertNotIn("real-person-check-card", html)

    def test_a_member_without_a_check_shows_no_check_and_no_badge(self):
        response = self.overview()
        html = response.content.decode()
        self.assertIn("real-person-check-card", html)
        self.assertIn("No check yet.", html)
        self.assertIn("No badge", html)

    def test_my_open_claim_links_back_to_the_check(self):
        check = self.claim()
        html = self.overview().content.decode()
        self.assertIn("Open", html)
        self.assertIn(f'href="{self.url(check)}"', html)
        self.assertIn("Continue the check", html)

    def test_someone_elses_claim_is_status_only_without_a_link(self):
        check = self.claim(coach=self.other)
        html = self.overview().content.decode()
        self.assertIn("real-person-check-card", html)
        self.assertNotIn(self.url(check), html)
        self.assertNotIn("Continue the check", html)

    def test_a_pass_shows_the_medium_the_date_and_the_badge_state(self):
        check = self.claim()
        decide_check(
            self.coach,
            check.pk,
            "pass_video",
            checklist=VIDEO,
            confirmed=True,
            photo_key=self.photo_key(),
            notes="rpcv-overview-private-note",
        )
        card = self.overview().context["real_person_card"]
        self.assertEqual(card["check"]["status"], "passed")
        self.assertEqual(card["badge_medium"], "video")
        html = self.overview().content.decode()
        self.assertIn("Passed", html)
        self.assertIn("Video call", html)
        self.assertIn("Badge: video-verified by a coach", html)
        # Status only: never the notes, the checklist or the checking coach.
        self.assertNotIn("rpcv-overview-private-note", html)
        self.assertNotIn("spoke_live", html)

    def test_the_badge_note_says_when_members_cannot_see_it_yet(self):
        check = self.claim()
        decide_check(
            self.coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True
        )
        with override_settings(CONNECT_REAL_PERSON_BADGE_VISIBLE=False):
            html = self.overview().content.decode()
        self.assertIn("Badge: spoke with a coach", html)
        self.assertIn("not shown to members yet", html)
        html = self.overview().content.decode()
        self.assertNotIn("not shown to members yet", html)

    def test_a_lapsed_claim_reads_as_released(self):
        check = self.claim()
        self.expire(check)
        card = self.overview().context["real_person_card"]
        self.assertEqual(card["check"]["status"], "released")
        self.assertIsNone(card["check"]["open_id"])

    def test_a_member_without_a_profile_still_renders(self):
        guest = User.objects.create_user("rpcv_guest", "rpcv_guest@example.com", "pw")
        response = self.overview(guest)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["real_person_card"])

    def test_the_member_reading_their_own_page_sees_status_only(self):
        mine = make_member("rpcv_self_view")
        CrushCoach.objects.create(user=mine, bio="coach and member")
        _grant_consent(mine)
        self.client.force_login(mine)
        # A pass by another coach is the member's to know; the rest is not.
        passed = self.claim(coach=self.other, member=mine)
        decide_check(
            self.other,
            passed.pk,
            "pass_voice",
            checklist=VOICE,
            confirmed=True,
            notes="rpcv-self-view-note",
        )
        response = self.overview(mine)
        card = response.context["real_person_card"]
        self.assertTrue(card["is_self"])
        self.assertEqual(card["check"]["status"], "passed")
        self.assertIsNone(card["check"]["open_id"])
        html = response.content.decode()
        self.assertNotIn("rpcv-self-view-note", html)
        self.assertNotIn("Continue the check", html)

    def test_the_member_never_reads_a_suspected_fake_verdict_on_themself(self):
        mine = make_member("rpcv_self_fake")
        CrushCoach.objects.create(user=mine, bio="coach and member")
        _grant_consent(mine)
        check = self.claim(coach=self.other, member=mine)
        decide_check(self.other, check.pk, "suspected_fake", notes="rpcv-fake-note")
        self.client.force_login(mine)
        response = self.overview(mine)
        self.assertIsNone(response.context["real_person_card"]["check"])
        html = response.content.decode()
        self.assertNotIn("Suspected fake", html)
        self.assertNotIn("rpcv-fake-note", html)

    def test_only_the_filing_coach_and_a_superuser_read_a_suspected_fake_verdict(self):
        # The claim page keeps a pending report vague on purpose, so another
        # coach must not read it off the member page either.
        check = self.claim(coach=self.other)
        decide_check(self.other, check.pk, "suspected_fake", notes="rpcv-fake-note")
        # A different coach: neutral, in the context and in the page.
        response = self.overview()
        card = response.context["real_person_card"]["check"]
        self.assertEqual(
            (card["status"], str(card["status_label"])), ("closed", "Closed")
        )
        html = response.content.decode()
        self.assertNotIn("Suspected fake", html)
        self.assertNotIn("suspected_fake", html)
        self.assertNotIn("rpcv-fake-note", html)
        # The coach who filed it knows what they decided.
        self.client.force_login(self.other.user)
        self.assertEqual(
            self.overview().context["real_person_card"]["check"]["status"],
            "suspected_fake",
        )
        self.assertIn("Suspected fake", self.overview().content.decode())
        # A superuser triages these reports.
        admin = User.objects.create_superuser(
            "rpcv_admin", "rpcv_admin@example.com", "pw"
        )
        CrushCoach.objects.create(user=admin, bio="admin coach")
        _grant_consent(admin)
        self.client.force_login(admin)
        self.assertEqual(
            self.overview().context["real_person_card"]["check"]["status"],
            "suspected_fake",
        )

    def test_a_revoked_pass_is_neutral_to_other_coaches_too(self):
        check = self.claim(coach=self.other)
        decide_check(
            self.other, check.pk, "pass_voice", checklist=VOICE, confirmed=True
        )
        admin = User.objects.create_superuser(
            "rpcv_admin2", "rpcv_admin2@example.com", "pw"
        )
        revoke_check(admin, check.pk, "rpcv-revoke-reason")
        card = self.overview().context["real_person_card"]["check"]
        self.assertEqual(card["status"], "closed")
        self.assertNotIn("Revoked", self.overview().content.decode())
        self.assertNotIn("rpcv-revoke-reason", self.overview().content.decode())
        self.client.force_login(self.other.user)
        self.assertEqual(
            self.overview().context["real_person_card"]["check"]["status"], "revoked"
        )

    def test_the_member_cannot_open_the_claim_on_themself_from_their_card(self):
        mine = make_member("rpcv_self_open")
        self_coach = CrushCoach.objects.create(user=mine, bio="coach and member")
        _grant_consent(mine)
        now = timezone.now()
        ConnectRealPersonCheck.objects.create(
            profile=profile_of(mine),
            coach=self_coach,
            claimed_at=now,
            lease_expires_at=now + CLAIM_TTL,
        )
        self.client.force_login(mine)
        card = self.overview(mine).context["real_person_card"]
        self.assertIsNone(card["check"]["open_id"])
