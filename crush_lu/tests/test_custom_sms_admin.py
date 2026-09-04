"""
Crush-Admin "Custom SMS" page.

Covers: access gate, batch creation with each audience type, personalised
``sms:`` bodies (recipient language + event placeholders), the idempotent
log / undo endpoints, and the pure helpers (placeholder rendering, manual
list parsing).
"""

import re
from datetime import date, timedelta
from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.admin.custom_sms import (
    build_sms_uri,
    canonical_phone,
    parse_manual_recipients,
    render_message,
)
from crush_lu.models import (
    CallAttempt,
    CrushCoach,
    CrushProfile,
    CustomSmsBatch,
    EventRegistration,
    MeetupEvent,
)
from crush_lu.models.profiles import UserDataConsent
from crush_lu.models.site_config import CrushSiteConfig

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}
COMPOSE_URL = "/crush-admin/custom-sms/"


class RenderMessageTests(TestCase):
    def test_substitutes_known_placeholders_only(self):
        body = render_message(
            "Hi {first_name}, {coach_name} here. {unknown} {first_name.__class__}",
            {"first_name": "Marie", "coach_name": "Sophie"},
        )
        self.assertEqual(
            body, "Hi Marie, Sophie here. {unknown} {first_name.__class__}"
        )

    def test_none_values_are_left_literal(self):
        self.assertEqual(
            render_message("{event_title}", {"event_title": None}), "{event_title}"
        )

    def test_sms_uri_encodes_body(self):
        uri = build_sms_uri("+352691000001", "Hé & bonjour")
        self.assertTrue(uri.startswith("sms:+352691000001?body="))
        self.assertEqual(unquote(uri.split("body=", 1)[1]), "Hé & bonjour")
        self.assertNotIn("&", uri.split("body=", 1)[1])

    def test_sms_uri_uses_canonical_number(self):
        self.assertEqual(canonical_phone("+352 691-000.001"), "+352691000001")
        self.assertTrue(
            build_sms_uri("+352 691 000 001", "x").startswith("sms:+352691000001?")
        )


class ParseManualRecipientsTests(TestCase):
    def test_splits_emails_phones_and_junk(self):
        emails, phones, suffixes, junk = parse_manual_recipients(
            "Marie@Example.lu\n+352 691 000 001,\n00352691000002\n691 000 003\nfoo\n\n"
        )
        self.assertEqual(emails, ["marie@example.lu"])
        self.assertEqual(phones, ["+352691000001", "+352691000002"])
        self.assertEqual(suffixes, ["691000003"])
        self.assertEqual(junk, ["foo"])


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CustomSmsAdminTests(TestCase):
    def setUp(self):
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        CrushSiteConfig.get_config()

        self.coach_user = self._user("coach@test.com", "Sophie")
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, is_active=True, max_active_reviews=10
        )
        self.member_user = self._user("member@test.com", "Plain")
        CrushProfile.objects.create(
            user=self.member_user,
            date_of_birth=date(1990, 1, 1),
            gender="M",
            location="Luxembourg",
        )

        self.fr_user = self._user("fr@test.com", "Marie")
        self.fr_profile = self._profile(self.fr_user, "+352691000001", "fr", "F")
        self.de_user = self._user("de@test.com", "Hans")
        self.de_profile = self._profile(self.de_user, "+352691000002", "de", "M")
        self.unverified_user = self._user("unv@test.com", "Noa")
        self.unverified_profile = self._profile(
            self.unverified_user, "+352691000003", "en", "F", verified=False
        )

        self.event = MeetupEvent.objects.create(
            title="Test Speed Dating",
            title_de="Test Speed Dating DE",
            title_fr="Test Speed Dating FR",
            description="desc",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=3),
            location="Luxembourg",
            address="1 Test St",
            max_participants=20,
            min_age=18,
            max_age=45,
            registration_deadline=timezone.now() + timedelta(days=2),
            registration_fee=0,
            is_published=True,
            profile_requirement="approved",
        )
        EventRegistration.objects.create(
            event=self.event, user=self.fr_user, status="confirmed"
        )
        EventRegistration.objects.create(
            event=self.event, user=self.de_user, status="waitlist"
        )
        EventRegistration.objects.create(
            event=self.event, user=self.unverified_user, status="confirmed"
        )

        self.client = Client()
        self.client.login(username="coach@test.com", password="pass")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _user(self, email, first_name):
        user = User.objects.create_user(
            username=email, email=email, password="pass", first_name=first_name
        )
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
        return user

    def _profile(self, user, phone, lang, gender, verified=True):
        return CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender=gender,
            location="Luxembourg",
            is_approved=True,
            verification_status="verified",
            phone_number=phone,
            phone_verified=verified,
            preferred_language=lang,
        )

    def _post_compose(self, **overrides):
        data = {
            "audience_type": "event",
            "event_id": str(self.event.id),
            "registration_statuses": ["confirmed"],
            "message_en": "Hi {first_name}, {coach_name} here: {event_title} on {event_date} {event_url}",
            "message_fr": "Salut {first_name} — {event_title} le {event_date} {event_url}",
        }
        data.update(overrides)
        return self.client.post(COMPOSE_URL, data)

    def _sms_uri_for(self, content, phone):
        match = re.search(r'href="(sms:%s[^"]*)"' % re.escape(phone), content)
        self.assertIsNotNone(match, f"no sms: link for {phone}")
        return unquote(match.group(1))

    # ------------------------------------------------------------------
    # access
    # ------------------------------------------------------------------
    def test_plain_member_is_forbidden(self):
        client = Client()
        client.login(username="member@test.com", password="pass")
        self.assertEqual(client.get(COMPOSE_URL).status_code, 403)
        batch = CustomSmsBatch.objects.create(message_en="x", event=self.event)
        self.assertEqual(client.get(f"{COMPOSE_URL}{batch.pk}/").status_code, 403)
        self.assertEqual(
            client.post(
                f"{COMPOSE_URL}{batch.pk}/log/{self.fr_profile.pk}/"
            ).status_code,
            403,
        )

    def test_anonymous_is_redirected_to_login(self):
        response = Client().get(COMPOSE_URL)
        self.assertEqual(response.status_code, 302)

    def test_superuser_without_coach_row_can_open_page(self):
        User.objects.create_superuser("root@test.com", "root@test.com", "pass")
        client = Client()
        client.login(username="root@test.com", password="pass")
        self.assertEqual(client.get(COMPOSE_URL).status_code, 200)

    # ------------------------------------------------------------------
    # compose
    # ------------------------------------------------------------------
    def test_compose_page_renders_with_quick_link_target(self):
        response = self.client.get(COMPOSE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Custom SMS")
        self.assertContains(response, "{first_name}")
        self.assertContains(response, self.event.title)

    def test_compose_creates_event_batch_and_redirects(self):
        response = self._post_compose(title="Reminder")
        batch = CustomSmsBatch.objects.get()
        self.assertRedirects(
            response, f"{COMPOSE_URL}{batch.pk}/", fetch_redirect_response=False
        )
        self.assertEqual(batch.audience_type, "event")
        self.assertEqual(batch.event, self.event)
        self.assertEqual(batch.registration_statuses, ["confirmed"])
        self.assertEqual(batch.created_by, self.coach_user)
        self.assertEqual(batch.title, "Reminder")

    def test_compose_rejects_unknown_placeholder(self):
        response = self._post_compose(message_en="Hi {frist_name}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unknown placeholder")
        self.assertContains(response, "{frist_name}")
        self.assertFalse(CustomSmsBatch.objects.exists())

    def test_compose_rejects_malformed_placeholders(self):
        for bad in ("{first-name}", "{first_name.foo}", "{ first_name }", "{}"):
            with self.subTest(bad=bad):
                response = self._post_compose(message_en=f"Hi {bad}", message_fr="")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Unknown placeholder")
        self.assertFalse(CustomSmsBatch.objects.exists())

    def test_compose_rejects_event_url_for_unpublished_event(self):
        self.event.is_published = False
        self.event.save(update_fields=["is_published"])
        response = self._post_compose(message_fr="")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not published yet")
        self.assertFalse(CustomSmsBatch.objects.exists())
        # The dropdown no longer offers the draft either.
        page = self.client.get(COMPOSE_URL)
        self.assertNotContains(page, f'value="{self.event.id}"')

    def test_compose_rejects_event_placeholders_without_event(self):
        response = self._post_compose(
            audience_type="manual",
            event_id="",
            manual_recipients="fr@test.com",
            message_en="See you at {event_title}",
            message_fr="",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "need an event selected")
        self.assertFalse(CustomSmsBatch.objects.exists())

    def test_compose_requires_message_and_audience_details(self):
        response = self._post_compose(
            message_en="", message_fr="", registration_statuses=[]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Write the message")
        self.assertContains(response, "at least one registration status")

    def test_duplicate_prefills_form_from_existing_batch(self):
        self._post_compose(title="Original", message_fr="")
        batch = CustomSmsBatch.objects.get()
        response = self.client.get(f"{COMPOSE_URL}?from={batch.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hi {first_name}, {coach_name} here")
        self.assertContains(response, f'value="{self.event.id}" selected')

    # ------------------------------------------------------------------
    # send list
    # ------------------------------------------------------------------
    def test_send_list_personalises_body_per_recipient_language(self):
        self._post_compose()
        batch = CustomSmsBatch.objects.get()
        response = self.client.get(
            f"{COMPOSE_URL}{batch.pk}/", HTTP_ACCEPT_LANGUAGE="de"
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        fr_uri = self._sms_uri_for(content, "+352691000001")
        self.assertIn("Salut Marie", fr_uri)
        self.assertIn("Test Speed Dating FR", fr_uri)
        self.assertIn("/fr/", fr_uri)
        self.assertNotIn("Test Speed Dating DE", fr_uri)

        # Waitlisted Hans is not a confirmed registrant → not listed.
        self.assertNotIn("+352691000002", content)
        # Unverified phone excluded by default.
        self.assertNotIn("+352691000003", content)
        # Header is translated (the request browses in DE) — assert the data attributes.
        self.assertContains(response, 'data-total="1" data-sent="0"')

    def test_send_list_falls_back_to_english_for_german_recipient(self):
        self._post_compose(
            registration_statuses=["confirmed", "waitlist"], message_fr=""
        )
        batch = CustomSmsBatch.objects.get()
        content = self.client.get(f"{COMPOSE_URL}{batch.pk}/").content.decode()
        de_uri = self._sms_uri_for(content, "+352691000002")
        self.assertIn("Hi Hans, Sophie here: Test Speed Dating DE", de_uri)
        self.assertIn("/de/", de_uri)

    def test_banned_and_deactivated_members_are_excluded(self):
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.filter(user=self.fr_user).update(crushlu_banned=True)
        self.de_user.is_active = False
        self.de_user.save(update_fields=["is_active"])
        self.unverified_profile.is_active = False
        self.unverified_profile.save(update_fields=["is_active"])
        self._post_compose(
            registration_statuses=["confirmed", "waitlist"],
            message_fr="",
            include_unverified_phones="on",
        )
        batch = CustomSmsBatch.objects.get()
        response = self.client.get(f"{COMPOSE_URL}{batch.pk}/")
        content = response.content.decode()
        self.assertNotIn("+352691000001", content)  # banned Marie
        self.assertNotIn("+352691000002", content)  # deactivated account Hans
        self.assertNotIn("+352691000003", content)  # deactivated profile Noa
        self.assertContains(response, 'data-total="0"')
        # The log endpoint refuses them too.
        response = self.client.post(
            f"{COMPOSE_URL}{batch.pk}/log/{self.fr_profile.pk}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_include_unverified_phones_opt_in(self):
        self._post_compose(include_unverified_phones="on")
        batch = CustomSmsBatch.objects.get()
        self.assertTrue(batch.include_unverified_phones)
        response = self.client.get(f"{COMPOSE_URL}{batch.pk}/")
        self.assertContains(response, "+352691000003")
        self.assertContains(response, "unverified")

    def test_manual_audience_matches_email_and_phone(self):
        self._post_compose(
            audience_type="manual",
            event_id="",
            manual_recipients="FR@test.com\n691 000 002\nnobody@test.com\nzzz",
            message_en="Hi {first_name}",
            message_fr="",
        )
        batch = CustomSmsBatch.objects.get()
        self.assertEqual(batch.audience_type, "manual")
        self.assertIsNone(batch.event)
        response = self.client.get(f"{COMPOSE_URL}{batch.pk}/")
        content = response.content.decode()
        self.assertIn("+352691000001", content)
        self.assertIn("+352691000002", content)
        self.assertContains(response, "Ignored 1 line")
        self.assertContains(response, "0 / 2 sent")

    def test_manual_audience_matches_formatted_stored_numbers(self):
        """A stored '+352 691 000 005' must match both the pasted E.164 and national forms."""
        lea = self._user("lea@test.com", "Léa")
        self._profile(lea, "+352 691 000 005", "fr", "F")
        self._post_compose(
            audience_type="manual",
            event_id="",
            manual_recipients="+352 691 000 005",
            message_en="Hi {first_name}",
            message_fr="",
        )
        batch = CustomSmsBatch.objects.get()
        content = self.client.get(f"{COMPOSE_URL}{batch.pk}/").content.decode()
        self.assertIn("lea@test.com", content)
        self.assertIn('href="sms:+352691000005?', content)

        self._post_compose(
            audience_type="manual",
            event_id="",
            manual_recipients="691000005",
            message_en="Hi {first_name}",
            message_fr="",
            title="national",
        )
        national = CustomSmsBatch.objects.get(title="national")
        self.assertContains(
            self.client.get(f"{COMPOSE_URL}{national.pk}/"), "lea@test.com"
        )

    def test_custom_sms_rows_do_not_count_as_coach_calls(self):
        """The Analytics coach-workload 'Calls' figure must ignore outreach rows."""
        self._post_compose(message_fr="")
        batch = CustomSmsBatch.objects.get()
        self.client.post(f"{COMPOSE_URL}{batch.pk}/log/{self.fr_profile.pk}/")
        CallAttempt.objects.create(
            profile=self.fr_profile, result="success", coach=self.coach
        )
        User.objects.create_superuser("root2@test.com", "root2@test.com", "pass")
        client = Client()
        client.login(username="root2@test.com", password="pass")
        response = client.get("/crush-admin/dashboard/")
        self.assertEqual(response.status_code, 200)
        workload = {c["name"]: c for c in response.context["coach_workload"]}
        sophie = workload[self.coach_user.get_full_name() or self.coach_user.username]
        self.assertEqual(sophie["calls"], 1)
        self.assertEqual(sophie["call_success_pct"], 100)
        self.assertEqual(response.context["call_summary"]["total"], 1)

    def test_segment_audience_resolves_profiles(self):
        self._post_compose(
            audience_type="segment",
            event_id="",
            segment_key="gender_female",
            message_en="Hi {first_name}",
            message_fr="",
        )
        batch = CustomSmsBatch.objects.get()
        self.assertEqual(batch.segment_key, "gender_female")
        content = self.client.get(f"{COMPOSE_URL}{batch.pk}/").content.decode()
        self.assertIn("+352691000001", content)  # Marie, F, verified phone
        self.assertNotIn("+352691000002", content)  # Hans, M
        self.assertNotIn("+352691000003", content)  # Noa, F but unverified phone

    def test_segment_batch_does_not_count_every_segment(self):
        """Resolving one segment must not pay the dashboard's 64 COUNT queries."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._post_compose(
            audience_type="segment",
            event_id="",
            segment_key="gender_female",
            message_en="Hi {first_name}",
            message_fr="",
        )
        batch = CustomSmsBatch.objects.get()
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(f"{COMPOSE_URL}{batch.pk}/")
        self.assertEqual(response.status_code, 200)
        count_queries = [
            q for q in ctx.captured_queries if "COUNT(" in q["sql"].upper()
        ]
        self.assertLess(len(count_queries), 10, len(count_queries))

    def test_segment_options_endpoint_lists_segments(self):
        response = self.client.get(f"{COMPOSE_URL}segments/?selected=gender_female")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="gender_female" selected')
        self.assertContains(response, "<optgroup")

    # ------------------------------------------------------------------
    # log / undo
    # ------------------------------------------------------------------
    def test_log_creates_one_audit_row_and_is_idempotent(self):
        self._post_compose()
        batch = CustomSmsBatch.objects.get()
        log_url = f"{COMPOSE_URL}{batch.pk}/log/{self.fr_profile.pk}/"

        response = self.client.post(log_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sent")
        self.assertContains(response, 'hx-swap-oob="true"')
        self.assertContains(response, "1 / 1 sent")

        self.client.post(log_url)
        attempts = CallAttempt.objects.filter(
            profile=self.fr_profile, result="custom_sms"
        )
        self.assertEqual(attempts.count(), 1)
        attempt = attempts.get()
        self.assertEqual(attempt.coach, self.coach)
        self.assertEqual(attempt.event, self.event)
        self.assertTrue(
            attempt.notes.startswith(f"[custom-sms:{batch.pk}] Salut Marie")
        )
        self.assertIn("Test Speed Dating FR", attempt.notes)

        # The list page now shows the row as sent and the batch as complete.
        page = self.client.get(f"{COMPOSE_URL}{batch.pk}/")
        self.assertContains(page, "All done")
        self.assertContains(page, 'data-sent="1"')

    def test_log_refuses_profile_outside_audience(self):
        self._post_compose()
        batch = CustomSmsBatch.objects.get()
        response = self.client.post(
            f"{COMPOSE_URL}{batch.pk}/log/{self.de_profile.pk}/"
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(CallAttempt.objects.filter(result="custom_sms").exists())

    def test_log_requires_post(self):
        self._post_compose()
        batch = CustomSmsBatch.objects.get()
        response = self.client.get(f"{COMPOSE_URL}{batch.pk}/log/{self.fr_profile.pk}/")
        self.assertEqual(response.status_code, 405)

    def test_unlog_removes_only_this_batch_rows(self):
        self._post_compose()
        first = CustomSmsBatch.objects.get()
        self._post_compose(title="second")
        second = CustomSmsBatch.objects.get(title="second")
        self.client.post(f"{COMPOSE_URL}{first.pk}/log/{self.fr_profile.pk}/")
        self.client.post(f"{COMPOSE_URL}{second.pk}/log/{self.fr_profile.pk}/")
        self.assertEqual(CallAttempt.objects.filter(result="custom_sms").count(), 2)

        response = self.client.post(
            f"{COMPOSE_URL}{first.pk}/unlog/{self.fr_profile.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open SMS")
        self.assertContains(response, "0 / 1 sent")
        remaining = CallAttempt.objects.filter(result="custom_sms")
        self.assertEqual(remaining.count(), 1)
        self.assertTrue(remaining.get().notes.startswith(second.notes_prefix))

    def test_batch_prefix_does_not_bleed_across_ids(self):
        """[custom-sms:1] must not match rows tagged [custom-sms:12]."""
        batch = CustomSmsBatch.objects.create(message_en="x", event=self.event, pk=1)
        CallAttempt.objects.create(
            profile=self.fr_profile,
            result="custom_sms",
            notes="[custom-sms:12] other batch",
        )
        page = self.client.get(f"{COMPOSE_URL}{batch.pk}/")
        self.assertContains(page, "0 / 1 sent")

    def test_compose_ignores_non_numeric_from(self):
        response = self.client.get(f"{COMPOSE_URL}?from=abc")
        self.assertEqual(response.status_code, 200)

    def test_recent_batches_show_progress_on_compose_page(self):
        self._post_compose(title="Progress check")
        batch = CustomSmsBatch.objects.get()
        self.client.post(f"{COMPOSE_URL}{batch.pk}/log/{self.fr_profile.pk}/")
        response = self.client.get(COMPOSE_URL)
        self.assertContains(response, "Progress check")
        self.assertContains(response, "1 / 1")
