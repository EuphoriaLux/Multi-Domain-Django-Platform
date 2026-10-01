"""UX Wave 4 · WP3: gift wizard (findings 7-06, 7-07; #1053 decision C).

* 7-06 — the audio listener is bound to ``id_chapter5_letter_music``; a
  server re-render opens on the step holding the first error and shows a
  role=alert summary linking to the errored fields.
* 7-07 — the Back button carries the ``btn-gift`` base, the stepper is an
  ``<ol>`` with ``aria-current="step"``, and the media headings are 1–4.
* #1053 decision C — only successful creates count toward the 5/day cap.

Paths are literal: ``reverse("crush_lu:...")`` builds ``/crush/...`` paths
that 404 under ``HTTP_HOST=crush.lu``.
"""

import base64
from html.parser import HTMLParser
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

User = get_user_model()

CREATE_URL = "/en/journey/gift/create/"
JS_DIR = Path(__file__).resolve().parents[1] / "static" / "crush_lu" / "js"
PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
VALID = {
    "recipient_name": "Marie",
    "date_first_met": "2024-02-14",
    "location_first_met": "Luxembourg City",
}


class _Collector(HTMLParser):
    """Collects the elements (tag, attrs, text) the wizard assertions need."""

    def __init__(self):
        super().__init__()
        self.elements = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": dict(attrs), "text": "", "parents": []}
        element["parents"] = [e["tag"] for e in self._stack]
        self.elements.append(element)
        if tag not in ("input", "br", "img", "meta", "link", "hr", "source"):
            self._stack.append(element)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i]["tag"] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        for element in self._stack:
            element["text"] += data


def _parse(response):
    parser = _Collector()
    parser.feed(response.content.decode())
    return parser.elements


def _find(elements, tag, **attrs):
    return [
        e
        for e in elements
        if e["tag"] == tag and all(e["attrs"].get(k) == v for k, v in attrs.items())
    ]


def _give_consent(user):
    """CrushConsentMiddleware redirects accounts without Crush.lu consent."""
    from crush_lu.models import UserDataConsent

    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )


class GiftWizardTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.user = User.objects.create_user(
            username="staff@example.com",
            email="staff@example.com",
            password="testpass123",
            is_staff=True,
        )
        _give_consent(self.user)
        self.client.login(username="staff@example.com", password="testpass123")

    def _stepper(self, response):
        self.assertEqual(response.status_code, 200)
        elements = _parse(response)
        stepper = _find(elements, "ol", **{"class": "step-nav"})
        self.assertEqual(len(stepper), 1)
        items = [e for e in elements if e["tag"] == "li" and "ol" in e["parents"]]
        steppers = [e for e in items if "step-indicator" in e["attrs"].get("class", "")]
        self.assertEqual(len(steppers), 2)
        return elements, steppers

    def _root(self, elements):
        roots = [e for e in elements if e["attrs"].get("x-data") == "giftCreateForm"]
        self.assertEqual(len(roots), 1)
        return roots[0]


class GiftWizardMarkupTests(GiftWizardTestBase):
    def test_get_opens_on_step_one_without_error_summary(self):
        response = self.client.get(CREATE_URL)
        self.assertEqual(response.status_code, 200)
        elements, steps = self._stepper(response)
        self.assertEqual(self._root(elements)["attrs"]["data-initial-step"], "1")
        self.assertEqual(steps[0]["attrs"].get("aria-current"), "step")
        self.assertNotIn("aria-current", steps[1]["attrs"])
        self.assertEqual(steps[0]["attrs"]["x-bind:aria-current"], "stepOneCurrent")
        self.assertEqual(steps[1]["attrs"]["x-bind:aria-current"], "stepTwoCurrent")
        self.assertIn("Story details", steps[0]["text"])
        self.assertIn("Add media", steps[1]["text"])
        self.assertFalse(_find(elements, "div", role="alert"))

    def test_back_button_carries_the_btn_gift_base(self):
        elements = _parse(self.client.get(CREATE_URL))
        back = [
            e
            for e in elements
            if e["tag"] == "button" and e["attrs"].get("@click") == "goToStep1"
        ]
        self.assertEqual(len(back), 1)
        self.assertEqual(
            back[0]["attrs"]["class"].split(), ["btn-gift", "btn-gift-secondary"]
        )

    def test_media_headings_are_numbered_one_to_four(self):
        elements = _parse(self.client.get(CREATE_URL))
        headings = [
            " ".join(e["text"].split())
            for e in _find(elements, "h3", **{"class": "media-section-title"})
        ]
        self.assertEqual(
            headings,
            [
                "🧩 1. Photo Puzzle",
                "🎞️ 2. Photo Slideshow",
                "📹 3. Video Message",
                "🎵 4. Letter Music",
            ],
        )

    def test_stepper_is_translated(self):
        for lang, one, two in (
            ("de", "Details zur Geschichte", "Medien hinzufügen"),
            ("fr", "Détails de l'histoire", "Ajouter des médias"),
        ):
            response = self.client.get(f"/{lang}/journey/gift/create/")
            _, steps = self._stepper(response)
            self.assertIn(one, steps[0]["text"])
            self.assertIn(two, steps[1]["text"])


class GiftWizardErrorStepTests(GiftWizardTestBase):
    def test_media_error_opens_step_two_with_linked_summary(self):
        bad = SimpleUploadedFile("puzzle.jpg", b"not an image", "image/jpeg")
        response = self.client.post(CREATE_URL, {**VALID, "chapter1_image": bad})
        self.assertEqual(response.status_code, 200)
        elements, steps = self._stepper(response)
        self.assertEqual(self._root(elements)["attrs"]["data-initial-step"], "2")
        self.assertEqual(steps[1]["attrs"].get("aria-current"), "step")
        self.assertNotIn("aria-current", steps[0]["attrs"])

        alerts = _find(elements, "div", role="alert")
        self.assertEqual(len(alerts), 1)
        self.assertIn("data-gift-error-summary", alerts[0]["attrs"])
        self.assertIn("Please fix the following errors:", alerts[0]["text"])
        self.assertIn("select them again", alerts[0]["text"])
        links = _find(elements, "a", href="#id_chapter1_image")
        self.assertEqual(len(links), 1)
        self.assertIn("Photo Puzzle Image", links[0]["text"])

        # The summary sits inside the step-2 panel (the one Alpine opens).
        panels = [
            e
            for e in elements
            if e["attrs"].get("x-bind:class") == "stepTwoContentClass"
        ]
        self.assertEqual(len(panels), 1)
        self.assertIn("Please fix the following errors:", panels[0]["text"])

    def test_story_error_keeps_step_one_and_summarises_there(self):
        response = self.client.post(
            CREATE_URL, {**VALID, "recipient_name": "", "recipient_email": "nope"}
        )
        elements, steps = self._stepper(response)
        self.assertEqual(self._root(elements)["attrs"]["data-initial-step"], "1")
        self.assertEqual(steps[0]["attrs"].get("aria-current"), "step")
        self.assertEqual(len(_find(elements, "a", href="#id_recipient_name")), 1)
        self.assertEqual(len(_find(elements, "a", href="#id_recipient_email")), 1)
        panels = [
            e
            for e in elements
            if e["attrs"].get("x-bind:class") == "stepOneContentClass"
        ]
        self.assertIn("Please fix the following errors:", panels[0]["text"])
        self.assertNotIn("select them again", response.content.decode())

    def test_errors_on_both_steps_open_step_one(self):
        bad = SimpleUploadedFile("puzzle.jpg", b"not an image", "image/jpeg")
        response = self.client.post(
            CREATE_URL, {**VALID, "recipient_name": "", "chapter1_image": bad}
        )
        elements, _ = self._stepper(response)
        self.assertEqual(self._root(elements)["attrs"]["data-initial-step"], "1")
        self.assertEqual(len(_find(elements, "div", role="alert")), 2)

    def test_summary_is_translated(self):
        bad = SimpleUploadedFile("puzzle.jpg", b"not an image", "image/jpeg")
        response = self.client.post(
            "/de/journey/gift/create/", {**VALID, "chapter1_image": bad}
        )
        self.assertContains(response, "Bitte behebe die folgenden Fehler:")
        self.assertContains(response, "Bitte wähle sie erneut aus.")
        response = self.client.post(
            "/fr/journey/gift/create/", {**VALID, "chapter1_image": bad}
        )
        self.assertContains(response, "Veuillez corriger les erreurs suivantes")
        self.assertContains(response, "Veuillez les sélectionner à nouveau.")


class GiftWizardScriptTests(TestCase):
    def test_audio_listener_is_bound_to_the_letter_music_field(self):
        for name in ("alpine/journey.js", "alpine/journey.min.js"):
            source = (JS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("id_chapter5_letter_music", source, name)
            self.assertNotIn("id_chapter4_audio", source, name)


class GiftCreateRateLimitTests(GiftWizardTestBase):
    def test_invalid_posts_do_not_count_toward_the_daily_cap(self):
        from crush_lu.models import JourneyGift

        for _ in range(8):
            response = self.client.post(CREATE_URL, {})
            self.assertEqual(response.status_code, 200)
        for i in range(5):
            response = self.client.post(CREATE_URL, VALID)
            self.assertEqual(response.status_code, 302, i)
        self.assertEqual(JourneyGift.objects.count(), 5)

        sixth = self.client.post(CREATE_URL, VALID)
        self.assertEqual(sixth.status_code, 429)
        self.assertEqual(JourneyGift.objects.count(), 5)
        # Once capped, even an invalid POST is refused before processing.
        self.assertEqual(self.client.post(CREATE_URL, {}).status_code, 429)
        # GET still renders the form.
        self.assertEqual(self.client.get(CREATE_URL).status_code, 200)


class GiftWizardRestTests(GiftWizardTestBase):
    """Wave 5 · WP13: the remainders of 7-06/7-07 (#1111)."""

    def test_step_one_failure_with_uploads_shows_the_reselect_note(self):
        # A valid image, so only step one fails.
        photo = SimpleUploadedFile("puzzle.png", PIXEL_PNG, "image/png")
        response = self.client.post(
            CREATE_URL, {**VALID, "recipient_name": "", "chapter1_image": photo}
        )
        elements = _parse(response)
        self.assertEqual(len(_find(elements, "div", role="alert")), 1)
        self.assertContains(response, "Please select them again.")

    def test_step_one_failure_without_uploads_has_no_note(self):
        response = self.client.post(CREATE_URL, {**VALID, "recipient_name": ""})
        self.assertNotContains(response, "select them again")

    def test_story_labels_carry_a_visible_required_marker(self):
        html = self.client.get(CREATE_URL).content.decode()
        self.assertEqual(html.count("(Required)"), 3)

    def test_client_errors_are_served_translated_from_the_page(self):
        de = self.client.get("/de/journey/gift/create/").content.decode()
        self.assertIn('data-i18n-audio-size="Die Audiodatei ist zu groß.', de)
        fr = self.client.get("/fr/journey/gift/create/").content.decode()
        self.assertIn('data-i18n-video-size="Le fichier vidéo est trop volumineux.', fr)

    def test_script_has_no_hardcoded_english_client_errors(self):
        for name in ("alpine/journey.js", "alpine/journey.min.js"):
            source = (JS_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("Invalid audio format.", source, name)
            self.assertNotIn("too large. Maximum", source, name)

    def test_stale_thumbnail_reads_cannot_overwrite_the_selection(self):
        for name in ("alpine/journey.js", "alpine/journey.min.js"):
            source = (JS_DIR / name).read_text(encoding="utf-8")
            start = source.index("readAsDataURL")
            # The onload callback sits just before readAsDataURL.
            callback = source[max(0, start - 600) : start]
            self.assertRegex(callback, r"files\[0\]\s*[!=]==\s*\w+", name)

    def test_thumbnail_falls_back_to_the_extension_without_a_mime_type(self):
        for name in ("alpine/journey.js", "alpine/journey.min.js"):
            source = (JS_DIR / name).read_text(encoding="utf-8")
            start = source.index("handleSlideshowFileChange")
            block = source[start : source.index("readAsDataURL", start)]
            self.assertRegex(block, r"jpe\?g", name)
            self.assertIn("heic", block, name)

    def test_slideshow_tiles_have_thumbnail_slots(self):
        html = self.client.get(CREATE_URL).content.decode()
        self.assertEqual(html.count('class="slideshow-item"'), 5)
        # Five slideshow slots plus the photo puzzle's own preview.
        self.assertEqual(html.count('class="file-upload-preview"'), 6)

    def test_letter_music_help_does_not_number_a_chapter(self):
        self.assertNotContains(self.client.get(CREATE_URL), "in Chapter 5")

    def test_recipient_placeholder_is_short(self):
        self.assertContains(
            self.client.get(CREATE_URL), 'placeholder="e.g., Marie, Sunshine"'
        )

    def test_reported_translations_are_fixed(self):
        from django.utils import translation

        with translation.override("de"):
            self.assertEqual(
                translation.gettext("Chapter 3: Photo Slideshow"),
                "Kapitel 3: Foto-Diashow",
            )
        with translation.override("fr"):
            self.assertEqual(
                translation.gettext("Click or drag to upload photo"),
                "Cliquez ou glissez pour télécharger une photo",
            )
