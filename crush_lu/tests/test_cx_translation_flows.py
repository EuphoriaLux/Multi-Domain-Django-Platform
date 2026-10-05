"""DE/FR copy on the registration, ticket, cancellation, reset and checkout screens.

CX review bundle P4-2. Every assertion here renders the real page (or reads
the real catalog) in German and French, because the failures were silent:

* FR "Back to Event" said "Retour a la connexion" (= Back to login) on the
  registration and ticket pages.
* The password-reset confirmation said "Auf Updates pruefen" / "Verifier les
  mises a jour" (= check for updates) where it meant "check your email", and
  the reset form labelled the email field "Ajouter une adresse e-mail".
* The curated-application confirmation, the post-cancel credit flash and the
  checkout-failure toast had no DE/FR entry at all, so they rendered in
  English.
* The "event already started" cancel refusal told members to contact a coach
  (a first-timer has none) and was untranslated.

The "no entry at all" group (``MISSING_ENTRIES`` below) was found by the
synthesis msgid scan, not by an adversarially verified finding. Each entry is
kept only because the DE/FR page was rendered before the entry was added and
showed English.

Paths are literal: ``reverse("crush_lu:...")`` builds ``/crush/...`` paths that
404 under ``HTTP_HOST=crush.lu`` because the middleware swaps the urlconf.

Run with: pytest crush_lu/tests/test_cx_translation_flows.py -v
"""

import html
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import polib
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.utils.translation import gettext, override

from crush_lu.models import (
    CrushProfile,
    EventInvitation,
    EventRegistration,
    MeetupEvent,
    UserDataConsent,
)
from crush_lu.services.sumup import SumUpError
from crush_lu.tests.test_crush_credit import CreditFixture

User = get_user_model()
HOST = "crush.lu"
LOCALE = Path(__file__).resolve().parent.parent / "locale"

# ---------------------------------------------------------------------------
# The catalog contract: msgid -> (German, French)
# ---------------------------------------------------------------------------

STARTED_MSGID = (
    "This event has already started, so it can't be cancelled online. "
    "If you can't make it, email support@crush.lu."
)
OLD_STARTED_MSGID = (
    "This event has already started. If you can't make it, contact your coach."
)
TAKEN_PLACE_MSGID = (
    "This event has already taken place. If something is wrong, contact your coach."
)
CREDIT_FLASH_MSGID = (
    "We've added €%(amount).2f in Crush Credit to your account — "
    "it's ready to use on any Crush.lu event."
)
CHECKOUT_ERROR_MSGID = (
    "Unable to initiate payment at the moment. Please try again later."
)
APPLICATION_SENTENCE_MSGID = (
    "The organiser team composes the group before the event. We'll let you "
    "know whether you have a place — you don't need to pay anything yet."
)
APPLICATION_FLASH_MSGID = (
    "Your application has been received. The organiser team composes the "
    "group before the event and will let you know whether you have a place."
)

CHOOSE_MSGID = "Choose from Speed Dating, 90-Second Presentations, or Mystery Event"

# Entries that already existed and were wrong (or English, or informal).
# msgid -> (German, French); None = the shipped German was already right.
FIXED_ENTRIES = {
    "Back to Event": (None, "Retour à l'événement"),
    "Anchor – stay here": (None, "Ancre – restez ici"),
    "Submit Answer": (None, "Envoyer la réponse"),
    "Check your email": ("Prüfe deine E-Mails", "Consultez votre boîte mail"),
    "Check Your Email:": ("Prüfe deine E-Mails:", "Consultez votre boîte mail :"),
    "Email address": ("E-Mail-Adresse", "Adresse e-mail"),
    "Choose a new password": (None, "Choisissez un nouveau mot de passe"),
    "Choose a new password - Crush.lu": (
        None,
        "Choisissez un nouveau mot de passe - Crush.lu",
    ),
    TAKEN_PLACE_MSGID: (
        (
            "Das Event hat bereits stattgefunden. Wenn etwas nicht stimmt, wende "
            "dich an deinen Coach."
        ),
        "L'événement a déjà eu lieu. En cas de problème, contactez votre coach.",
    ),
    STARTED_MSGID: (
        (
            "Das Event hat schon begonnen und kann online nicht mehr storniert "
            "werden. Wenn du nicht kommen kannst, schreib an support@crush.lu."
        ),
        (
            "L'événement a déjà commencé : il ne peut plus être annulé en ligne. "
            "Si vous ne pouvez pas venir, écrivez à support@crush.lu."
        ),
    ),
    CHOOSE_MSGID: (
        (
            "Wähle zwischen Speed Dating, 90-Sekunden-Präsentationen oder einem "
            "Mystery Event"
        ),
        (
            "Choisissez entre Speed Dating, Présentations de 90 secondes, ou "
            "Événement Mystère"
        ),
    ),
    "Interactive Activity Voting:": (None, "Vote interactif sur l'activité :"),
}

# Strings that had no .po entry in either language.
MISSING_ENTRIES = {
    "Application Received!": ("Bewerbung eingegangen!", "Candidature reçue !"),
    APPLICATION_SENTENCE_MSGID: (
        (
            "Das Organisationsteam stellt die Gruppe vor dem Event zusammen. Wir "
            "sagen dir Bescheid, ob du einen Platz hast – du musst noch nichts "
            "bezahlen."
        ),
        (
            "L'équipe organisatrice compose le groupe avant l'événement. Nous "
            "vous dirons si vous avez une place — vous n'avez encore rien à payer."
        ),
    ),
    "Your application is in!": (
        "Deine Bewerbung ist eingegangen!",
        "Votre candidature est bien enregistrée !",
    ),
    APPLICATION_FLASH_MSGID: (
        (
            "Deine Bewerbung ist eingegangen. Das Organisationsteam stellt die "
            "Gruppe vor dem Event zusammen und sagt dir Bescheid, ob du einen "
            "Platz hast."
        ),
        (
            "Votre candidature a bien été reçue. L'équipe organisatrice compose "
            "le groupe avant l'événement et vous dira si vous avez une place."
        ),
    ),
    CREDIT_FLASH_MSGID: (
        (
            "Wir haben deinem Konto %(amount).2f EUR als Crush Credit "
            "gutgeschrieben – du kannst es sofort für jedes Crush.lu-Event "
            "einsetzen."
        ),
        (
            "Nous avons ajouté %(amount).2f EUR de Crush Credit à votre compte — "
            "utilisable immédiatement pour tout événement Crush.lu."
        ),
    ),
    CHECKOUT_ERROR_MSGID: (
        (
            "Die Zahlung konnte gerade nicht gestartet werden. Bitte versuche es "
            "später noch einmal."
        ),
        (
            "Impossible de lancer le paiement pour le moment. Veuillez "
            "réessayer plus tard."
        ),
    ),
}

# The wrong values that used to ship, which must be gone: (lang, msgid) -> text.
OLD_WRONG = {
    ("fr", "Back to Event"): "Retour à la connexion",
    ("fr", "Anchor – stay here"): "Ancre – reste ici",
    ("fr", "Submit Answer"): "Soumis",
    ("de", "Check your email"): "Auf Updates prüfen",
    ("fr", "Check your email"): "Vérifier les mises à jour",
    ("de", "Check Your Email:"): "Auf Updates prüfen",
    ("fr", "Check Your Email:"): "Vérifier les mises à jour",
    ("de", "Email address"): "E-Mail-Adresse *",
    ("fr", "Email address"): "Ajouter une adresse e-mail",
    ("fr", "Choose a new password"): "Choisis un nouveau mot de passe",
    ("fr", "Choose a new password - Crush.lu"): (
        "Choisis un nouveau mot de passe - Crush.lu"
    ),
    ("fr", "Interactive Activity Voting:"): "Sélection interactive de parcelle",
    ("de", CHOOSE_MSGID): CHOOSE_MSGID,
    ("fr", CHOOSE_MSGID): (
        "Choisis entre Speed Dating, Présentations de 90 secondes, ou "
        "Événement Mystère"
    ),
}

_PLACEHOLDER = re.compile(r"%\([A-Za-z_]+\)[#0\- +]*[\d.]*[sdfi]|%[sd]|\{[A-Za-z_]+\}")
# FR must use "vous" (see AGENTS.md "FR is not uniformly vous"); the Python
# regex, not grep, is the safe way to look for the informal forms.
_FR_INFORMAL = re.compile(r"\b(tu|ton|tes|toi)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _text(response):
    """Rendered page with HTML entities decoded (``'`` renders as ``&#x27;``)."""
    return html.unescape(response.content.decode())


def _flat(fragment):
    """Collapse tags and whitespace so a label can be compared as plain text."""
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _member(email, gender="F"):
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        first_name=email.split("@")[0].title(),
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={"verified": True, "primary": True}
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 1, 1),
        gender=gender,
        location="Luxembourg",
        is_approved=True,
        verification_status="verified",
        completion_status="step4",
    )
    return user


def _event(title="Translation Event", *, start=None, **extra):
    start = start or timezone.now() + timedelta(days=7)
    defaults = {
        "title": title,
        "description": "CX translation flows",
        "event_type": "mixer",
        "date_time": start,
        "duration_minutes": 120,
        "location": "Luxembourg",
        "address": "1 Test Street",
        "max_participants": 20,
        "registration_deadline": start - timedelta(hours=1),
        "is_published": True,
        "profile_requirement": "none",
    }
    defaults.update(extra)
    return MeetupEvent.objects.create(**defaults)


def _po(lang):
    return polib.pofile(str(LOCALE / lang / "LC_MESSAGES" / "django.po"))


# ---------------------------------------------------------------------------
# 1. The catalog itself
# ---------------------------------------------------------------------------


class CatalogContractTests(TestCase):
    """Every msgid of the bundle: .po entry, compiled catalog, placeholders."""

    @classmethod
    def setUpTestData(cls):
        cls.po = {"de": _po("de"), "fr": _po("fr")}
        cls.expected = {**FIXED_ENTRIES, **MISSING_ENTRIES}

    def _expected_msgstr(self, lang, msgid):
        # None means the shipped German was already right and is left alone;
        # the tests below only require it to be present and unflagged.
        pair = self.expected[msgid]
        return pair[0] if lang == "de" else pair[1]

    def test_every_entry_exists_unflagged_with_the_agreed_text(self):
        for lang in ("de", "fr"):
            for msgid in self.expected:
                want = self._expected_msgstr(lang, msgid)
                entry = self.po[lang].find(msgid)
                self.assertIsNotNone(entry, f"{lang}: no .po entry for {msgid!r}")
                self.assertNotIn("fuzzy", entry.flags, f"{lang}: {msgid!r} is fuzzy")
                self.assertFalse(
                    entry.previous_msgid, f"{lang}: {msgid!r} keeps a #| line"
                )
                self.assertTrue(entry.msgstr, f"{lang}: {msgid!r} is empty")
                if want is not None:
                    self.assertEqual(entry.msgstr, want, f"{lang}: {msgid!r}")

    def test_the_old_wrong_wording_is_gone(self):
        for (lang, msgid), wrong in OLD_WRONG.items():
            entry = self.po[lang].find(msgid)
            self.assertIsNotNone(entry, f"{lang}: no .po entry for {msgid!r}")
            self.assertNotEqual(entry.msgstr, wrong, f"{lang}: {msgid!r} still wrong")

    def test_the_replaced_english_msgid_is_gone_from_both_catalogs(self):
        for lang in ("de", "fr"):
            self.assertIsNone(
                self.po[lang].find(OLD_STARTED_MSGID),
                f"{lang}: the old 'contact your coach' msgid is still in the .po",
            )

    def test_placeholders_match_the_msgid_exactly(self):
        for lang in ("de", "fr"):
            for msgid in self.expected:
                entry = self.po[lang].find(msgid)
                self.assertEqual(
                    sorted(_PLACEHOLDER.findall(entry.msgstr)),
                    sorted(_PLACEHOLDER.findall(msgid)),
                    f"{lang}: placeholders differ for {msgid!r}",
                )

    def test_french_msgstr_uses_vous_not_tu(self):
        for msgid in self.expected:
            msgstr = self.po["fr"].find(msgid).msgstr
            self.assertIsNone(
                _FR_INFORMAL.search(msgstr), f"informal French in {msgid!r}: {msgstr!r}"
            )

    def test_the_compiled_catalog_serves_exactly_the_po_text(self):
        """gettext() reads the .mo; a stale or mismatched build falls back to English."""
        for lang in ("de", "fr"):
            with override(lang):
                for msgid in self.expected:
                    self.assertEqual(
                        gettext(msgid),
                        self.po[lang].find(msgid).msgstr,
                        f"{lang}: compiled catalog differs from .po for {msgid!r}",
                    )

    def test_the_credit_flash_still_formats_with_its_percent_placeholder(self):
        for lang in ("de", "fr"):
            with override(lang):
                text = gettext(CREDIT_FLASH_MSGID) % {"amount": 15.5}
            self.assertIn("15.50 EUR", text)
            self.assertNotIn("%(", text)


# ---------------------------------------------------------------------------
# 2. QN-18 — "Back to Event" on the registration and ticket pages
# ---------------------------------------------------------------------------


class BackToEventLinkTests(TestCase):
    LABELS: ClassVar[dict[str, str]] = {
        "en": "Back to Event",
        "de": "Zurück zum Event",
        "fr": "Retour à l'événement",
    }

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        self.event = _event()
        self.holder = _member("holder@example.com")
        EventRegistration.objects.create(
            event=self.event, user=self.holder, status="confirmed"
        )
        self.newcomer = _member("newcomer@example.com", gender="M")

    def _links_to_event(self, path, lang):
        text = _text(self.client.get(path))
        pattern = rf'<a[^>]+href="/{lang}/events/{self.event.id}/"[^>]*>(.*?)</a>'
        return [_flat(m) for m in re.findall(pattern, text, flags=re.DOTALL)]

    def test_register_and_ticket_pages_link_back_to_the_event_not_to_login(self):
        for lang, label in self.LABELS.items():
            with self.subTest(page="register", lang=lang):
                self.client.force_login(self.newcomer)
                links = self._links_to_event(
                    f"/{lang}/events/{self.event.id}/register/", lang
                )
                self.assertIn(label, links)
                self.assertNotIn("Retour à la connexion", links)
            with self.subTest(page="ticket", lang=lang):
                self.client.force_login(self.holder)
                links = self._links_to_event(
                    f"/{lang}/events/{self.event.id}/ticket/", lang
                )
                self.assertIn(label, links)
                self.assertNotIn("Retour à la connexion", links)

    def test_back_to_login_keeps_its_own_french_wording(self):
        """Negative control: only the *Event* msgid changed, 'Back to login' did not."""
        response = self.client.get(
            "/accounts/password/reset/", headers={"Accept-Language": "fr"}
        )
        self.assertContains(response, "Retour à la connexion")


# ---------------------------------------------------------------------------
# 3. XC-16 — the password-reset flow in DE/FR
# ---------------------------------------------------------------------------


class PasswordResetFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        mail.outbox = []
        self.user = User.objects.create_user(
            username="resetflow@example.com",
            email="resetflow@example.com",
            password="OldPassword123!",
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )

    def _get(self, path, lang, **extra):
        return self.client.get(path, headers={"Accept-Language": lang}, **extra)

    @staticmethod
    def _email_label(page):
        match = re.search(
            r'<label[^>]*for="id_email"[^>]*>(.*?)</label>', page, flags=re.DOTALL
        )
        return _flat(match.group(1)) if match else None

    @staticmethod
    def _heading(page):
        match = re.search(r"<h1[^>]*>(.*?)</h1>", page, flags=re.DOTALL)
        return _flat(match.group(1)) if match else None

    def test_request_form_labels_the_field_as_an_email_address(self):
        expected = {
            "en": "Email address",
            "de": "E-Mail-Adresse",
            "fr": "Adresse e-mail",
        }
        for lang, label in expected.items():
            with self.subTest(lang=lang):
                page = _text(self._get("/accounts/password/reset/", lang))
                self.assertEqual(self._email_label(page), label)
        self.assertNotIn(
            "Ajouter une adresse e-mail",
            _text(self._get("/accounts/password/reset/", "fr")),
        )

    def test_done_page_says_check_your_email_not_check_for_updates(self):
        expected = {
            "en": "Check your email",
            "de": "Prüfe deine E-Mails",
            "fr": "Consultez votre boîte mail",
        }
        for lang, heading in expected.items():
            with self.subTest(lang=lang):
                cache.clear()
                response = self.client.post(
                    "/accounts/password/reset/",
                    {"email": self.user.email},
                    headers={"Accept-Language": lang},
                    follow=True,
                )
                page = _text(response)
                self.assertEqual(self._heading(page), heading)
                self.assertNotIn("Auf Updates prüfen", page)
                self.assertNotIn("Vérifier les mises à jour", page)

    def test_choose_a_new_password_page_is_formal_in_french(self):
        self.client.post("/accounts/password/reset/", {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body or ""
        for alt in getattr(mail.outbox[0], "alternatives", []) or []:
            body += alt[0]
        match = re.search(r"(/accounts/password/reset/key/[^\s\"<>]+)", body)
        self.assertIsNotNone(match, "reset link missing from the email")

        expected = {
            "en": "Choose a new password",
            "de": "Neues Passwort wählen",
            "fr": "Choisissez un nouveau mot de passe",
        }
        for lang, heading in expected.items():
            with self.subTest(lang=lang):
                response = self.client.get(
                    match.group(1), headers={"Accept-Language": lang}, follow=True
                )
                page = _text(response)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self._heading(page), heading)
                self.assertIn(f"<title>{heading} - Crush.lu", page.replace("–", "-"))
                self.assertNotIn("Choisis un nouveau", page)

    def test_the_reset_form_is_still_the_same_form(self):
        """Negative control: copy changed, the form and its button did not."""
        page = _text(self._get("/accounts/password/reset/", "fr"))
        self.assertIn('id="id_email"', page)
        self.assertIn('name="email"', page)
        self.assertIn('type="submit"', page)


class InvitationPendingApprovalCopyTests(TestCase):
    """XC-16 (verifier addition): the same wrong msgstr on a second page."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        coach_user = _member("invitercoach@example.com")
        self.event = _event(
            "Private VIP Event",
            is_private_invitation=True,
            invitation_code="vip2024",
            invitation_expires_at=timezone.now() + timedelta(days=30),
        )
        self.invitation = EventInvitation.objects.create(
            event=self.event,
            guest_email="guest@example.com",
            guest_first_name="John",
            guest_last_name="Doe",
            invited_by=coach_user,
            status="pending",
            approval_status="pending_approval",
        )

    def _accept(self, lang):
        return self.client.post(
            f"/{lang}/invite/{self.invitation.invitation_code}/accept/",
            {"date_of_birth": "1995-05-15", "agree_to_terms": "on"},
        )

    def test_check_your_email_label(self):
        expected = {
            "en": "Check Your Email:",
            "de": "Prüfe deine E-Mails:",
            "fr": "Consultez votre boîte mail :",
        }
        for lang, label in expected.items():
            with self.subTest(lang=lang):
                self.client = Client(HTTP_HOST=HOST)
                self.invitation.created_user = None
                self.invitation.status = "pending"
                self.invitation.save()
                User.objects.filter(email="guest@example.com").delete()
                response = self._accept(lang)
                self.assertTemplateUsed(
                    response, "crush_lu/invitation_pending_approval.html"
                )
                page = _text(response)
                self.assertIn(f"<strong>{label}</strong>", page)
                self.assertNotIn("Auf Updates prüfen", page)
                self.assertNotIn("Vérifier les mises à jour", page)


# ---------------------------------------------------------------------------
# 4. SD-14 — cancelling once the event has started or ended
# ---------------------------------------------------------------------------


@patch("crush_lu.views_events.send_event_cancellation_confirmation")
class CancelRefusalTests(TestCase):
    STARTED: ClassVar[dict[str, tuple[str, str]]] = {
        "en": ("so it can't be cancelled online", "email support@crush.lu"),
        "de": (
            "kann online nicht mehr storniert werden",
            "schreib an support@crush.lu",
        ),
        "fr": ("il ne peut plus être annulé en ligne", "écrivez à support@crush.lu"),
    }
    TAKEN_PLACE: ClassVar[dict[str, str]] = {
        "en": "This event has already taken place.",
        "de": "Das Event hat bereits stattgefunden.",
        "fr": "L'événement a déjà eu lieu.",
    }

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        self.user = _member("canceller@example.com")
        self.client.force_login(self.user)

    def _registration(self, event):
        return EventRegistration.objects.create(
            event=event, user=self.user, status="confirmed"
        )

    def _cancel(self, event, lang):
        return self.client.post(f"/{lang}/events/{event.id}/cancel/", follow=True)

    def test_started_event_refusal_in_every_language(self, _mail):
        event = _event(start=timezone.now() - timedelta(minutes=10))
        registration = self._registration(event)
        for lang, (first, second) in self.STARTED.items():
            with self.subTest(lang=lang):
                page = _text(self._cancel(event, lang))
                self.assertIn(first, page)
                self.assertIn(second, page)
                self.assertNotIn("contact your coach", page)
        registration.refresh_from_db()
        self.assertEqual(registration.status, "confirmed")

    def test_started_event_refusal_is_the_new_english_sentence(self, _mail):
        event = _event(start=timezone.now() - timedelta(minutes=10))
        self._registration(event)
        page = _text(self._cancel(event, "en"))
        self.assertIn(STARTED_MSGID, page)

    def test_ended_event_refusal_is_translated(self, _mail):
        event = _event(start=timezone.now() - timedelta(hours=5))
        registration = self._registration(event)
        for lang, sentence in self.TAKEN_PLACE.items():
            with self.subTest(lang=lang):
                self.assertIn(sentence, _text(self._cancel(event, lang)))
        registration.refresh_from_db()
        self.assertEqual(registration.status, "confirmed")

    def test_an_upcoming_event_is_still_cancellable(self, _mail):
        """Negative control: the refusal copy must not leak into the happy path."""
        event = _event(start=timezone.now() + timedelta(days=5))
        registration = self._registration(event)
        page = _text(self._cancel(event, "de"))
        registration.refresh_from_db()
        self.assertEqual(registration.status, "cancelled")
        self.assertNotIn("kann online nicht mehr storniert werden", page)
        self.assertNotIn("Das Event hat bereits stattgefunden", page)


# ---------------------------------------------------------------------------
# 5. SD-02 (translation slice) — the activity-voting box
# ---------------------------------------------------------------------------


class VotingBoxTranslationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        self.user = _member("voter@example.com")
        self.client.force_login(self.user)
        self.event = _event("Voting Night", event_type="speed_dating")
        MeetupEvent.objects.filter(pk=self.event.pk).update(enable_activity_voting=True)

    def _page(self, lang):
        return _text(self.client.get(f"/{lang}/events/{self.event.id}/"))

    def test_german_choice_line_is_translated(self):
        page = self._page("de")
        self.assertIn("Wähle zwischen Speed Dating", page)
        self.assertNotIn("Choose from Speed Dating", page)
        self.assertIn("Interaktive Aktivitätsabstimmung:", page)

    def test_french_box_is_formal_and_no_longer_a_plot_of_land(self):
        page = self._page("fr")
        self.assertIn("Vote interactif sur l'activité", page)
        self.assertIn("Choisissez entre Speed Dating", page)
        self.assertNotIn("parcelle", page)
        self.assertNotIn("Choisis entre", page)

    def test_the_box_stays_hidden_when_voting_is_off(self):
        """Negative control: the box is still gated on enable_activity_voting."""
        MeetupEvent.objects.filter(pk=self.event.pk).update(
            enable_activity_voting=False
        )
        for lang in ("en", "de", "fr"):
            with self.subTest(lang=lang):
                page = self._page(lang)
                self.assertNotIn("Interaktive Aktivitätsabstimmung:", page)
                self.assertNotIn("Vote interactif sur l'activité", page)
                self.assertNotIn("Interactive Activity Voting:", page)


# ---------------------------------------------------------------------------
# 6. S-01 group — strings that had no .po entry at all
# ---------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CuratedApplicationStringTests(TestCase):
    EXPECTED: ClassVar[dict[str, dict[str, str]]] = {
        "de": {
            "heading": "Bewerbung eingegangen!",
            "sentence": "Wir sagen dir Bescheid, ob du einen Platz hast",
            "flash": "Deine Bewerbung ist eingegangen. Das Organisationsteam",
            "banner": "Deine Bewerbung ist eingegangen!",
        },
        "fr": {
            "heading": "Candidature reçue !",
            "sentence": "Nous vous dirons si vous avez une place",
            "flash": "Votre candidature a bien été reçue.",
            "banner": "Votre candidature est bien enregistrée !",
        },
    }
    ENGLISH: ClassVar[tuple[str, ...]] = (
        "Application Received!",
        "We'll let you know whether you have a place",
        "Your application has been received.",
        "Your application is in!",
    )

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        self.event = _event(
            "Curated Round",
            event_type="speed_dating",
            registration_mode="curated",
            max_participants=2,
            registration_fee=0,
        )

    def _applicant(self, lang):
        cache.clear()
        user = _member(f"applicant-{lang}@example.com")
        self.client.force_login(user)
        return user

    def _payload(self):
        return {"preferred_age_min": "25", "preferred_age_max": "40"}

    def test_htmx_confirmation_card_is_translated(self):
        for lang, want in self.EXPECTED.items():
            with self.subTest(lang=lang):
                user = self._applicant(lang)
                response = self.client.post(
                    f"/{lang}/events/{self.event.id}/register/",
                    self._payload(),
                    headers={"HX-Request": "true"},
                )
                self.assertEqual(response.status_code, 200)
                page = _text(response)
                self.assertEqual(
                    EventRegistration.objects.get(event=self.event, user=user).status,
                    "applied",
                )
                self.assertIn(want["heading"], page)
                self.assertIn(want["sentence"], page)
                for english in self.ENGLISH[:2]:
                    self.assertNotIn(english, page)

    def test_full_page_flash_is_translated(self):
        for lang, want in self.EXPECTED.items():
            with self.subTest(lang=lang):
                self._applicant(lang)
                response = self.client.post(
                    f"/{lang}/events/{self.event.id}/register/",
                    self._payload(),
                    follow=True,
                )
                page = _text(response)
                self.assertIn(want["flash"], page)
                self.assertNotIn(self.ENGLISH[2], page)

    def test_event_page_banner_for_an_applicant_is_translated(self):
        for lang, want in self.EXPECTED.items():
            with self.subTest(lang=lang):
                user = self._applicant(lang)
                EventRegistration.objects.create(
                    event=self.event, user=user, status="applied"
                )
                page = _text(self.client.get(f"/{lang}/events/{self.event.id}/"))
                self.assertIn(want["banner"], page)
                self.assertNotIn(self.ENGLISH[3], page)

    def test_event_page_banner_is_still_english_in_english(self):
        user = self._applicant("en")
        EventRegistration.objects.create(event=self.event, user=user, status="applied")
        page = _text(self.client.get(f"/en/events/{self.event.id}/"))
        self.assertIn("Your application is in!", page)

    def test_a_seat_holder_does_not_see_the_application_banner(self):
        """Negative control: the banner is only for an *applied* registration."""
        user = self._applicant("seat")
        EventRegistration.objects.create(
            event=self.event, user=user, status="confirmed"
        )
        page = _text(self.client.get(f"/de/events/{self.event.id}/"))
        self.assertNotIn("Deine Bewerbung ist eingegangen!", page)
        self.assertNotIn("Your application is in!", page)

    def test_english_wording_is_unchanged(self):
        self._applicant("en")
        response = self.client.post(
            f"/en/events/{self.event.id}/register/",
            self._payload(),
            headers={"HX-Request": "true"},
        )
        page = _text(response)
        self.assertIn("Application Received!", page)
        self.assertIn("We'll let you know whether you have a place", page)

    def test_a_direct_event_confirmation_is_not_turned_into_an_application(self):
        """Negative control: a direct event keeps the seat wording, in German."""
        direct = _event("Direct Round", event_type="speed_dating", max_participants=5)
        self._applicant("direct")
        response = self.client.post(
            f"/de/events/{direct.id}/register/",
            self._payload(),
            headers={"HX-Request": "true"},
        )
        page = _text(response)
        self.assertNotIn("Bewerbung eingegangen!", page)
        self.assertNotIn("Candidature reçue", page)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CancelCreditFlashTests(CreditFixture):
    """The flash after a member cancels a paid seat (Crush Credit issued)."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def _cancel_in(self, lang):
        self.client.force_login(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                f"/{lang}/events/{self.event.pk}/cancel/", follow=True
            )

    def test_german_flash_is_translated_and_keeps_the_amount(self):
        page = _text(self._cancel_in("de"))
        self.assertIn("Wir haben deinem Konto 15.50 EUR als Crush Credit", page)
        self.assertIn("gutgeschrieben", page)
        self.assertNotIn("We've added", page)

    def test_french_flash_is_translated_and_keeps_the_amount(self):
        page = _text(self._cancel_in("fr"))
        self.assertIn("Nous avons ajouté 15.50 EUR de Crush Credit", page)
        self.assertNotIn("We've added", page)

    def test_english_flash_is_unchanged(self):
        page = _text(self._cancel_in("en"))
        self.assertIn("We've added €15.50 in Crush Credit to your account", page)

    def test_an_unpaid_cancellation_announces_no_credit(self):
        """Negative control: no credit, no credit sentence, in any language."""
        event = self._event(hours_away=100, max_participants=5)
        registration = self._registration(
            event, self._user("unpaid-flash@crush.lu"), status="confirmed"
        )
        self.client.force_login(registration.user)
        page = _text(self.client.post(f"/de/events/{event.pk}/cancel/", follow=True))
        self.assertNotIn("gutgeschrieben", page)
        self.assertNotIn("We've added", page)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CheckoutErrorTranslationTests(CreditFixture):
    """The checkout toast shown when SumUp could not be reached."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.owing_event = self._event(hours_away=100, max_participants=50)

    def _start_checkout(self, lang):
        """Open a card checkout for a seat still owing money; SumUp is down.

        A fresh member per call: an ambiguous failure deliberately keeps the
        durable creation claim, so a second POST for the same seat would
        answer 409 instead of reaching the error under test.
        """
        owing = self._registration(
            self.owing_event, self._user(f"owing-{lang}@crush.lu"), status="pending"
        )
        self.client.force_login(owing.user)
        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout",
            side_effect=SumUpError("transport failure"),
        ):
            return self.client.post(
                f"/payments/sumup/create-event-checkout/{owing.pk}/",
                data=json.dumps({"payment_method": "card"}),
                content_type="application/json",
                headers={"Accept-Language": lang},
            )

    def test_error_is_translated(self):
        expected = {
            "de": "Die Zahlung konnte gerade nicht gestartet werden. "
            "Bitte versuche es später noch einmal.",
            "fr": "Impossible de lancer le paiement pour le moment. "
            "Veuillez réessayer plus tard.",
        }
        for lang, message in expected.items():
            with self.subTest(lang=lang):
                response = self._start_checkout(lang)
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json()["error"], message)

    def test_english_error_is_unchanged(self):
        response = self._start_checkout("en")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], CHECKOUT_ERROR_MSGID)

    def test_a_working_checkout_is_untouched_by_the_translation(self):
        """Negative control: when SumUp answers, no error text is involved."""
        owing = self._registration(
            self.owing_event, self._user("owing-ok@crush.lu"), status="pending"
        )
        self.client.force_login(owing.user)
        with patch(
            "crush_lu.views_payments.SumUpClient.create_checkout",
            return_value={"id": "CHK_OK", "status": "PENDING"},
        ):
            response = self.client.post(
                f"/payments/sumup/create-event-checkout/{owing.pk}/",
                data=json.dumps({"payment_method": "card"}),
                content_type="application/json",
                headers={"Accept-Language": "de"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checkout_id"], "CHK_OK")
        self.assertNotIn("error", response.json())
