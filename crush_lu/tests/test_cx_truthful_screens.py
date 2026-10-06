"""CX bundle P4-1 -- screens that said something untrue or unclear.

Each class renders one screen as the right member in the right state, in EN,
DE and FR, and asserts three things: the new, truthful text is shown; the old
wrong text is gone; and a negative control proving the surrounding behaviour
did not move (a state that must still show a button or a section still does).

* SD-06  waitlist success screen offered "Pay with Card" for a seat the member
         does not have (the server refuses it); My Events linked a ticket that
         404s for waitlisted registrations.
* XC-07  nav "Profile Status" said "Not Submitted" for members who finished
         the wizard and are awaiting verification.
* XC-09  the verification page claimed a mail "was sent" on a path where
         nothing was sent.
* SD-11  late-cancel credit caveat (credit is all-or-nothing against a seat).
* CC-20  Connect readiness said 3 suggestions a day; the product gives up to 2
         (``get_or_create_todays_cards`` returns fewer when the pool is small).
* CC-06  Connect promised a coach for members who get a private chat.
* CC-15  weekly review showed the viewer's own guess unlabelled.

Paths are literal and the host is ``crush.lu``: ``reverse("crush_lu:...")``
builds ``/crush/...`` paths that 404 under that host (see AGENTS.md).

Run with: pytest crush_lu/tests/test_cx_truthful_screens.py -v
"""

import re
from datetime import date
from html import unescape as _unescape

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.utils import translation

from crush_lu.models import (
    CrushConnectMembership,
    CrushProfile,
    EventRegistration,
    ProfileSubmission,
    UserDataConsent,
)
from crush_lu.models.crush_connect_cycle import ConnectCycleCard
from crush_lu.tests.test_crush_connect import (
    _login_eligible,
    _make_user,
    _mark_attended,
    _set_gate_questions,
)
from crush_lu.tests.test_crush_credit import CreditFixture

User = get_user_model()

LANGS = ("en", "de", "fr")


def _flat(response):
    """Response body, HTML entities decoded (a view-side string with an
    apostrophe renders as ``&#x27;``), whitespace runs collapsed to one space."""
    return re.sub(r"\s+", " ", _unescape(response.content.decode("utf-8")))


def _translated(lang, msgid):
    with translation.override(lang):
        return translation.gettext(msgid)


class _CrushHostTestCase(TestCase):
    """crush.lu host, a Site row, and a clean cache (shared @ratelimit counter)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "crush.lu", "name": "Crush.lu"}
        )

    def setUp(self):
        super().setUp()
        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"


# ---------------------------------------------------------------------------
# SD-06 -- waitlist success screen + My Events ticket link
# ---------------------------------------------------------------------------


class WaitlistSuccessScreenTests(CreditFixture):
    """CreditFixture: one paid event (max 1) whose only seat is already held."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def _register(self, user, event, lang="en"):
        self.client.force_login(user)
        return self.client.post(
            f"/{lang}/events/{event.id}/register/",
            {"preferred_age_min": "25", "preferred_age_max": "40"},
            HTTP_HX_REQUEST="true",
        )

    def _reg_of(self, user, event):
        return EventRegistration.objects.get(user=user, event=event)

    def test_waitlisted_member_on_paid_event_is_not_offered_payment(self):
        expected = {
            "en": (
                "You're on the Waitlist!",
                "The event is currently full, but we'll notify you if a spot "
                "opens up.",
                "You pay nothing unless a place opens up for you.",
            ),
            "de": (
                "Du bist auf der Warteliste!",
                "Das Event ist derzeit ausgebucht, aber wir benachrichtigen "
                "dich, wenn ein Platz frei wird.",
                "Du zahlst nichts, solange kein Platz für dich frei wird.",
            ),
            "fr": (
                "Vous êtes sur la liste d'attente !",
                "L'événement est complet pour le moment, mais nous vous "
                "préviendrons si une place se libère.",
                "Vous ne payez rien tant qu'une place ne se libère pas pour vous.",
            ),
        }
        for lang in LANGS:
            with self.subTest(lang=lang):
                member = self._user(f"waitlister-{lang}@crush.lu")
                response = self._register(member, self.event, lang)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(self._reg_of(member, self.event).status, "waitlist")
                body = _flat(response)
                for text in expected[lang]:
                    self.assertIn(text, body)
                # The payment buttons are gone, in the member's own language.
                self.assertNotIn("data-sumup-reg-id", body)
                self.assertNotIn(_translated(lang, "Pay with Card"), body)
                self.assertNotIn(_translated(lang, "Pay with Crush Credit"), body)
                self.assertNotIn("Pay with Card", body)
                # Old defects: DE typo "wennein" and FR informal "on te".
                self.assertNotIn("wennein", body)
                self.assertNotIn("on te préviendra", body)

    def test_waitlisted_member_with_enough_credit_is_not_offered_credit_payment(self):
        # The credit button is gated by the same condition as the card button.
        member = self._user("waitlister-credit@crush.lu")
        html = render_to_string(
            "crush_lu/_event_registration_success.html",
            {
                "event": self.event,
                "registration": EventRegistration.objects.create(
                    event=self.event, user=member, status="waitlist"
                ),
                "waitlist_position": 1,
                "has_sufficient_crush_credit": True,
            },
        )
        self.assertIn("You're on the Waitlist!", html)
        self.assertNotIn("Pay with Crush Credit", html)
        self.assertNotIn("Pay with Card", html)

    def test_free_event_waitlist_does_not_mention_payment(self):
        free = self._event(fee=0, max_participants=1, title="Free Full Event")
        # The fixture member takes the single free seat.
        EventRegistration.objects.create(event=free, user=self.user, status="confirmed")
        member = self._user("waitlister-free@crush.lu")

        response = self._register(member, free)

        self.assertEqual(self._reg_of(member, free).status, "waitlist")
        body = _flat(response)
        self.assertIn("You're on the Waitlist!", body)
        self.assertNotIn("You pay nothing unless a place opens up for you.", body)
        self.assertNotIn("data-sumup-reg-id", body)

    def test_waitlisted_member_whose_payment_is_already_held_is_not_told_they_pay_nothing(
        self,
    ):
        # A late cancel keeps ``payment_confirmed`` on the row; re-registering on
        # a full event reuses that row, so the member is waitlisted with their
        # money still held (``_admitted_status``). "You pay nothing unless a place
        # opens up" would be false for them.
        paid = self._registration_in_state(
            "waitlist", "waitlist-already-paid@crush.lu", paid=True
        )
        unpaid = self._registration_in_state("waitlist", "waitlist-unpaid@crush.lu")

        def render(registration):
            return render_to_string(
                "crush_lu/_event_registration_success.html",
                {
                    "event": self.event,
                    "registration": registration,
                    "waitlist_position": 1,
                    "has_sufficient_crush_credit": False,
                },
            )

        with self.subTest(payment_confirmed=True):
            html = render(paid)
            self.assertIn("You're on the Waitlist!", html)
            self.assertNotIn("You pay nothing unless a place opens up for you.", html)
            self.assertNotIn("data-sumup-reg-id", html)
        with self.subTest(payment_confirmed=False):  # negative control
            html = render(unpaid)
            self.assertIn("You pay nothing unless a place opens up for you.", html)
            self.assertNotIn("data-sumup-reg-id", html)

    def test_negative_control_seat_holder_on_paid_event_still_gets_pay_button(self):
        # Same paid event type, but a seat is free: signup lands "pending" and
        # the member must still be offered payment.
        roomy = self._event(fee=self.event.registration_fee, max_participants=5)
        member = self._user("seat-holder@crush.lu")

        response = self._register(member, roomy)

        self.assertEqual(self._reg_of(member, roomy).status, "pending")
        body = _flat(response)
        self.assertIn("Your Spot Is Reserved!", body)
        self.assertIn("Pay with Card", body)
        self.assertIn('data-payment-method="card"', body)
        self.assertNotIn("You pay nothing unless a place opens up for you.", body)

    def test_negative_control_partial_still_offers_payment_where_payable(self):
        def render(registration, credit):
            return render_to_string(
                "crush_lu/_event_registration_success.html",
                {
                    "event": self.event,
                    "registration": registration,
                    "waitlist_position": None,
                    "has_sufficient_crush_credit": credit,
                },
            )

        pending = self._registration_in_state("pending", "pending@crush.lu")
        confirmed_unpaid = self._registration_in_state(
            "confirmed", "confirmed-unpaid@crush.lu"
        )
        confirmed_paid = self._registration_in_state(
            "confirmed", "confirmed-paid@crush.lu", paid=True
        )
        applied = self._registration_in_state("applied", "applied@crush.lu")

        for registration in (pending, confirmed_unpaid):
            with self.subTest(status=registration.status):
                html = render(registration, credit=True)
                self.assertIn('data-payment-method="card"', html)
                self.assertIn('data-payment-method="credit"', html)
                self.assertIn("Pay with Card", html)
        # Unchanged exclusions: already paid, and an application holds no seat.
        for registration in (confirmed_paid, applied):
            with self.subTest(excluded=registration.status):
                html = render(registration, credit=True)
                self.assertNotIn("data-sumup-reg-id", html)

    def _registration_in_state(self, status, email, paid=False):
        member = self._user(email)
        return EventRegistration.objects.create(
            event=self.event,
            user=member,
            status=status,
            payment_confirmed=paid,
        )


class MyEventsTicketLinkTests(CreditFixture):
    """The ticket page 404s unless the registration holds a seat."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def _ticket_href(self, event, lang="en"):
        return f'href="/{lang}/events/{event.id}/ticket/"'

    def test_waitlist_has_no_ticket_link_but_seat_holders_do(self):
        waitlisted_event = self._event(hours_away=96, title="Waitlisted Event")
        pending_event = self._event(hours_away=120, title="Pending Event")
        applied_event = self._event(hours_away=144, title="Applied Event")
        # self.event: the fixture's confirmed + paid seat.
        self._registration(waitlisted_event, self.user, status="waitlist")
        self._registration(pending_event, self.user, status="pending")
        self._registration(applied_event, self.user, status="applied")

        self.client.force_login(self.user)
        response = self.client.get("/en/my-events/")
        body = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        # Cards are all on the page...
        for event in (self.event, waitlisted_event, pending_event, applied_event):
            self.assertIn(event.title, body)
        # ...seat-holding ones keep the ticket link (negative control)...
        self.assertIn(self._ticket_href(self.event), body)
        self.assertIn(self._ticket_href(pending_event), body)
        # ...a waitlisted registration does not get a link that 404s...
        self.assertNotIn(self._ticket_href(waitlisted_event), body)
        # ...and an application still does not (existing behaviour).
        self.assertNotIn(self._ticket_href(applied_event), body)

    def test_the_removed_link_really_was_dead(self):
        # Guards the premise: the ticket view refuses a waitlisted member.
        waitlisted_event = self._event(hours_away=96, title="Waitlisted Event")
        self._registration(waitlisted_event, self.user, status="waitlist")
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{waitlisted_event.id}/ticket/")

        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# XC-07 -- nav "Profile Status" label
# ---------------------------------------------------------------------------


class NavProfileStatusLabelTests(_CrushHostTestCase):
    LABELS = {
        # lang: (Awaiting verification, Not Submitted, Rejected, Approved)
        "en": ("Awaiting verification", "Not Submitted", "Rejected", "Approved"),
        "de": (
            "Warten auf Verifizierung",
            "Nicht eingereicht",
            "Abgelehnt",
            "Genehmigt",
        ),
        "fr": ("En attente de vérification", "Non soumis", "Rejeté", "Approuvé"),
    }

    def _member(self, name, verification_status, is_approved=False):
        user = User.objects.create_user(
            username=f"{name}@example.com",
            email=f"{name}@example.com",
            password="testpass123",
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            verification_status=verification_status,
            is_approved=is_approved,
            completion_status="submitted",
        )
        return user, profile

    def _events_page(self, user, lang):
        self.client.force_login(user)
        response = self.client.get(f"/{lang}/events/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode("utf-8")

    def _desktop_label(self, label, tone):
        return f'<span class="text-{tone}-500">{label}</span>'

    def test_pending_member_without_submission_is_awaiting_verification(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                awaiting, not_submitted, _rejected, _approved = self.LABELS[lang]
                user, _ = self._member(f"pending-{lang}", "pending")
                self.assertFalse(ProfileSubmission.objects.filter(profile__user=user))

                body = self._events_page(user, lang)

                # Desktop dropdown AND mobile drawer both carry the new label.
                self.assertIn(self._desktop_label(awaiting, "blue"), body)
                self.assertIn(f"⏳ {awaiting}</span>", body)
                # The wrong label is gone from both.
                self.assertNotIn(not_submitted, body)

    def test_rejected_member_without_submission_is_rejected_not_unsubmitted(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                _awaiting, not_submitted, rejected, _approved = self.LABELS[lang]
                user, _ = self._member(f"rejected-{lang}", "rejected")

                body = self._events_page(user, lang)

                self.assertIn(self._desktop_label(rejected, "red"), body)
                self.assertIn(f"✗ {rejected}</span>", body)
                self.assertNotIn(not_submitted, body)

    def test_incomplete_profile_still_reads_not_submitted(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                awaiting, not_submitted, _rejected, _approved = self.LABELS[lang]
                user, _ = self._member(f"incomplete-{lang}", "incomplete")

                body = self._events_page(user, lang)

                self.assertIn(self._desktop_label(not_submitted, "gray"), body)
                self.assertIn(f">{not_submitted}</span>", body)
                self.assertNotIn(awaiting, body)

    def test_verified_and_approved_member_still_reads_approved(self):
        user, _ = self._member("approved-en", "verified", is_approved=True)

        body = self._events_page(user, "en")

        self.assertIn('<span class="text-green-500">Approved ✓</span>', body)
        self.assertNotIn("Awaiting verification", body)
        self.assertNotIn("Not Submitted", body)

    def test_legacy_pending_submission_label_wins(self):
        # A real submission under review keeps "Pending Review": the new
        # branches sit after every ProfileSubmission-driven label.
        user, profile = self._member("legacy-pending", "pending")
        ProfileSubmission.objects.create(profile=profile, status="pending")

        body = self._events_page(user, "en")

        self.assertIn('<span class="text-blue-500">Pending Review</span>', body)
        self.assertNotIn("Awaiting verification", body)

    def test_the_translations_ship_in_the_compiled_catalogue(self):
        for lang in LANGS:
            awaiting, _not_submitted, rejected, _approved = self.LABELS[lang]
            self.assertEqual(_translated(lang, "Awaiting verification"), awaiting)
            self.assertEqual(_translated(lang, "Rejected"), rejected)


# ---------------------------------------------------------------------------
# XC-09 -- verification-sent page without a pending email
# ---------------------------------------------------------------------------


class VerificationSentPageTests(_CrushHostTestCase):
    URL = "/accounts/confirm-email/"
    OLD = (
        "We've sent a verification link to your email address. "
        "Click the link in the email to confirm your account."
    )

    IMPLIES_SENT = {
        "en": ("the link we sent",),
        "de": ("den wir dir geschickt haben",),
        "fr": ("que nous vous avons envoyé",),
    }

    def _get(self, lang):
        return self.client.get(self.URL, HTTP_ACCEPT_LANGUAGE=lang)

    def test_page_no_longer_claims_a_mail_was_just_sent(self):
        expected = {
            "en": (
                "If you asked for a verification link, check your inbox. Didn't "
                "get it, or has it expired? Enter your email below and we'll "
                "send a new one.",
                "Still nothing after a few minutes? Write to",
            ),
            "de": (
                "Wenn du einen Bestätigungslink angefordert hast, schau in dein "
                "Postfach. Nichts bekommen oder abgelaufen? Gib unten deine "
                "E-Mail-Adresse ein, dann senden wir dir einen neuen Link.",
                "Nach ein paar Minuten noch nichts? Schreib an",
            ),
            "fr": (
                "Si vous avez demandé un lien de vérification, consultez votre "
                "boîte de réception. Rien reçu, ou le lien a expiré ? Saisissez "
                "votre adresse e-mail ci-dessous et nous vous en enverrons un "
                "nouveau.",
                "Toujours rien après quelques minutes ? Écrivez à",
            ),
        }
        for lang in LANGS:
            with self.subTest(lang=lang):
                response = self._get(lang)

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(
                    response, "account/verification_sent_crush.html"
                )
                body = _flat(response)
                for text in expected[lang]:
                    self.assertIn(text, body)
                self.assertIn('href="mailto:support@crush.lu"', body)
                # The email box that the sentence points to is on the page.
                self.assertIn('name="email"', body)
                # Old claim gone in every language.
                self.assertNotIn(self.OLD, body)
                self.assertNotIn(_translated(lang, self.OLD), body)
                # No evidence of a send on this branch: nothing may call the link
                # "the link we sent" (first draft of this fix did).
                for sent in self.IMPLIES_SENT[lang]:
                    self.assertNotIn(sent, body)

    def test_negative_control_pending_email_branch_keeps_its_sentence(self):
        session = self.client.session
        session["pending_verification_email"] = "someone@example.com"
        session.save()

        response = self._get("en")

        body = _flat(response)
        self.assertIn("We've sent a verification link to", body)
        self.assertIn("<strong>", body)
        self.assertNotIn("If you asked for a verification link", body)
        # The support line helps on this branch too.
        self.assertIn("Still nothing after a few minutes? Write to", body)
        self.assertIn('href="mailto:support@crush.lu"', body)


# ---------------------------------------------------------------------------
# SD-11 -- late-cancel credit caveat
# ---------------------------------------------------------------------------


class LateCancelCreditCaveatTests(CreditFixture):
    EXPECTED = {
        "en": (
            "Crush Credit can only be used when your balance covers the full "
            "price of an event; smaller amounts are added to what you already "
            "have."
        ),
        "de": (
            "Crush Credit lässt sich nur einsetzen, wenn dein Guthaben den "
            "vollen Preis eines Events abdeckt; kleinere Beträge werden deinem "
            "bestehenden Guthaben hinzugefügt."
        ),
        "fr": (
            "Le Crush Credit ne peut être utilisé que si votre solde couvre le "
            "prix complet d’un événement ; les montants plus petits s’ajoutent "
            "à votre solde existant."
        ),
    }

    def setUp(self):
        super().setUp()
        cache.clear()

    def _cancel_page(self, user, event, lang="en"):
        self.client.force_login(user)
        return self.client.get(f"/{lang}/events/{event.pk}/cancel/")

    def test_late_cancel_with_resale_share_carries_the_caveat(self):
        event = self._event(hours_away=10, max_participants=5)
        user = self._user("late-caveat@crush.lu")
        self._paid_registration(event, user)

        for lang in LANGS:
            with self.subTest(lang=lang):
                response = self._cancel_page(user, event, lang)

                self.assertContains(response, 'data-outcome="late"')
                self.assertContains(response, self.EXPECTED[lang])
                self.assertContains(response, 'data-testid="cancellation-outcome"')

    def test_caveat_sits_inside_the_outcome_box_before_the_confirm_button(self):
        event = self._event(hours_away=10, max_participants=5)
        user = self._user("late-order@crush.lu")
        self._paid_registration(event, user)

        html = self._cancel_page(user, event).content.decode("utf-8")

        self.assertLess(
            html.index('data-testid="cancellation-outcome"'),
            html.index(self.EXPECTED["en"]),
        )
        self.assertLess(
            html.index(self.EXPECTED["en"]), html.index("Yes, Cancel Registration")
        )

    def test_full_credit_preview_has_no_caveat(self):
        # The fixture member holds a paid seat 72 h away: full-credit outcome.
        response = self._cancel_page(self.user, self.event)

        self.assertContains(response, 'data-outcome="credit"')
        for text in self.EXPECTED.values():
            self.assertNotContains(response, text)

    def test_late_cancel_without_attributable_payment_has_no_caveat(self):
        from django.utils import timezone

        event = self._event(hours_away=10, max_participants=5)
        user = self._user("late-legacy@crush.lu")
        EventRegistration.objects.create(
            event=event,
            user=user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )

        response = self._cancel_page(user, event)

        self.assertContains(response, 'data-outcome="late"')
        self.assertNotContains(response, self.EXPECTED["en"])

    def test_unpaid_cancel_preview_has_no_caveat(self):
        event = self._event(hours_away=10, max_participants=5)
        user = self._user("late-unpaid@crush.lu")
        self._registration(event, user, status="pending")

        response = self._cancel_page(user, event)

        self.assertNotContains(response, self.EXPECTED["en"])


# ---------------------------------------------------------------------------
# CC-20 / CC-06 / CC-15 -- Crush Connect
# ---------------------------------------------------------------------------


@override_settings(CRUSH_CONNECT_LAUNCHED=False, CRUSH_CONNECT_CANDIDATE_OPEN=True)
class ConnectReadinessNumbersTests(_CrushHostTestCase):
    """CARDS_PER_DAY is 2 (services/connect_cycle.py) and a day can hold fewer
    when the pool is small, so the copy says "up to" two; it used to say 3."""

    EXPECTED = {
        "en": (
            "To receive up to 2 new profiles each day, complete these steps",
            "up to two new profiles a day",
            "unlocks the active journey with up to two new profiles per day.",
        ),
        "de": (
            "Um täglich bis zu 2 neue Profile zu erhalten, schließe diese Schritte ab",
            "bis zu zwei neue Profile pro Tag",
            "schaltet den aktiven Weg mit bis zu zwei neuen Profilen pro Tag frei.",
        ),
        "fr": (
            "Pour recevoir jusqu'à 2 nouveaux profils par jour, complétez ces étapes",
            "jusqu'à deux nouveaux profils par jour",
            "débloque le parcours actif avec jusqu'à deux nouveaux profils par jour.",
        ),
    }
    STALE = {
        "en": (
            "3 suggestions",
            "three daily suggestions",
            "three suggestions per day",
            # Promises exactly two (also false when the pool is small):
            "To receive your 2 new profiles each day",
            "your two daily profiles",
        ),
        "de": (
            "3 Vorschläge",
            "drei täglichen Vorschläge",
            "drei Vorschlägen",
            "Um täglich 2 neue Profile zu erhalten",
            "deine zwei täglichen Profile",
        ),
        "fr": (
            "3 propositions",
            "trois suggestions quotidiennes",
            "trois propositions",
            "Pour recevoir 2 nouveaux profils par jour",
            "vos deux profils quotidiens",
        ),
    }

    def test_hub_readiness_card_says_up_to_two_profiles_a_day(self):
        from crush_lu.services.connect_cycle import CARDS_PER_DAY

        self.assertEqual(CARDS_PER_DAY, 2)  # the number the copy must match
        for lang in LANGS:
            with self.subTest(lang=lang):
                # LuxID-only, not onboarded, no event: card + LuxID trust text.
                me = _make_user(
                    username=f"ready-{lang}", premium=False, onboarded=False
                )
                _login_eligible(self.client, me)

                response = self.client.get(f"/{lang}/crush-connect/home/")

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data-trust-status="luxid"')
                body = _flat(response)
                for text in self.EXPECTED[lang]:
                    self.assertIn(text, body)
                for stale in self.STALE[lang]:
                    self.assertNotIn(stale, body)

    def test_event_verified_member_trust_text_is_unchanged(self):
        me = _make_user(
            username="ready-event", premium=False, onboarded=False, has_luxid=False
        )
        _mark_attended(me)
        _login_eligible(self.client, me)

        response = self.client.get("/en/crush-connect/home/")

        self.assertContains(response, 'data-trust-status="event"')
        self.assertContains(
            response,
            "A Crush coach confirmed your participation at an in-person event.",
        )
        self.assertNotContains(response, "three daily suggestions")
        self.assertNotContains(response, "up to two new profiles a day")


class ConnectMarketingAndWizardPromiseTests(_CrushHostTestCase):
    """Free members get a private chat on a mutual yes; no coach is involved."""

    OLD_STEP4 = (
        "When it's mutual, your Crush Coach contacts you both and arranges "
        "the date personally."
    )
    OLD_STEP1 = "No wrong answers — your coach just needs a starting point."
    OLD_STEP2 = "These help your coach find someone whose rhythm matches yours."

    STEP4 = {
        "en": "a private chat opens so you can plan a coffee together.",
        "de": "öffnet sich ein privater Chat, in dem ihr gemeinsam einen Kaffee plant.",
        "fr": "une discussion privée s'ouvre pour organiser un café ensemble.",
    }
    STEP4_PREMIUM = {
        "en": "Premium members also get their coach's weekly pick.",
        "de": "Premium-Mitglieder erhalten zusätzlich die wöchentliche Auswahl ihres Coachs.",
        "fr": "Les membres Premium reçoivent en plus la proposition hebdomadaire de leur coach.",
    }
    STEP1 = {
        "en": "No wrong answers — we just need a starting point.",
        "de": "Es gibt keine falschen Antworten — wir brauchen nur einen Ausgangspunkt.",
        "fr": "Il n'y a pas de mauvaises réponses — nous avons juste besoin d'un point de départ.",
    }
    STEP2 = {
        "en": "These help us find someone whose rhythm matches yours.",
        "de": "Das hilft uns, jemanden zu finden, dessen Rhythmus zu deinem passt.",
        "fr": "Cela nous aide à trouver quelqu'un dont le rythme correspond au vôtre.",
    }

    def test_anonymous_marketing_page_no_longer_promises_a_coach_for_free_members(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                response = self.client.get(f"/{lang}/crush-connect/")

                self.assertEqual(response.status_code, 200)
                body = _flat(response)
                self.assertIn(self.STEP4[lang], body)
                self.assertIn(self.STEP4_PREMIUM[lang], body)
                self.assertNotIn(self.OLD_STEP4, body)
                self.assertNotIn(_translated(lang, self.OLD_STEP4), body)

    def test_marketing_page_still_describes_the_premium_coach_pick(self):
        # Negative control: only the free-member step changed. The Premium
        # Coach's Pick sentence, where a coach really arranges the date, stays.
        body = _flat(self.client.get("/en/crush-connect/"))

        self.assertIn(
            "a real Crush Coach chooses one person for you, tells you why, and",
            body,
        )

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_wizard_steps_do_not_promise_a_coach(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                me = _make_user(
                    username=f"wizard-{lang}",
                    premium=False,
                    onboarded=False,
                    preferred_genders=["F"],
                )
                _mark_attended(me)
                _login_eligible(self.client, me)

                step1 = self.client.get(f"/{lang}/crush-connect/onboarding/1/")
                CrushConnectMembership.objects.filter(user=me).update(onboarding_step=2)
                step2 = self.client.get(f"/{lang}/crush-connect/onboarding/2/")

                self.assertEqual(step1.status_code, 200)
                self.assertEqual(step2.status_code, 200)
                self.assertIn(self.STEP1[lang], _flat(step1))
                self.assertIn(self.STEP2[lang], _flat(step2))
                self.assertNotIn(self.OLD_STEP1, _flat(step1))
                self.assertNotIn(self.OLD_STEP2, _flat(step2))
                self.assertNotIn(_translated(lang, self.OLD_STEP1), _flat(step1))
                self.assertNotIn(_translated(lang, self.OLD_STEP2), _flat(step2))
                # Negative control: the step still renders its question tiles.
                self.assertIn("connect-card", _flat(step1))


@override_settings(CRUSH_CONNECT_LAUNCHED=True)
class WeeklyReviewGuessLabelTests(_CrushHostTestCase):
    GUESS = {"en": "Your guess:", "de": "Dein Tipp:", "fr": "Votre supposition :"}
    YES_NO = {
        "en": {True: "Yes", False: "No"},
        "de": {True: "Ja", False: "Nein"},
        "fr": {True: "Oui", False: "Non"},
    }
    INTRO = {
        "en": "These are your guesses, not their answers. Their answers stay private.",
        "de": "Das sind deine Tipps, nicht ihre Antworten. Ihre Antworten bleiben privat.",
        "fr": "Ce sont vos suppositions, pas leurs réponses. Leurs réponses restent privées.",
    }

    def _review_session(self, me, with_card=True, guess=True):
        from django.utils import timezone

        from crush_lu.models.crush_connect_cycle import ConnectWeekSession

        session = ConnectWeekSession.objects.create(user=me)
        session.open_weekly_review()
        if not with_card:
            return session, None
        target = _make_user(username=f"target-{me.username}", gender="F", premium=False)
        _mark_attended(target)
        questions = _set_gate_questions(target)
        card = ConnectCycleCard.objects.create(
            session=session,
            day_number=1,
            card_index=1,
            target_user=target,
            generated_date=timezone.localdate(),
            is_completed=True,
            completed_at=timezone.now(),
            answers_json={"guesses": {str(questions[0].pk): guess}},
        )
        return session, card

    def _member(self, name):
        me = _make_user(username=name, premium=False)
        _mark_attended(me)
        _login_eligible(self.client, me)
        return me

    def test_review_labels_the_viewers_guess(self):
        for lang in LANGS:
            for guess in (True, False):
                with self.subTest(lang=lang, guess=guess):
                    me = self._member(f"review-{lang}-{int(guess)}")
                    self._review_session(me, guess=guess)

                    response = self.client.get(f"/{lang}/crush-connect/week/review/")

                    self.assertEqual(response.status_code, 200)
                    body = _flat(response)
                    self.assertIn(
                        f"{self.GUESS[lang]} {self.YES_NO[lang][guess]}", body
                    )
                    self.assertIn(self.INTRO[lang], body)
                    self.assertNotIn("vous avez répondu", body)
                    # The wrong-way-round reading of a bare answer is gone.
                    self.assertNotRegex(
                        body,
                        r"&rdquo; — <span[^>]*> *"
                        + self.YES_NO[lang][guess]
                        + " *</span>",
                    )

    def test_negative_control_review_without_cards_has_no_guess_note(self):
        me = self._member("review-empty")
        self._review_session(me, with_card=False)

        response = self.client.get("/en/crush-connect/week/review/")

        self.assertEqual(response.status_code, 200)
        body = _flat(response)
        self.assertNotIn("These are your guesses, not their answers.", body)
        self.assertNotIn("Your guess:", body)
        # The page itself still renders its header.
        self.assertIn("Your weekly review", body)

    def test_french_guess_privacy_line_no_longer_calls_a_guess_an_answer(self):
        self.assertEqual(
            _translated(
                "fr",
                "Your guesses stay private — they help you choose during the "
                "24-hour review.",
            ),
            "Vos suppositions restent privées — elles vous aident à choisir "
            "pendant le bilan de 24 heures.",
        )
