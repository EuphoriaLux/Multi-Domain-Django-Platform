"""After a claim, the member is asked how and when their coach may reach them.

Covers services.real_person_contact: the off-by-default switch, one request
per member, what was really sent, the member's form, and what the coach sees.
Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from crush_lu.models import (
    ConnectRealPersonCheck,
    CrushProfile,
    EmailPreference,
    Notification,
)
from crush_lu.services.real_person_check import release_check
from crush_lu.services.real_person_contact import (
    request_contact_details,
    save_contact_details,
)
from crush_lu.tests.real_person_check_helpers import make_member
from crush_lu.tests.test_real_person_check_views import RealPersonViewTestCase

ON = override_settings(CONNECT_REAL_PERSON_CONTACT_REQUEST_ENABLED=True)


def _contact_url(check):
    return f"/en/crush-connect/real-person-check/{check.pk}/contact/"


class ContactRequestTestCase(RealPersonViewTestCase):
    def claim_committed(self, coach=None, member=None):
        with self.captureOnCommitCallbacks(execute=True):
            check = self.claim(coach, member)
        check.refresh_from_db()
        return check

    def bells(self, user):
        return Notification.objects.filter(
            user=user, notification_type="real_person_contact"
        )


class SwitchOffTests(ContactRequestTestCase):
    def test_a_claim_sends_nothing_and_the_pages_do_not_exist(self):
        check = self.claim_committed()
        self.assertEqual(mail.outbox, [])
        self.assertFalse(self.bells(self.member).exists())
        self.assertFalse(check.contact_notice)
        self.assertIsNone(check.contact_requested_at)

        self.client.force_login(self.member)
        self.assertEqual(self.client.get(_contact_url(check)).status_code, 404)

        self.client.force_login(self.coach.user)
        html = self.client.get(self.url(check)).content.decode()
        self.assertNotIn("data-contact-answer", html)

    def test_a_stored_whatsapp_answer_opens_no_link_while_off(self):
        check = self.claim_committed()
        ConnectRealPersonCheck.objects.filter(pk=check.pk).update(
            contact_preference="whatsapp", contact_answered_at=timezone.now()
        )
        self.client.force_login(self.coach.user)
        self.assertNotIn("wa.me", self.client.get(self.url(check)).content.decode())


@ON
class ClaimSendsTheRequestTests(ContactRequestTestCase):
    def test_a_new_claim_emails_the_member_and_writes_the_bell(self):
        check = self.claim_committed()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.member.email])
        self.assertIn(self.coach.user.first_name, message.subject)
        self.assertIn(_contact_url(check), message.body)
        self.assertEqual(check.contact_notice, "email")
        self.assertIsNotNone(check.contact_requested_at)
        bell = self.bells(self.member).get()
        self.assertEqual(bell.link_url, _contact_url(check))

    def test_claiming_again_as_the_same_coach_sends_nothing_more(self):
        self.claim_committed()
        self.claim_committed()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.bells(self.member).count(), 1)

    def test_a_refused_email_is_recorded_as_bell_only(self):
        EmailPreference.objects.update_or_create(
            user=self.member, defaults={"email_profile_updates": False}
        )
        check = self.claim_committed()
        self.assertEqual(mail.outbox, [])
        self.assertEqual(check.contact_notice, "bell")
        self.assertTrue(self.bells(self.member).exists())
        self.client.force_login(self.coach.user)
        html = self.client.get(self.url(check)).content.decode()
        self.assertIn("data-contact-answer", html)
        self.assertIn("No email reached", html)
        self.assertNotIn("was asked by email", html)

    def test_a_failing_send_never_fails_the_claim(self):
        with mock.patch(
            "crush_lu.email_helpers.send_real_person_contact_request",
            side_effect=RuntimeError("mail down"),
        ):
            check = self.claim_committed()
        self.assertEqual(check.status, "open")
        self.assertEqual(check.contact_notice, "failed")

    def test_a_second_claim_within_the_cooldown_does_not_email_again(self):
        first = self.claim_committed()
        release_check(self.coach, first.pk)
        second = self.claim_committed(coach=self.other)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(len(mail.outbox), 1)
        first.refresh_from_db()
        self.assertEqual(second.contact_requested_at, first.contact_requested_at)
        self.assertEqual(second.contact_notice, "email")

    def test_an_old_request_is_asked_again(self):
        first = self.claim_committed()
        release_check(self.coach, first.pk)
        ConnectRealPersonCheck.objects.filter(pk=first.pk).update(
            contact_requested_at=timezone.now() - timedelta(days=15)
        )
        self.claim_committed(coach=self.other)
        self.assertEqual(len(mail.outbox), 2)

    def test_a_recent_answer_is_carried_onto_the_next_claim(self):
        first = self.claim_committed()
        save_contact_details(
            self.member, first.pk, preference="call", windows=["weekend_morning"]
        )
        release_check(self.coach, first.pk)
        second = self.claim_committed(coach=self.other)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(second.contact_preference, "call")
        self.assertEqual(second.contact_availability, ["weekend_morning"])
        self.assertIsNotNone(second.contact_answered_at)

    def test_asking_twice_for_one_check_sends_once(self):
        check = self.claim_committed()
        request_contact_details(check.pk)
        self.assertEqual(len(mail.outbox), 1)


@ON
class MemberFormTests(ContactRequestTestCase):
    def test_only_the_checks_member_sees_the_form(self):
        check = self.claim_committed()
        self.client.force_login(self.member)
        response = self.client.get(_contact_url(check))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.coach.user.first_name)
        self.assertContains(response, 'name="preference"')

        stranger = make_member("rpcc_stranger")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(_contact_url(check)).status_code, 404)
        self.client.force_login(self.coach.user)
        self.assertEqual(self.client.get(_contact_url(check)).status_code, 404)

    def test_saving_stores_the_answer_and_tells_the_coach(self):
        check = self.claim_committed()
        self.client.force_login(self.member)
        response = self.client.post(
            _contact_url(check),
            {
                "preference": "sms",
                "windows": ["weekday_evening", "weekend_morning", "not_a_window"],
            },
        )
        self.assertRedirects(response, _contact_url(check))
        check.refresh_from_db()
        self.assertEqual(check.contact_preference, "sms")
        self.assertEqual(
            check.contact_availability, ["weekday_evening", "weekend_morning"]
        )
        self.assertIsNotNone(check.contact_answered_at)
        self.assertEqual(self.bells(self.coach.user).count(), 1)
        self.assertContains(self.client.get(_contact_url(check)), "Saved.")

    def test_an_unknown_preference_is_refused(self):
        check = self.claim_committed()
        self.client.force_login(self.member)
        response = self.client.post(_contact_url(check), {"preference": "fax"})
        self.assertEqual(response.status_code, 400)
        check.refresh_from_db()
        self.assertFalse(check.contact_preference)

    def test_a_closed_check_shows_the_not_open_page_and_saves_nothing(self):
        check = self.claim_committed()
        release_check(self.coach, check.pk)
        self.client.force_login(self.member)
        response = self.client.post(_contact_url(check), {"preference": "call"})
        self.assertContains(response, "data-real-person-call-ended")
        check.refresh_from_db()
        self.assertFalse(check.contact_preference)


@ON
class CoachSeesTheAnswerTests(ContactRequestTestCase):
    def page(self, check):
        self.client.force_login(self.coach.user)
        return self.client.get(self.url(check)).content.decode()

    def test_before_an_answer_the_page_says_the_member_was_asked(self):
        html = self.page(self.claim_committed())
        self.assertIn("was asked by email", html)
        self.assertNotIn("prefers:", html)

    def test_the_answer_shows_with_its_time_windows(self):
        check = self.claim_committed()
        save_contact_details(
            self.member, check.pk, preference="call", windows=["weekday_evening"]
        )
        html = self.page(check)
        self.assertIn("prefers: Phone call", html)
        self.assertIn("Weekday evenings", html)

    def test_whatsapp_first_opens_the_link_for_this_check_only(self):
        EmailPreference.objects.filter(user=self.member).update(whatsapp_opt_in=False)
        check = self.claim_committed()
        self.assertNotIn("wa.me", self.page(check))
        save_contact_details(self.member, check.pk, preference="whatsapp", windows=[])
        html = self.page(check)
        self.assertIn("https://wa.me/352621123456", html)
        self.assertIn("I sent a WhatsApp message", html)
        # The campaign opt-in is the member's own switch and stays untouched.
        self.assertFalse(
            EmailPreference.objects.filter(
                user=self.member, whatsapp_opt_in=True
            ).exists()
        )

    def test_whatsapp_first_still_needs_a_number_on_whatsapp(self):
        EmailPreference.objects.filter(user=self.member).update(whatsapp_opt_in=False)
        check = self.claim_committed()
        save_contact_details(self.member, check.pk, preference="whatsapp", windows=[])
        CrushProfile.objects.filter(user=self.member).update(not_on_whatsapp=True)
        self.assertNotIn("wa.me", self.page(check))


@ON
class RetentionTests(ContactRequestTestCase):
    def test_an_old_answer_is_blanked_with_the_coach_notes(self):
        from django.core.management import call_command

        check = self.claim_committed()
        save_contact_details(
            self.member, check.pk, preference="sms", windows=["weekday_lunch"]
        )
        release_check(self.coach, check.pk)
        old = timezone.now() - timedelta(days=400)
        ConnectRealPersonCheck.objects.filter(pk=check.pk).update(
            claimed_at=old, lease_expires_at=old, decided_at=old
        )
        call_command("gdpr_retention_cleanup", "--apply", verbosity=0)
        check.refresh_from_db()
        self.assertIsNone(check.contact_preference)
        self.assertIsNone(check.contact_availability)
