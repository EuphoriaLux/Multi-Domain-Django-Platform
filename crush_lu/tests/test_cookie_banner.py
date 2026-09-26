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

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
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

    def test_native_cookie_consent_format_is_reflected(self):
        """The library's /cookies/ views store "group=version|...", not this
        banner's JSON; the modal must read both, or it shows an accepted
        choice as declined and a save overwrites it."""
        html = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
        self.assertIn("function storedConsent()", html)
        self.assertIn("consent.split('|')", html)
        self.assertIn("parts[1] !== '-1'", html)

    def test_withdrawn_marketing_revokes_the_pixel(self):
        html = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
        self.assertIn(
            "window.fbq('consent', consent.marketing ? 'grant' : 'revoke')", html
        )

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
        self.assertIn("storedConsent()", reflect)
        stored = _js_function_body(script, "storedConsent")
        self.assertIn("getCookie(COOKIE_NAME)", stored)
        # A stored JSON object or the library's own "group=version|..." string
        # counts; anything else leaves both off.
        self.assertIn("typeof consent === 'object'", stored)
        self.assertIn("consent.split('|')", stored)
        self.assertIn("analytics.checked = stored.analytics === true;", reflect)
        self.assertIn("marketing.checked = stored.marketing === true;", reflect)


class FacebookPixelConsentTests(SimpleTestCase):
    """An unchecked marketing toggle is only honest if the Pixel really waits:
    it has no consent mode of its own, so an undecided visitor must get the
    placeholder, not the PageView."""

    def _render(self, stored):
        request = RequestFactory().get("/")
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=stored
        ):
            return Template("{% load analytics %}{% analytics_body %}").render(
                Context({"FACEBOOK_PIXEL_ID": "123456", "request": request})
            )

    def test_undecided_visitor_gets_the_waiting_placeholder(self):
        html = self._render(None)

        self.assertIn("Facebook Pixel (waiting for consent)", html)
        self.assertIn("cookie_consent_updated", html)
        self.assertNotIn("fbq('init', '123456')", html)
        self.assertNotIn("facebook.com/tr?id=", html)

    def test_declined_visitor_gets_the_waiting_placeholder(self):
        html = self._render(False)

        self.assertIn("Facebook Pixel (waiting for consent)", html)
        self.assertNotIn("fbq('init', '123456')", html)

    def test_accepted_visitor_gets_the_pixel(self):
        html = self._render(True)

        self.assertNotIn("waiting for consent", html)
        self.assertIn("fbq('init', '123456')", html)


class CookieSettingsTriggerTests(TestCase):
    """Once a choice is stored the banner stays hidden, so a persistent footer
    link has to reopen the preferences modal."""

    def setUp(self):
        cache.clear()

    def test_every_site_footer_reopens_the_settings(self):
        client = Client()
        client.cookies["cookie_consent"] = (
            '{"essential":true,"analytics":false,"marketing":false}'
        )
        # delegations.lu is not listed: its landing page is a standalone sign-in
        # template without the banner (no consent UI at all); the portal pages
        # behind it extend the base that carries the link.
        for host, path in (
            ("crush.lu", "/en/"),
            ("vinsdelux.com", "/"),
            ("entreprinder.lu", "/"),
            ("arborist.lu", "/"),
        ):
            with self.subTest(host=host):
                response = client.get(path, HTTP_HOST=host, follow=True)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn("data-cookie-settings", html)
                self.assertIn("Cookie Settings", html)
                self.assertIn("closest('[data-cookie-settings]')", html)


@pytest.mark.playwright
@pytest.mark.parametrize(
    "stored, expected",
    [
        (None, [False, False]),
        ('{"essential":true,"analytics":true,"marketing":false}', [True, False]),
        ('{"essential":true,"analytics":false,"marketing":true}', [False, True]),
        ("accepted", [False, False]),  # legacy non-JSON value
        # django-cookie-consent's own format (its /cookies/ views): declined = -1
        ("analytics=2026-01-01T00:00:00|marketing=-1", [True, False]),
        ("analytics=-1|marketing=2026-01-01T00:00:00", [False, True]),
    ],
)
def test_settings_modal_shows_stored_choice_in_browser(page, stored, expected):
    """Run the real script: open Customize, read the two toggles."""
    html = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
    url = "http://crush.test/"
    # The footers' persistent trigger: with a stored choice the banner stays
    # hidden, and this link is the real way back to the preferences.
    footer = (
        '<a href="#" data-cookie-settings id="open-cookie-settings">Cookie Settings</a>'
    )
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body="<!doctype html><html><body>%s%s</body></html>" % (footer, html),
        ),
    )
    if stored is not None:
        page.context.add_cookies(
            [{"name": "cookie_consent", "value": stored, "url": url}]
        )
    page.goto(url)
    page.click("#open-cookie-settings")
    assert (
        page.evaluate("document.getElementById('cookie-settings-modal').style.display")
        == "flex"
    )
    checked = page.evaluate(
        "[document.getElementById('cookie-analytics').checked, "
        "document.getElementById('cookie-marketing').checked]"
    )
    assert checked == expected


@pytest.mark.playwright
def test_withdrawing_marketing_revokes_a_loaded_pixel(page):
    """A visitor who had accepted marketing (so the Pixel is loaded) unticks it
    in the reopened settings: the Pixel must be told to stop on this page, not
    only at the next navigation."""
    html = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
    url = "http://crush.test/"
    footer = (
        '<a href="#" data-cookie-settings id="open-cookie-settings">Cookie Settings</a>'
    )
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body="<!doctype html><html><body>%s%s</body></html>" % (footer, html),
        ),
    )
    # The library's own endpoints the banner also posts to.
    page.route("**/cookies/**", lambda route: route.fulfill(status=200, body=""))
    page.context.add_cookies(
        [
            {
                "name": "cookie_consent",
                "value": '{"essential":true,"analytics":true,"marketing":true}',
                "url": url,
            }
        ]
    )
    page.add_init_script(
        "window.__fbqCalls = [];"
        "window.fbq = function () { window.__fbqCalls.push([].slice.call(arguments)); };"
    )
    page.goto(url)
    page.click("#open-cookie-settings")
    assert page.is_checked("#cookie-marketing")
    # The input is visually hidden behind the styled slider: toggle it the
    # way a member does, through its label.
    page.click("label.cookie-toggle:has(#cookie-marketing)")
    assert not page.is_checked("#cookie-marketing")
    page.click("#cookie-btn-save")

    # On load the stored acceptance is re-dispatched (a grant); the save must
    # end with the revoke.
    consent_calls = [c for c in page.evaluate("window.__fbqCalls") if c[0] == "consent"]
    assert consent_calls and consent_calls[-1] == ["consent", "revoke"]
