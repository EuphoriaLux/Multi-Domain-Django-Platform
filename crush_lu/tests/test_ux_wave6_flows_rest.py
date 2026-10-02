"""UX Wave 6 · WP5 flows-rest (#1146, #1116, #1058, connect buttons).

#1146 (owner decision D): the coach share and the same-gender auto-share get
the same email/phone opt-ins as members (chosen by each member on the shared
connection page), the consent list no longer names the member themself, and
rows shared before a contact choice existed say so instead of "chose not to".

Paths are literal (``reverse("crush_lu:...")`` 404s under HTTP_HOST=crush.lu).
"""

import re
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.core.cache import cache
from django.template import Context, Template
from django.test import Client, TestCase
from django.utils import translation

from crush_lu.models import CrushProfile, EventConnection, UserDataConsent
from crush_lu.tests.test_event_lobby import (
    _attend,
    _end_event,
    _make_event,
    _make_member,
)

HOST = "crush.lu"
PHONE_ME = "+352691111111"
PHONE_OTHER = "+352692222222"
TEMPLATES = Path(__file__).resolve().parents[1] / "templates" / "crush_lu"
PRE_OPT_IN = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
PRE_OPT_IN_COPY = "Shared before contact choices were available"


class ContactChoicesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.me = _make_member("wp6_me", gender="F", membership=False)
        self.other = _make_member("wp6_other", gender="M", membership=False)
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

    def _url(self, lang="en"):
        return f"/{lang}/connections/{self.conn.id}/"

    def _share(self, **fields):
        # What the coach crush_share and the same-gender auto-share leave
        # behind: both consents, status shared, no contact opt-in.
        self.conn.status = "shared"
        self.conn.requester_consents_to_share = True
        self.conn.recipient_consents_to_share = True
        for key, value in fields.items():
            setattr(self.conn, key, value)
        self.conn.save()

    def _choose(self, user, **data):
        return self._client(user).post(
            self._url(), {"contact_choices": "1", **data}, HTTP_HOST=HOST
        )

    def test_shared_page_offers_own_unticked_contact_choices(self):
        self._share()
        html = self._client(self.me).get(self._url(), HTTP_HOST=HOST).content.decode()
        form = re.search(r'data-testid="contact-choices".*?</form>', html, re.S)
        self.assertIsNotNone(form)
        self.assertIn('name="share_email"', form.group(0))
        self.assertIn('name="share_phone"', form.group(0))
        self.assertNotIn("checked", form.group(0))
        self.assertIn("Also share my phone number with", form.group(0))

    def test_member_opts_in_on_the_shared_page(self):
        self._share()
        self._choose(self.me, share_email="on", share_phone="on")
        self.conn.refresh_from_db()
        self.assertTrue(self.conn.requester_shares_email)
        self.assertTrue(self.conn.requester_shares_phone)
        self.assertFalse(self.conn.recipient_shares_email)
        self.assertFalse(self.conn.recipient_shares_phone)
        entry = self.conn.system_actions[-1]
        self.assertEqual(entry["type"], "contact_choices")
        self.assertEqual(entry["details"], {"side": "requester"})
        seen = self._client(self.other).get(self._url(), HTTP_HOST=HOST)
        self.assertContains(seen, f"tel:{PHONE_ME}")
        self.assertContains(seen, f"mailto:{self.me.email}")
        # The choice form shows the saved state back to its owner.
        mine = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertContains(mine, 'name="share_phone" checked')

    def test_choices_are_ignored_before_the_introduction(self):
        response = self._choose(self.me, share_email="on", share_phone="on")
        self.assertEqual(response.status_code, 302)
        self.conn.refresh_from_db()
        self.assertFalse(self.conn.requester_shares_email)
        self.assertFalse(self.conn.requester_shares_phone)

    def test_same_gender_auto_share_members_can_opt_in(self):
        CrushProfile.objects.filter(user=self.other).update(gender="F")
        self.conn.status = "pending"
        self.conn.save()
        self._client(self.other).post(
            f"/en/connections/{self.conn.id}/accept/", HTTP_HOST=HOST
        )
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.status, "shared")
        self._choose(self.other, share_phone="on")
        self.conn.refresh_from_db()
        self.assertTrue(self.conn.recipient_shares_phone)
        self.assertFalse(self.conn.recipient_shares_email)
        seen = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertContains(seen, f"tel:{PHONE_OTHER}")

    def test_coach_shared_crush_lead_members_can_opt_in(self):
        self._share(flow=EventConnection.FLOW_CRUSH)
        self._choose(self.other, share_email="on")
        self.conn.refresh_from_db()
        self.assertTrue(self.conn.recipient_shares_email)
        seen = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertContains(seen, f"mailto:{self.other.email}")

    def test_consent_list_does_not_name_the_member(self):
        html = self._client(self.me).get(self._url(), HTTP_HOST=HOST).content.decode()
        block = html.split("What will be shared:", 1)[1].split("</ul>", 1)[0]
        self.assertIn("Profile details and photos", block)
        name = CrushProfile.objects.get(user=self.me).display_name
        self.assertNotIn(name, block)
        self.assertNotIn("First name only:", block)
        self.assertNotIn("Full name:", block)

    def test_pre_opt_in_rows_say_so_instead_of_chose_not_to(self):
        self._share(shared_at=PRE_OPT_IN)
        en = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertContains(en, PRE_OPT_IN_COPY, count=2)
        self.assertNotContains(en, "chose not to share their email")
        self.assertNotContains(en, "did not share their phone number")
        de = self._client(self.me).get(self._url("de"), HTTP_HOST=HOST)
        self.assertContains(de, "Geteilt, bevor die Kontaktauswahl verfügbar war")
        fr = self._client(self.me).get(self._url("fr"), HTTP_HOST=HOST)
        self.assertContains(
            fr, "Partagé avant que le choix des coordonnées soit disponible"
        )

    def test_pre_opt_in_copy_ends_once_the_member_has_chosen(self):
        self._share(shared_at=PRE_OPT_IN)
        self._choose(self.other)  # saved with nothing ticked
        en = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertNotContains(en, PRE_OPT_IN_COPY)
        self.assertContains(en, "did not share their phone number")

    def test_rows_shared_after_the_opt_in_keep_the_choice_copy(self):
        self._share()
        en = self._client(self.me).get(self._url(), HTTP_HOST=HOST)
        self.assertNotContains(en, PRE_OPT_IN_COPY)
        self.assertContains(en, "did not share their phone number")


class OptInSinceCacheTests(TestCase):
    """A missing recorder row is not cached for the life of the process."""

    def setUp(self):
        cache.clear()
        from crush_lu import views_connections

        self.views = views_connections
        self.name = views_connections.EMAIL_OPT_IN_MIGRATION
        cache_dict = getattr(views_connections, "_OPT_IN_SINCE_CACHE", None)
        if cache_dict is not None:
            cache_dict.clear()
        clear = getattr(views_connections._opt_in_available_since, "cache_clear", None)
        if clear:
            clear()

    def test_missing_row_is_retried_once_it_exists(self):
        from django.db.migrations.recorder import MigrationRecorder

        rows = MigrationRecorder.Migration.objects.filter(
            app="crush_lu", name=self.name
        )
        applied = rows.values_list("applied", flat=True).first()
        rows.delete()
        with self.assertLogs("crush_lu.views_connections", level="WARNING"):
            self.assertIsNone(self.views._opt_in_available_since(self.name))
        MigrationRecorder.Migration.objects.create(
            app="crush_lu", name=self.name, applied=applied
        )
        self.assertEqual(self.views._opt_in_available_since(self.name), applied)


class PointsAvailablePluralTests(TestCase):
    """#1058: the open-text points aria-label is plural-aware."""

    def _label(self, points, lang):
        source = (TEMPLATES / "journey/challenges/open_text.html").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r'journey-points-display" aria-label="(\{% blocktrans count .*?'
            r"\{% endblocktrans %\})",
            source,
        )
        self.assertIsNotNone(match)
        with translation.override(lang):
            return Template("{% load i18n %}" + match.group(1)).render(
                Context({"challenge": type("C", (), {"points_awarded": points})})
            )

    def test_singular_and_plural(self):
        self.assertEqual(self._label(1, "en"), "1 point available")
        self.assertEqual(self._label(5, "en"), "5 points available")
        self.assertEqual(self._label(1, "de"), "1 Punkt verfügbar")
        self.assertEqual(self._label(5, "de"), "5 Punkte verfügbar")
        self.assertEqual(self._label(1, "fr"), "1 point disponible")
        self.assertEqual(self._label(5, "fr"), "5 points disponibles")


class CopyFailureToastTests(TestCase):
    """#1116: the copy-failure toast must not send members to the address
    bar, which drops the ?ref referral credit."""

    def test_toast_shows_the_share_url_and_stays(self):
        source = (TEMPLATES / "event_detail.html").read_text(encoding="utf-8")
        match = re.search(r"function notifyCopyFailed\(\) \{.*?\n    \}", source, re.S)
        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertNotIn("address bar", body)
        self.assertIn("Long-press or select it here instead:", body)
        self.assertIn("+ ' ' + shareUrl", body)
        self.assertIn("duration: 0", body)

    def test_new_copy_is_translated(self):
        msgid = "Could not copy the link. Long-press or select it here instead:"
        for lang, expected in (
            ("de", "Halte ihn hier gedrückt"),
            ("fr", "sélectionnez-le ici"),
        ):
            with translation.override(lang):
                self.assertIn(expected, translation.gettext(msgid))


class ConnectButtonVariantTests(TestCase):
    """STYLE.md: the gradient is for the page's single hero CTA; other
    primary actions use .btn-crush-solid."""

    def test_accept_buttons_are_solid(self):
        for name, label in (
            ("chat_detail.html", "Accept plan"),
            ("coach_pick.html", 'value="accept"'),
        ):
            source = (TEMPLATES / "crush_connect" / name).read_text(encoding="utf-8")
            line = next(line for line in source.splitlines() if label in line)
            self.assertIn("btn-crush-solid", line, name)
            self.assertNotIn("btn-crush-primary", line, name)
