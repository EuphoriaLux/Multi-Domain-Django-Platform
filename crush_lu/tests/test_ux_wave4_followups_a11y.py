"""UX Wave 4 · WP13b "followups-a11y-contrast" (issues #1077, #1088).

* Decision I: the GDPR export names a counterpart's email only once that
  member shared it (connection shared + they ticked email at consent), the
  same rule connection_detail.html shows it by.
* Decision I: the legacy ``.text-muted`` class maps to the ``--text-muted``
  token (via the ``text-muted-fg`` utility) instead of gray-500.
* themeToggle's status / aria strings are rendered translated as
  ``data-label-*`` attributes.
* 6-03 (decision J): each Crush Connect landing card carries a one-line
  subline (the experience pages' approved tagline).

Paths are literal: ``reverse("crush_lu:...")`` builds ``/crush/...`` paths
that 404 under ``HTTP_HOST=crush.lu``.
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from crush_lu.models import EventConnection, UserDataConsent
from crush_lu.tests.test_event_lobby import (
    _attend,
    _end_event,
    _make_event,
    _make_member,
)

HOST = "crush.lu"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _export(user):
    client = Client()
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    client.force_login(user)
    response = client.get("/en/account/gdpr/export/", HTTP_HOST=HOST)
    assert response.status_code == 200, response.status_code
    return json.loads(response.content)


class GdprExportCounterpartEmailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.me = _make_member("wp13b_me", gender="F", membership=False)
        self.other = _make_member("wp13b_other", gender="M", membership=False)
        event = _end_event(_make_event())
        _attend(self.me, event)
        _attend(self.other, event)
        self.conn = EventConnection.objects.create(
            requester=self.me,
            recipient=self.other,
            event=event,
            flow=EventConnection.FLOW_LEGACY,
            status="accepted",
        )

    def _connected_with(self, user):
        rows = _export(user).get("connections", [])
        self.assertEqual(len(rows), 1)
        return rows[0]["connected_with"]

    def test_unanswered_counterpart_email_is_hidden(self):
        # The other member has not reached the consent step: nothing shared.
        self.assertIsNone(self._connected_with(self.me))
        self.assertIsNone(self._connected_with(self.other))

    def test_email_ticked_but_connection_not_shared_is_hidden(self):
        self.conn.recipient_consents_to_share = True
        self.conn.recipient_shares_email = True
        self.conn.save()
        self.assertIsNone(self._connected_with(self.me))

    def test_shared_without_email_is_hidden(self):
        self.conn.status = "shared"
        self.conn.requester_consents_to_share = True
        self.conn.recipient_consents_to_share = True
        self.conn.requester_shares_email = True
        self.conn.save()
        # The requester shared their email; the recipient did not.
        self.assertIsNone(self._connected_with(self.me))
        self.assertEqual(self._connected_with(self.other), self.me.email)

    def test_shared_with_email_is_exported(self):
        self.conn.status = "shared"
        self.conn.requester_consents_to_share = True
        self.conn.recipient_consents_to_share = True
        self.conn.recipient_shares_email = True
        self.conn.save()
        self.assertEqual(self._connected_with(self.me), self.other.email)


class TextMutedTokenTests(TestCase):
    def test_text_muted_maps_to_the_token(self):
        css = (REPO_ROOT / "tailwind-src/crush_lu/tailwind-input.css").read_text(
            encoding="utf-8"
        )
        match = re.search(r"\n\s*\.text-muted\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertIn("text-muted-fg", body)
        self.assertNotIn("gray-500", body)

    def test_legal_pages_drop_the_duplicate_dark_override(self):
        for name in ("privacy_policy", "terms_of_service", "child_safety_standards"):
            html = (REPO_ROOT / f"crush_lu/templates/crush_lu/{name}.html").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("text-muted dark:text-gray-400", html, name)


class _ThemeToggleAttrs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.toggles = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("x-data") == "themeToggle":
            self.toggles.append(attrs)


@override_settings(CRUSH_CONNECT_CANDIDATE_OPEN=True)
class ThemeToggleLabelsAndLandingTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_theme_toggle_labels_are_translated(self):
        response = Client().get("/de/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        parser = _ThemeToggleAttrs()
        parser.feed(response.content.decode())
        self.assertGreaterEqual(len(parser.toggles), 1)
        for attrs in parser.toggles:
            self.assertEqual(attrs.get("data-label-dark-mode"), "Dunkelmodus")
            self.assertEqual(attrs.get("data-label-system-light"), "System (Hell)")
            self.assertEqual(
                attrs.get("data-label-switch-light-mode"), "Zum Hellmodus wechseln"
            )

    def test_landing_cards_carry_their_subline(self):
        response = Client().get("/en/crush-connect/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        for tagline in (
            "One match a week, picked by a human.",
            "No bios. Three questions. One photo.",
            "Verified, discoverable, free — forever.",
        ):
            self.assertContains(response, tagline)

    def test_landing_sublines_are_translated(self):
        response = Client().get("/fr/crush-connect/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Un match par semaine, choisi par un humain.")
