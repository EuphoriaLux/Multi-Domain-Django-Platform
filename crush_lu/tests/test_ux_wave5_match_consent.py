"""UX Wave 5 · WP4 "match-consent" (finding R2, owner decision A).

The phone number is an opt-in at the match consent step, default OFF, exactly
like email: the counterpart sees it (page, tel: link, WhatsApp link, GDPR
export) only when its owner ticked the box. Existing rows are not backfilled,
so a phone that was never explicitly granted is not shared.

Paths are literal (``reverse("crush_lu:...")`` 404s under HTTP_HOST=crush.lu).
"""

from pathlib import Path

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.db import connection
from django.template import Context, Template
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import translation
from django.utils.translation import gettext

from crush_lu.models import CrushProfile, EventConnection, UserDataConsent
from crush_lu.tests.test_event_lobby import (
    _attend,
    _end_event,
    _make_event,
    _make_member,
)
from crush_lu.tests.test_ux_wave4_followups_a11y import _export

HOST = "crush.lu"
PHONE_ME = "+352691111111"
PHONE_OTHER = "+352692222222"


class MatchPhoneConsentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.me = _make_member("wp4_me", gender="F", membership=False)
        self.other = _make_member("wp4_other", gender="M", membership=False)
        CrushProfile.objects.filter(user=self.me).update(phone_number=PHONE_ME)
        CrushProfile.objects.filter(user=self.other).update(phone_number=PHONE_OTHER)
        for u in (self.me, self.other):
            UserDataConsent.objects.update_or_create(
                user=u, defaults={"crushlu_consent_given": True}
            )
        event = _end_event(_make_event())
        _attend(self.me, event)
        _attend(self.other, event)
        self.conn = EventConnection.objects.create(
            requester=self.me,
            recipient=self.other,
            event=event,
            flow=EventConnection.FLOW_LEGACY,
            status="coach_approved",
        )

    def _client(self, user):
        client = Client()
        client.force_login(user)
        return client

    def _url(self):
        return f"/en/connections/{self.conn.id}/"

    def _share(self, **flags):
        self.conn.status = "shared"
        self.conn.requester_consents_to_share = True
        self.conn.recipient_consents_to_share = True
        for key, value in flags.items():
            setattr(self.conn, key, value)
        self.conn.save()

    def test_consent_form_offers_unchecked_phone_checkbox(self):
        response = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertContains(response, 'name="share_phone"')
        self.assertNotContains(response, 'name="share_phone" checked')
        # The phone is no longer listed as unconditionally shared.
        self.assertNotContains(response, PHONE_ME)

    def test_consent_copy_does_not_claim_visible_name_is_withheld(self):
        # The page already shows the counterpart's name, age and city before
        # consent, so the prompt may only name what consent really unlocks.
        en = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertContains(en, "Before your profile details and photos are shared")
        self.assertContains(en, "share my profile details and photos with")
        self.assertNotContains(en, "Before your name and profile")
        self.assertNotContains(en, "share my name, profile details")
        de = self._client(self.me).get(
            f"/de/connections/{self.conn.id}/", HTTP_HOST=HOST
        )
        self.assertContains(de, "Bevor deine Profilangaben und deine Fotos")
        fr = self._client(self.me).get(
            f"/fr/connections/{self.conn.id}/", HTTP_HOST=HOST
        )
        self.assertContains(fr, "Avant que les détails de votre profil")

    def test_shared_state_without_contact_details_promises_no_contacts(self):
        # Both sides left email and phone unticked (or the row predates the
        # opt-ins): profile details are shared, no contact channel is.
        self._share()
        en = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertContains(en, "Profiles Shared")
        self.assertNotContains(en, "Contacts Shared")
        # Migrated rows never declined: the copy must not say they chose to.
        self.assertNotContains(en, "chose not to share their phone")
        de = self._client(self.me).get(
            f"/de/connections/{self.conn.id}/", HTTP_HOST=HOST
        )
        self.assertContains(de, "Profile geteilt")
        self.assertContains(de, "hat die Telefonnummer nicht geteilt")
        fr = self._client(self.me).get(
            f"/fr/connections/{self.conn.id}/", HTTP_HOST=HOST
        )
        self.assertContains(fr, "Profils partagés")
        self.assertContains(fr, "a pas partagé son numéro de téléphone")

    def test_shared_status_badge_is_translated_and_promises_no_contacts(self):
        # The My Connections card renders the badge from the canonical tag
        # map; its label must exist in every catalog, not fall back to English.
        self._share()
        template = Template(
            "{% load connection_status %}{% connection_status_badge c %}"
        )
        html = {}
        for lang in ("en", "de", "fr"):
            with translation.override(lang):
                html[lang] = template.render(Context({"c": self.conn}))
        self.assertIn("Profiles Shared!", html["en"])
        self.assertIn("Profile geteilt!", html["de"])
        self.assertIn("Profils partagés !", html["fr"])
        self.assertNotIn("Contacts", html["en"])

    def test_same_gender_accept_messages_do_not_claim_contact_info_shared(self):
        # Both accept paths (HTMX toast + non-HTMX message) share one msgid.
        source = (
            Path(__file__).resolve().parents[1] / "views_connections.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Contact info is now shared", source)
        message = "Connection accepted! You can now see each other's profile details."
        self.assertEqual(source.count(message), 2)
        translated = {}
        for lang in ("en", "de", "fr"):
            with translation.override(lang):
                translated[lang] = gettext(message)
        self.assertEqual(translated["en"], message)
        self.assertIn("Profilangaben", translated["de"])
        self.assertIn("détails du profil", translated["fr"])

    def test_consent_without_phone_checkbox_does_not_share_phone(self):
        response = self._client(self.me).post(
            self._url(), {"consent": "yes"}, HTTP_HOST=HOST
        )
        self.assertEqual(response.status_code, 302)
        self.conn.refresh_from_db()
        self.assertTrue(self.conn.requester_consents_to_share)
        self.assertFalse(self.conn.requester_shares_phone)

    def test_consent_with_phone_checkbox_records_choice_on_own_side(self):
        self._client(self.other).post(
            self._url(), {"consent": "yes", "share_phone": "on"}, HTTP_HOST=HOST
        )
        self.conn.refresh_from_db()
        self.assertTrue(self.conn.recipient_shares_phone)
        self.assertFalse(self.conn.requester_shares_phone)

    def test_counterpart_cannot_see_phone_by_default(self):
        self._share()
        response = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, PHONE_OTHER)
        self.assertNotContains(response, "tel:")
        self.assertNotContains(response, "wa.me")
        self.assertContains(response, "did not share their phone number")

    def test_counterpart_sees_phone_after_opt_in(self):
        self._share(recipient_shares_phone=True)
        response = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertContains(response, PHONE_OTHER)
        self.assertContains(response, f"tel:{PHONE_OTHER}")
        self.assertContains(response, "wa.me/+352692222222")

    def test_opt_in_is_per_side(self):
        # Only the requester opted in: the requester's own view of the
        # recipient stays private, and the recipient sees the requester's.
        self._share(requester_shares_phone=True)
        mine = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        theirs = self._client(self.other).get(self._url(), HTTP_HOST=HOST)
        self.assertNotContains(mine, PHONE_OTHER)
        self.assertContains(theirs, PHONE_ME)

    def test_export_hides_phone_by_default_and_reports_own_choice(self):
        self._share(requester_shares_phone=True)
        mine = _export(self.me)["connections"][0]
        theirs = _export(self.other)["connections"][0]
        # The recipient did not share: the requester's export has no phone.
        self.assertIsNone(mine["connected_with_phone"])
        self.assertTrue(mine["you_shared_phone"])
        # The requester did: the recipient's export names it.
        self.assertEqual(theirs["connected_with_phone"], PHONE_ME)
        self.assertFalse(theirs["you_shared_phone"])

    def test_export_query_count_does_not_grow_with_connections(self):
        self._share(recipient_shares_phone=True)
        _export(self.me)  # warm one-off caches (consent row, content types)
        with CaptureQueriesContext(connection) as one:
            _export(self.me)
        for i in range(3):
            extra = _make_member(f"wp4_extra{i}", gender="M", membership=False)
            CrushProfile.objects.filter(user=extra).update(
                phone_number=f"+35269333333{i}"
            )
            EventConnection.objects.create(
                requester=self.me,
                recipient=extra,
                event=self.conn.event,
                flow=EventConnection.FLOW_LEGACY,
                status="shared",
                requester_consents_to_share=True,
                recipient_consents_to_share=True,
                recipient_shares_phone=True,
            )
        with CaptureQueriesContext(connection) as many:
            exported = _export(self.me)
        self.assertEqual(len(exported["connections"]), 4)
        self.assertEqual(len(many), len(one))

    def test_export_hides_phone_before_shared(self):
        self.conn.recipient_shares_phone = True
        self.conn.save()
        self.assertIsNone(_export(self.me)["connections"][0]["connected_with_phone"])

    def test_existing_rows_default_to_not_shared(self):
        # Rows created without the new fields (the pre-migration state) get
        # False, never a silent share.
        self.assertFalse(self.conn.requester_shares_phone)
        self.assertFalse(self.conn.recipient_shares_phone)

    def test_consent_flash_names_details_not_contact_information(self):
        self.conn.recipient_consents_to_share = True
        self.conn.save()
        response = self._client(self.me).post(
            self._url(), {"consent": "yes"}, HTTP_HOST=HOST
        )
        flashed = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(
            flashed, ["The details you each chose to share are now visible!"]
        )

    def test_same_gender_auto_share_copy_and_phone_stay_private(self):
        # The auto-share path never shows the consent form, so no phone is
        # offered or shared; the copy must not promise contact info.
        CrushProfile.objects.filter(user=self.other).update(gender="F")
        self.conn.status = "pending"
        self.conn.save()
        response = self._client(self.other).post(
            f"/en/connections/{self.conn.id}/accept/",
            HTTP_HOST=HOST,
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=f"connection-{self.conn.id}",
        )
        self.assertEqual(response.status_code, 200)
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.status, "shared")
        self.assertFalse(self.conn.requester_shares_phone)
        self.assertFalse(self.conn.recipient_shares_phone)
        self.assertContains(response, "each other's profile details")
        self.assertNotContains(response, "contact info")
