"""Shared cookie-consent banner (core/templates/includes/cookie_banner.html).

The partial is included by five sites. Finding 8-06 of the Crush.lu UX review:

* GDPR/ePrivacy: optional categories (analytics, marketing) must default to
  off on every site; the script reflects a stored choice when the settings
  modal opens instead of a pre-ticked box.
* Crush.lu only (``cookie_banner_variant="crush"``): canonical STYLE.md §2
  buttons and the informal "du" DE copy (msgctxt "crush cookie banner" in
  crush_lu/locale, because core/locale is in LOCALE_PATHS and would otherwise
  win with its formal "Sie/Ihr" wording). Other sites keep the generic
  ``.btn`` markup and the formal DE copy.

Paths are literal because the host middleware swaps the urlconf per host;
``reverse()`` would build a path for the default host instead.
"""

import re
from html.parser import HTMLParser

import pytest
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase
from django.utils import translation

BANNER_TEMPLATE = "includes/cookie_banner.html"

EN_BANNER = (
    "We use cookies to enhance your experience. Analytics cookies help us "
    "understand how you use our site. You can accept all cookies or customize "
    "your preferences."
)
DE_FORMAL_BANNER = (
    "Wir verwenden Cookies, um Ihr Erlebnis zu verbessern. Analyse-Cookies "
    "helfen uns zu verstehen, wie Sie unsere Website nutzen. Sie können alle "
    "Cookies akzeptieren oder Ihre Einstellungen anpassen."
)
DE_DU_BANNER = (
    "Wir verwenden Cookies, um dein Erlebnis zu verbessern. Analyse-Cookies "
    "helfen uns zu verstehen, wie du unsere Website nutzt. Du kannst alle "
    "Cookies akzeptieren oder deine Einstellungen anpassen."
)
FR_BANNER = (
    "Nous utilisons des cookies pour améliorer votre expérience. Les cookies "
    "d'analyse nous aident à comprendre comment vous utilisez notre site. "
    "Vous pouvez accepter tous les cookies ou personnaliser vos préférences."
)


class _TagsById(HTMLParser):
    """Collect ``(tag, attrs)`` for every element carrying an ``id``."""

    def __init__(self):
        super().__init__()
        self.tags = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.tags.setdefault(attrs["id"], (tag, attrs))


def _tags(html):
    parser = _TagsById()
    parser.feed(html)
    return parser.tags


def _classes(tags, element_id):
    return set(tags[element_id][1].get("class", "").split())


def _banner_markup(html):
    """The rendered partial only: banner + modal + style + script."""
    start = html.index('<div id="cookie-consent-banner"')
    end = html.index("</script>", html.index("const COOKIE_NAME")) + len("</script>")
    return html[start:end]


def _js_function_body(script, name):
    match = re.search(
        r"function %s\(\) \{\n(.*?)\n    \}\n" % re.escape(name), script, re.S
    )
    assert match, f"function {name}() not found in the banner script"
    return match.group(1)


def _css_declarations(html):
    """Map each selector in the partial's <style> to its declarations."""
    css = html[html.index("<style>") : html.index("</style>")]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for selector in selectors.split(","):
            rules.setdefault(selector.strip(), []).append(" ".join(body.split()))
    return rules


def _assert_optional_categories_unchecked(tags):
    tag, analytics = tags["cookie-analytics"]
    assert tag == "input" and analytics.get("type") == "checkbox"
    assert (
        "checked" not in analytics
    ), "analytics must not be pre-ticked (GDPR/ePrivacy opt-in)"
    assert "checked" not in tags["cookie-marketing"][1]
    # Essential stays on and locked.
    essential = tags["cookie-essential"][1]
    assert "checked" in essential and "disabled" in essential


class CrushCookieBannerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def _get(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_optional_categories_are_unchecked_by_default(self):
        _assert_optional_categories_unchecked(_tags(self._get("/en/")))

    def test_buttons_use_canonical_crush_variants(self):
        html = self._get("/en/")
        tags = _tags(html)
        accept = _classes(tags, "cookie-btn-accept")
        decline = _classes(tags, "cookie-btn-decline")
        self.assertEqual(accept, {"btn-crush-solid", "btn-sm"})
        self.assertEqual(decline, {"btn-crush-outline", "btn-sm"})
        self.assertEqual(_classes(tags, "cookie-btn-customize"), {"btn-link"})
        self.assertEqual(
            _classes(tags, "cookie-btn-cancel"), {"btn-crush-outline", "btn-sm"}
        )
        self.assertEqual(
            _classes(tags, "cookie-btn-save"), {"btn-crush-solid", "btn-sm"}
        )
        self.assertIn("cookie-banner--crush", _classes(tags, "cookie-consent-banner"))
        self.assertIn("cookie-modal--crush", _classes(tags, "cookie-settings-modal"))

        banner = _banner_markup(html)
        # The generic `.cookie-banner .btn-sm` rule is unlayered and would
        # override the brand buttons' .btn-sm padding: not emitted on crush.
        self.assertNotIn(".cookie-banner .btn-sm", banner)
        self.assertNotIn("btn-primary", banner)
        self.assertNotIn("btn-secondary", banner)
        self.assertIn(
            ".cookie-modal--crush .cookie-toggle input:checked + "
            ".cookie-toggle-slider {\n    background-color: var(--crush-purple);",
            banner,
        )

    def test_de_copy_uses_informal_du(self):
        html = self._get("/de/")
        self.assertIn(DE_DU_BANNER, html)
        self.assertNotIn("Ihr Erlebnis", html)
        self.assertIn("als Reaktion auf deine Aktionen gesetzt", html)
        self.assertIn("ein Profil deiner Interessen zu erstellen und dir", html)
        self.assertNotIn("Ihrer Interessen", html)

    def test_en_and_fr_copy_match_the_shared_wording(self):
        self.assertIn(EN_BANNER, self._get("/en/"))
        self.assertIn(FR_BANNER, self._get("/fr/"))


class OtherSiteCookieBannerTests(TestCase):
    """vinsdelux.com stands in for the four non-crush includes."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="vinsdelux.com")

    def _get(self, **extra):
        response = self.client.get("/", **extra)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_optional_categories_are_unchecked_by_default(self):
        _assert_optional_categories_unchecked(_tags(self._get()))

    def test_keeps_generic_buttons_without_crush_classes(self):
        html = self._get()
        tags = _tags(html)
        self.assertEqual(
            _classes(tags, "cookie-btn-accept"), {"btn", "btn-primary", "btn-sm"}
        )
        self.assertEqual(
            _classes(tags, "cookie-btn-decline"), {"btn", "btn-secondary", "btn-sm"}
        )
        self.assertEqual(
            _classes(tags, "cookie-btn-customize"),
            {"btn", "btn-outline-secondary", "btn-sm"},
        )
        self.assertEqual(_classes(tags, "cookie-consent-banner"), {"cookie-banner"})
        banner = _banner_markup(html)
        self.assertNotIn("btn-crush", banner)
        self.assertNotIn("--crush", banner)
        self.assertIn(".cookie-banner .btn-sm", banner)

    def test_de_copy_stays_formal(self):
        html = self._get(HTTP_ACCEPT_LANGUAGE="de")
        self.assertIn(DE_FORMAL_BANNER, html)
        self.assertNotIn("dein Erlebnis", html)


class CookieBannerRenderTests(SimpleTestCase):
    def test_de_register_follows_the_variant(self):
        with translation.override("de"):
            crush = render_to_string(
                BANNER_TEMPLATE, {"cookie_banner_variant": "crush"}
            )
            generic = render_to_string(BANNER_TEMPLATE, {})
        self.assertIn(DE_DU_BANNER, crush)
        self.assertNotIn("Ihr Erlebnis", crush)
        self.assertIn(DE_FORMAL_BANNER, generic)
        self.assertNotIn("dein Erlebnis", generic)

    def test_crush_dark_button_text_uses_the_brand_token(self):
        # On the #1e293b dark surface the canonical dark .btn-link (3.52:1) and
        # .btn-crush-outline (4.47:1) miss WCAG AA 4.5:1; var(--crush-purple)
        # (#A78BFA in dark) is 5.38:1. Resting state only: hover stays canonical.
        crush = _css_declarations(
            render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
        )
        generic = _css_declarations(render_to_string(BANNER_TEMPLATE, {}))
        text_only = "html.dark .cookie-banner--crush .btn-link:not(:hover)"
        outlined = [
            "html.dark .cookie-banner--crush .btn-crush-outline:not(:hover)",
            "html.dark .cookie-modal--crush .btn-crush-outline:not(:hover)",
        ]
        for selector in [text_only, *outlined]:
            self.assertIn("color: var(--crush-purple);", crush.get(selector, []))
            self.assertNotIn(selector, generic)
        for selector in outlined:
            self.assertIn("border-color: var(--crush-purple);", crush.get(selector, []))

    def test_settings_modal_reflects_stored_consent(self):
        script = render_to_string(BANNER_TEMPLATE, {})
        show = [
            line.strip()
            for line in _js_function_body(script, "showCookieSettings").splitlines()
        ]
        # Sync the toggles before the modal becomes visible.
        self.assertEqual(show[0], "reflectStoredConsent();")
        self.assertIn("style.display = 'flex'", show[1])
        reflect = _js_function_body(script, "reflectStoredConsent")
        self.assertIn("getCookie(COOKIE_NAME)", reflect)
        # Only a stored JSON object counts; anything else leaves both off.
        self.assertIn("typeof consent === 'object'", reflect)
        self.assertIn("analytics.checked = stored.analytics === true;", reflect)
        self.assertIn("marketing.checked = stored.marketing === true;", reflect)


@pytest.mark.playwright
@pytest.mark.parametrize(
    "stored, expected",
    [
        (None, [False, False]),
        ('{"essential":true,"analytics":true,"marketing":false}', [True, False]),
        ('{"essential":true,"analytics":false,"marketing":true}', [False, True]),
        ("accepted", [False, False]),  # legacy non-JSON value
    ],
)
def test_settings_modal_shows_stored_choice_in_browser(page, stored, expected):
    """Run the real script: open Customize, read the two toggles."""
    html = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body="<!doctype html><html><body>%s</body></html>" % html,
        ),
    )
    if stored is not None:
        page.context.add_cookies(
            [{"name": "cookie_consent", "value": stored, "url": url}]
        )
    page.goto(url)
    # With a stored choice the banner stays hidden; reveal it to reach Customize.
    page.evaluate(
        "document.getElementById('cookie-consent-banner').style.display = 'block'"
    )
    page.click("#cookie-btn-customize")
    checked = page.evaluate(
        "[document.getElementById('cookie-analytics').checked, "
        "document.getElementById('cookie-marketing').checked]"
    )
    assert checked == expected
