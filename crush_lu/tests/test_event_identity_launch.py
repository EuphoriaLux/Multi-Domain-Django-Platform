"""Phase E launch-readiness tests for the Event Identity redesign (spec §10).

Covers:

  * the tracked DE/FR message catalogs (.mo) ship a translation for every
    Event Identity string introduced in Phases C-D — the guard fails if a new
    identity string lands without a catalog entry;
  * the German catalog is actually picked up when the merged edit section
    renders under an active ``de`` locale;
  * keyboard / screen-reader markup on the new chip controls does not
    regress: visually-hidden-but-focusable ``peer sr-only`` inputs (never
    ``peer hidden``, which removes them from the tab order), a visible
    ``peer-focus-visible`` focus ring, and ``aria-pressed`` on the trait
    toggle chips.

Phase C write-path tests live in ``test_event_identity_ui``; the Phase D
surface/grep guard in ``test_event_identity_surfaces``.
"""

import gettext
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from allauth.account.models import EmailAddress

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}

APP_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = APP_ROOT / "templates"
LOCALE_ROOT = APP_ROOT / "locale"
JS_COMPONENTS = APP_ROOT / "static" / "crush_lu" / "js" / "alpine-components.js"

# Every Event Identity msgid introduced in Phases C-D that must be translated
# in the tracked DE/FR catalogs (spec §10 "launch readiness: translations").
# Keep in sync with scripts/i18n/add_event_identity_translations.py.
EVENT_IDENTITY_MSGIDS = [
    # Edit card / wizard / display surfaces
    "Your Event Identity",
    "Event Identity",
    "Your Event Identity was updated!",
    "This is what people will discover about you at our events — no long bio needed.",
    "This is what people will discover about you at our events — no long bio needed. (All optional.)",
    "Vibe, interests & conversation starters — what people discover at events.",
    "Your vibe",
    "Pick up to 5 qualities and up to 5 things you own up to.",
    "Your interests",
    "Pick up to 8 — these appear as chips at events.",
    "Ask me about…",
    "Ask me about",
    "Highlight up to 3 of your interests as conversation starters.",
    "Select some interests above first.",
    "Nothing selected yet (optional)",
    "My event vibe",
    "Pick the one that sounds most like you at an event.",
    "Skip for now",
    "Legacy bio & interests (pre-2026 redesign)",
    # edit_profile IA cards on the same page
    "Name visibility and age display",
    "Phone number & region",
    # Event vibe choices (model)
    "First one on the dance floor",
    "Quiet corner conversations",
    "Here to meet everyone",
    "Dragged along by friends",
    # Form validation errors
    "Invalid conversation-starter selection.",
    "Pick each conversation starter once.",
    "Pick at most %(max)d conversation starters.",
    "Select at least one language you can speak at events.",
    "“Ask me about” items must be among your selected interests.",
    # Model help texts (admin/coach tooling)
    "Curated event interests (max 8) — replaces free-text interests",
    "Up to 3 interest ids to highlight as conversation starters",
    "Your event vibe — one chip shown on event surfaces",
    "DEPRECATED (Event Identity redesign): legacy free-text bio, coach-visible only",
    "DEPRECATED (Event Identity redesign): legacy free-text interests, coach-visible only",
]


class _SiteMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


def _member(username, **profile_kwargs):
    """A consent-granted, email-verified user + logged-in client + profile."""
    client = Client()
    user = User.objects.create_user(
        username=username, email=username, password="pass-pass-pass", first_name="Bo"
    )
    EmailAddress.objects.create(user=user, email=username, verified=True, primary=True)
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save()
    defaults = dict(
        date_of_birth=timezone.now().date() - timedelta(days=30 * 365),
        gender="F",
        location="canton-luxembourg",
        phone_number="+35212345678",
        phone_verified=True,
        phone_verified_at=timezone.now(),
        event_languages=["en"],
        is_approved=True,
        verification_status="verified",
    )
    defaults.update(profile_kwargs)
    profile = CrushProfile.objects.create(user=user, **defaults)
    client.login(username=username, password="pass-pass-pass")
    return client, user, profile


class EventIdentityCatalogTests(SimpleTestCase):
    """The tracked DE/FR .mo catalogs must translate every Event Identity
    string (spec §10). Reads the compiled catalogs with stdlib gettext so the
    guard runs anywhere without a gettext toolchain."""

    def _catalog(self, lang):
        mo_path = LOCALE_ROOT / lang / "LC_MESSAGES" / "django.mo"
        if not mo_path.exists():
            self.fail(f"compiled catalog missing: {mo_path}")
        with mo_path.open("rb") as fh:
            return gettext.GNUTranslations(fh)

    def test_msgid_list_matches_the_translation_script(self):
        """The guard's msgid list must stay in sync with the script that
        writes the catalogs (scripts/i18n/add_event_identity_translations.py)."""
        script = (
            APP_ROOT.parent / "scripts" / "i18n" / "add_event_identity_translations.py"
        ).read_text(encoding="utf-8")
        missing = [m for m in EVENT_IDENTITY_MSGIDS if f'    "{m}": (' not in script]
        self.assertEqual(
            missing,
            [],
            "msgids missing from add_event_identity_translations.py — "
            "keep both lists in sync.",
        )

    def test_every_event_identity_msgid_is_translated_de_fr(self):
        for lang in ("de", "fr"):
            catalog = self._catalog(lang)
            untranslated = [m for m in EVENT_IDENTITY_MSGIDS if catalog.gettext(m) == m]
            self.assertEqual(
                untranslated,
                [],
                f"[{lang}] untranslated Event Identity strings "
                f"(add them via scripts/i18n/add_event_identity_translations.py):\n"
                + "\n".join(untranslated),
            )

    def test_compiled_mo_is_not_stale_for_a_phase_e_msgid(self):
        """Spot-check a Phase E msgid end-to-end: the .mo must contain the
        exact msgstr the .po ships (catches a .po edited without rebuilding
        the .mo)."""
        expectations = {
            "de": ("Your Event Identity", "Deine Event-Identität"),
            "fr": ("Your Event Identity", "Votre identité événement"),
        }
        for lang, (msgid, msgstr) in expectations.items():
            self.assertEqual(self._catalog(lang).gettext(msgid), msgstr)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class EventIdentityGermanRenderTests(_SiteMixin, TestCase):
    """The merged edit section renders in German under an active de locale."""

    def test_event_identity_section_renders_translated_in_german(self):
        client, _user, _profile = _member("i18n-de@example.com")
        with translation.override("de"):
            resp = client.get(
                reverse("crush_lu:edit_profile") + "?section=event_identity",
                HTTP_HOST="crush.lu",
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Deine Event-Identität", body)
        self.assertIn("Deine Interessen", body)
        self.assertIn("Meine Event-Stimmung", body)
        self.assertIn("Vorerst überspringen", body)
        # No untranslated Phase C heading left on the section. ("My event vibe"
        # also lives in an HTML comment, so only the card title is asserted.)
        self.assertNotIn("Your Event Identity", body)

    def test_event_identity_section_renders_translated_in_french(self):
        client, _user, _profile = _member("i18n-fr@example.com")
        with translation.override("fr"):
            resp = client.get(
                reverse("crush_lu:edit_profile") + "?section=event_identity",
                HTTP_HOST="crush.lu",
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Votre identité événement", body)
        self.assertIn("Vos centres d'intérêt", body)
        self.assertNotIn("Your Event Identity", body)


class EventIdentityAccessibilityMarkupTests(SimpleTestCase):
    """Source guard for the chip-control markup on the new Event Identity
    surfaces (spec §10 accessibility)."""

    CHIP_TEMPLATES = [
        "crush_lu/partials/edit_event_identity.html",
        "crush_lu/create_profile.html",
    ]

    def test_chip_inputs_are_focusable_never_display_none(self):
        """`peer hidden` (display:none) removes the checkbox/radio from the
        tab order; the repo's accessible pattern is `peer sr-only`."""
        for rel in self.CHIP_TEMPLATES:
            text = (TEMPLATE_ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn(
                'class="peer hidden"',
                text,
                f"{rel}: chip inputs must use `peer sr-only`, not `peer hidden`",
            )

    def test_chip_inputs_have_a_visible_focus_ring(self):
        for rel in self.CHIP_TEMPLATES:
            text = (TEMPLATE_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(
                "peer-focus-visible:ring-purple-500",
                text,
                f"{rel}: sr-only chip inputs need a peer-focus-visible ring",
            )

    def test_trait_chips_expose_aria_pressed(self):
        """traitSelector toggle buttons must sync aria-pressed for screen
        readers (they are <button type=\"button\"> chips, not checkboxes)."""
        text = JS_COMPONENTS.read_text(encoding="utf-8")
        self.assertIn('setAttribute("aria-pressed"', text)
