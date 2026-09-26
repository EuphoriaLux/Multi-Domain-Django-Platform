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

import html
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

    def _render_with_cookies(self, cookies):
        request = RequestFactory().get("/")
        request.COOKIES.update(cookies)
        # The library sees nothing in its own format; only the banner's.
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=None
        ):
            return Template("{% load analytics %}{% analytics_body %}").render(
                Context({"FACEBOOK_PIXEL_ID": "123456", "request": request})
            )

    def test_banner_json_choice_counts_server_side(self):
        """The banner stores its choice as JSON (and a per-group flag), never
        in the library's format; the server must read it, or every visitor who
        accepted through the banner stays "undecided" and fb_event calls that
        run before the banner's script are dropped."""
        accepted = self._render_with_cookies(
            {"cookie_consent": '{"essential":true,"analytics":false,"marketing":true}'}
        )
        declined = self._render_with_cookies(
            {"cookie_consent": '{"essential":true,"analytics":true,"marketing":false}'}
        )

        self.assertIn("fbq('init', '123456')", accepted)
        self.assertNotIn("waiting for consent", accepted)
        self.assertIn("waiting for consent", declined)

    def test_banner_flag_cookie_counts_server_side(self):
        accepted = self._render_with_cookies({"cookie_consent_marketing": "accept"})
        declined = self._render_with_cookies({"cookie_consent_marketing": "decline"})

        self.assertIn("fbq('init', '123456')", accepted)
        self.assertIn("waiting for consent", declined)

    def test_banner_choice_wins_over_a_stale_library_cookie(self):
        """The banner posts each save to the library too, but that post can
        be cut off by a navigation; the banner's own cookies are then the
        newer choice and must win over the library's HttpOnly cookie."""
        request = RequestFactory().get("/")
        request.COOKIES["cookie_consent_marketing"] = "decline"
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=True
        ):
            html = Template("{% load analytics %}{% analytics_body %}").render(
                Context({"FACEBOOK_PIXEL_ID": "123456", "request": request})
            )

        self.assertIn("waiting for consent", html)
        self.assertNotIn("fbq('init', '123456')", html)


class AppInsightsConsentTests(SimpleTestCase):
    """Browser telemetry is an analytics cookie: the SDK must not load, nor
    the preconnect open a connection, before analytics is accepted."""

    def _render(self, cookies=None, with_request=True):
        context = {"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc"}
        if with_request:
            request = RequestFactory().get("/")
            request.COOKIES.update(cookies or {})
            context["request"] = request
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=None
        ):
            return Template("{% load analytics %}{% appinsights_head %}").render(
                Context(context)
            )

    def test_undecided_visitor_gets_a_waiting_placeholder(self):
        html = self._render({})

        self.assertIn("waiting for analytics consent", html)
        self.assertNotIn('rel="preconnect"', html)
        self.assertIn("cookie_consent_updated", html)
        self.assertIn("e.detail.analytics === true", html)
        # The snippet is there, but only inside load(): nothing runs on parse.
        self.assertIn("function load() {", html)
        self.assertLess(
            html.index("function load() {"), html.index("InstrumentationKey=abc")
        )

    def test_declined_visitor_gets_the_placeholder(self):
        html = self._render({"cookie_consent_analytics": "decline"})

        self.assertIn("waiting for analytics consent", html)

    def test_no_request_means_no_sdk(self):
        html = self._render(with_request=False)

        self.assertIn("waiting for analytics consent", html)

    def test_accepted_visitor_gets_the_sdk(self):
        html = self._render({"cookie_consent_analytics": "accept"})

        self.assertNotIn("waiting for analytics consent", html)
        self.assertIn('rel="preconnect" href="https://js.monitor.azure.com"', html)
        self.assertIn("InstrumentationKey=abc", html)

    def test_banner_silences_a_loaded_sdk_on_withdrawal(self):
        html = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
        self.assertIn(
            "window.appInsights.config.disableTelemetry = consent.analytics !== true;",
            html,
        )
        self.assertIn("keepalive: true", html)


class ConsentStateTagTests(SimpleTestCase):
    """The banner reads the server's view of the stored choice from
    data-consent-state, because the library's own cookie is HttpOnly."""

    def _state(self, cookies=None, with_request=True):
        import json

        context = {}
        if with_request:
            request = RequestFactory().get("/")
            request.COOKIES.update(cookies or {})
            context["request"] = request
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=None
        ):
            rendered = Template(
                "{% load analytics %}{% cookie_consent_state %}"
            ).render(Context(context))
        return json.loads(html.unescape(rendered))

    def test_undecided_without_a_request_or_a_cookie(self):
        self.assertEqual(
            self._state(with_request=False),
            {"analytics": None, "marketing": None, "decided": False},
        )
        self.assertEqual(
            self._state({}), {"analytics": None, "marketing": None, "decided": False}
        )

    def test_reflects_the_banner_json(self):
        self.assertEqual(
            self._state({"cookie_consent": '{"analytics":true,"marketing":false}'}),
            {"analytics": True, "marketing": False, "decided": True},
        )

    def test_reflects_the_library_cookie(self):
        request = RequestFactory().get("/")
        with patch(
            "cookie_consent.util.get_cookie_value_from_request",
            side_effect=lambda req, group: {"analytics": False, "marketing": True}[
                group
            ],
        ):
            rendered = Template(
                "{% load analytics %}{% cookie_consent_state %}"
            ).render(Context({"request": request}))
        import json

        self.assertEqual(
            json.loads(html.unescape(rendered)),
            {"analytics": False, "marketing": True, "decided": True},
        )

    def test_saving_updates_the_library_cookie_and_the_embedded_state(self):
        """The library's cookie is HttpOnly (the server reads it first) and the
        CSRF cookie is HttpOnly too: a saved choice is posted through the
        library's status/accept/decline endpoints, and data-consent-state is
        rewritten so a reopened modal on the same page shows the new choice."""
        html = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
        self.assertIn("const NATIVE_STATUS_URL = '/cookies/status/';", html)
        self.assertIn("'X-CSRFToken': csrftoken", html)
        self.assertIn("body.append('cookie_groups', group)", html)
        self.assertIn("banner.dataset.consentState = JSON.stringify({", html)
        # Accept, then decline: concurrent posts would each rewrite the whole
        # library cookie from a stale copy.
        sync = html[
            html.index("function syncNativeConsent(") : html.index(
                "function syncServerState("
            )
        ]
        self.assertLess(
            sync.index("postNativeChoice(status.acceptUrl"),
            sync.index("postNativeChoice(status.declineUrl"),
        )
        self.assertIn(".then(function() {", sync)
        self.assertNotIn("Promise.all", sync)
        for fn in ("acceptAllCookies", "declineAllCookies", "saveCustomCookies"):
            body = _js_function_body(html, fn)
            self.assertIn("syncNativeConsent(consent);", body, fn)
            self.assertIn("syncServerState(consent);", body, fn)
            # State first, then the event the analytics scripts react to.
            self.assertLess(
                body.index("syncServerState(consent);"),
                body.index("dispatchConsentEvent(consent);"),
                fn,
            )

    def test_banner_carries_the_state_and_reads_it_first(self):
        request = RequestFactory().get("/")
        request.COOKIES["cookie_consent"] = '{"analytics":false,"marketing":true}'
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=None
        ):
            html = render_to_string(BANNER_TEMPLATE, {"request": request})
        self.assertIn(
            'data-consent-state="{&quot;analytics&quot;: false, &quot;marketing&quot;: true, &quot;decided&quot;: true}"',
            html,
        )
        self.assertIn("function serverConsent()", html)
        self.assertIn("if (!consent && !serverConsent())", html)


class CookieSettingsTriggerTests(TestCase):
    """Once a choice is stored the banner stays hidden, so a persistent footer
    link has to reopen the preferences modal."""

    def setUp(self):
        cache.clear()

    def test_signed_in_member_reaches_the_settings_at_every_width(self):
        """The crush footer is hidden for signed-in members below lg, so the
        account settings page carries its own trigger."""
        from datetime import date

        from allauth.account.models import EmailAddress
        from django.contrib.auth import get_user_model

        from crush_lu.models import CrushProfile, UserDataConsent

        # A member the consent middleware and the settings view let through:
        # verified email, Crush consent, approved profile.
        user = get_user_model().objects.create_user(
            username="member@example.com",
            email="member@example.com",
            password="testpass123",
            first_name="Lena",
            last_name="Schmit",
        )
        EmailAddress.objects.create(
            user=user, email=user.email, verified=True, primary=True
        )
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 5, 15),
            gender="F",
            location="Luxembourg City",
            phone_number="+352621123456",
            phone_verified=True,
            is_approved=True,
            verification_status="verified",
            is_active=True,
        )
        client = Client()
        client.force_login(user)
        client.cookies["cookie_consent"] = (
            '{"essential":true,"analytics":false,"marketing":false}'
        )

        response = client.get(
            "/en/account/settings/", HTTP_HOST="crush.lu", follow=True
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-cookie-settings class="text-purple-600', html)
        self.assertIn("Cookie Settings", html)

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
    # The library's own endpoints: the status view hands out the CSRF token and
    # the accept/decline URLs; record what the banner posts to them.
    posted = []

    def native(route):
        request = route.request
        if request.url.endswith("/cookies/status/"):
            return route.fulfill(
                content_type="application/json",
                body='{"csrftoken":"tok","acceptUrl":"/cookies/accept/","declineUrl":"/cookies/decline/"}',
            )
        posted.append(
            (
                request.url.rsplit("/cookies/", 1)[1],
                request.post_data,
                request.headers.get("x-csrftoken"),
            )
        )
        return route.fulfill(status=200, body="")

    page.route("**/cookies/**", native)
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
        "window.appInsights = { config: { disableTelemetry: false } };"
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
    # Analytics stayed accepted in this save, so App Insights keeps running.
    assert page.evaluate("window.appInsights.config.disableTelemetry") is False

    # The withdrawal reached the library (HttpOnly cookie updated server-side)...
    page.wait_for_function("() => window.__fbqCalls.length > 0")
    page.wait_for_timeout(200)
    assert ("accept/", "cookie_groups=analytics", "tok") in posted
    assert ("decline/", "cookie_groups=marketing", "tok") in posted
    # ...one after the other, accept first.
    assert posted.index(("accept/", "cookie_groups=analytics", "tok")) < posted.index(
        ("decline/", "cookie_groups=marketing", "tok")
    )
    # ...and a reopened modal on the same page shows the new choice, not the
    # state the server rendered before the save.
    page.click("#open-cookie-settings")
    assert page.is_checked("#cookie-analytics")
    assert not page.is_checked("#cookie-marketing")


@pytest.mark.playwright
def test_server_side_choice_drives_the_banner_when_the_cookie_is_httponly(page):
    """A choice stored only where document.cookie cannot see it (the
    library's HttpOnly cookie) must still keep the banner closed and show up
    in the reopened settings: the page reads data-consent-state instead."""
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent"] = (
        '{"essential":true,"analytics":false,"marketing":true}'
    )
    with patch("cookie_consent.util.get_cookie_value_from_request", return_value=None):
        html = render_to_string(
            BANNER_TEMPLATE, {"cookie_banner_variant": "crush", "request": request}
        )
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
    # No cookie at all in the browser: the server-rendered state is the only source.
    page.goto(url)
    assert (
        page.evaluate(
            "getComputedStyle(document.getElementById('cookie-consent-banner')).display"
        )
        == "none"
    )
    page.click("#open-cookie-settings")
    checked = page.evaluate(
        "[document.getElementById('cookie-analytics').checked, "
        "document.getElementById('cookie-marketing').checked]"
    )
    assert checked == [False, True]
