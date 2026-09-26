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
from datetime import datetime, timezone
from html.parser import HTMLParser

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.utils import translation

BANNER_TEMPLATE = "includes/cookie_banner.html"
VERSION_SEAM = "azureproject.templatetags.analytics._cookie_group_version"
NO_VERSIONS = {"analytics": "", "marketing": ""}


def _pin_versions(test, value=""):
    """Group versions come from the DB (cached for an hour): pin them, so a
    group a TestCase left in the cache cannot make a bare "accept" stale."""
    patcher = patch(VERSION_SEAM, return_value=value)
    patcher.start()
    test.addCleanup(patcher.stop)


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

    def test_a_live_refusal_flag_vetoes_the_embedded_grant(self):
        """A copy of the page served later (the service worker's offline
        copy, a restored history entry) still embeds the grant it was
        rendered with. A refusal flag written since then must win on load,
        in the modal (else Save re-grants the withdrawal) and on a
        back/forward restore. Test_a_cached_accepted_page_honours_a_later_refusal
        runs it in a browser."""
        script = render_to_string(BANNER_TEMPLATE, {})
        helper = script[
            script.index("function currentFlag(group) {") : script.index(
                "function serverVersions()"
            )
        ]
        self.assertIn("getCookie('cookie_consent_' + group)", helper)
        self.assertIn("if (value === 'decline') return false;", helper)
        stored = _js_function_body(script, "storedConsent")
        server_branch = stored[
            : stored.index("const consent = getCookie(COOKIE_NAME);")
        ]
        self.assertIn(
            "analytics: server.analytics === true && currentFlag('analytics') !== false",
            server_branch,
        )
        self.assertIn(
            "marketing: server.marketing === true && currentFlag('marketing') !== false",
            server_branch,
        )
        # Flags only: CookieConsentFlagSyncMiddleware rewrites the flags after
        # the library's own forms, never the banner's JSON cookie.
        self.assertNotIn("COOKIE_NAME", server_branch)
        self.assertNotIn("function serverConsent()", script)  # unused, removed

    def test_a_restore_acts_only_on_a_refusal_recorded_since(self):
        """A back/forward restore reruns no load handler. It must revoke a
        tracker the page runs under an embedded grant the visitor withdrew
        since, decided page or not (a page asking about analytics already
        runs the Pixel for a current marketing acceptance), and must not
        replay an embedded refusal over a later acceptance. The browser runs
        are test_back_forward_restore_* below; this is the CI guard."""
        script = render_to_string(BANNER_TEMPLATE, {})
        pageshow = script[script.index("window.addEventListener('pageshow'") :]
        pageshow = pageshow[: pageshow.index("\n    });\n")]
        self.assertIn("if (!event.persisted) return;", pageshow)
        # A page rendered without a request keeps the load handler's fallback.
        fallback = pageshow[
            pageshow.index("if (!server) {") : pageshow.index("const withdrawn")
        ]
        self.assertIn("choiceOnLoad()", fallback)
        self.assertIn("return;", fallback)
        # With embedded state: only a grant it embeds that a live refusal
        # flag now contradicts, for either group, whatever "decided" says.
        embedded = pageshow[pageshow.index("const withdrawn") :]
        self.assertIn(
            "return server[group] === true && currentFlag(group) === false;",
            embedded,
        )
        self.assertIn("['analytics', 'marketing'].some(", embedded)
        self.assertIn("if (!withdrawn) return;", embedded)
        self.assertIn("const stored = storedConsent();", embedded)
        self.assertIn("dispatchConsentEvent(stored);", embedded)
        self.assertIn("updateGoogleConsent(stored);", embedded)
        self.assertNotIn("decided", embedded)
        self.assertNotIn("choiceOnLoad()", embedded)
        self.assertNotIn("showBanner()", pageshow)

    def test_the_identifier_wipe_spares_a_live_acceptance(self):
        """dispatchConsentEvent clears ai_user/ai_session on a refusal. Those
        belong to the browser, and a copy of the page (restored, or kept by
        the service worker) can embed a refusal older than an acceptance
        given elsewhere since: the wipe must hold back while the browser
        holds an acceptance flag. That relies on every save writing its flag
        before it dispatches, so a refusal saved on the page still clears."""
        script = render_to_string(BANNER_TEMPLATE, {})
        dispatch = script[
            script.index("function dispatchConsentEvent(consent) {") : script.index(
                "function updateGoogleConsent(consent)"
            )
        ]
        self.assertIn(
            "if (consent.analytics !== true && currentFlag('analytics') !== true) {"
            "\n            clearAppInsightsCookies();",
            dispatch,
        )
        self.assertEqual(dispatch.count("clearAppInsightsCookies();"), 1)
        for save in ("acceptAllCookies", "declineAllCookies", "saveCustomCookies"):
            body = _js_function_body(script, save)
            self.assertLess(
                body.index("setDjangoCookieConsent('analytics'"),
                body.index("dispatchConsentEvent(consent);"),
                save,
            )


# A refusal flag in the browser, as the granted branches of the analytics tags
# test it (document.cookie separates pairs with "; ").
MARKETING_DECLINED_JS = (
    "/(?:^|; )cookie_consent_marketing=decline(?:;|$)/.test(document.cookie)"
)
ANALYTICS_DECLINED_JS = (
    "/(?:^|; )cookie_consent_analytics=decline(?:;|$)/.test(document.cookie)"
)


class FacebookPixelConsentTests(SimpleTestCase):
    """An unchecked marketing toggle is only honest if the Pixel really waits:
    it has no consent mode of its own, so an undecided visitor must get the
    placeholder, not the PageView."""

    def setUp(self):
        _pin_versions(self)

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

    def test_accepted_pixel_yields_to_a_later_refusal_in_the_browser(self):
        """The full Pixel runs on parse. Served again later (the service
        worker's copy of the page), it must not load or send its PageView
        once the visitor has withdrawn marketing since the render."""
        html = self._render(True)

        guard = "if (!%s) {" % MARKETING_DECLINED_JS
        self.assertIn(guard, html)
        self.assertLess(html.index(guard), html.index("fbevents.js"))
        self.assertLess(html.index(guard), html.index("fbq('init', '123456')"))
        closing = html.index("}", html.index("fbq('track', 'PageView');"))
        self.assertLess(closing, html.index("</script>"))
        # The placeholder only loads the Pixel on a grant: it stays as it was.
        for placeholder in (self._render(None), self._render(False)):
            self.assertNotIn("cookie_consent_marketing=decline", placeholder)

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
    the preconnect open a connection, before analytics is accepted (there is
    no preconnect at all: see test_accepted_visitor_gets_the_sdk)."""

    def setUp(self):
        _pin_versions(self)

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
        self.assertIn("InstrumentationKey=abc", html)
        # No preconnect hint even then: it is HTML, so nothing could hold it
        # back on a copy of the page served after a withdrawal.
        self.assertNotIn('rel="preconnect"', html)

    def test_accepted_sdk_yields_to_a_later_refusal_in_the_browser(self):
        """The accepted SDK starts on parse. Served again later (the service
        worker's copy of the page), it must not start once the visitor has
        withdrawn analytics since the render."""
        html = self._render({"cookie_consent_analytics": "accept"})

        guard = "if (!%s) {" % ANALYTICS_DECLINED_JS
        self.assertIn(guard, html)
        self.assertLess(html.index(guard), html.index("!(function (cfg)"))
        self.assertLess(html.index(guard), html.index("InstrumentationKey=abc"))
        # The placeholder only loads the SDK on a grant: it stays as it was.
        for placeholder in (
            self._render({}),
            self._render({"cookie_consent_analytics": "decline"}),
        ):
            self.assertNotIn("cookie_consent_analytics=decline", placeholder)

    def test_banner_silences_a_loaded_sdk_on_withdrawal(self):
        html = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
        self.assertIn(
            "window.appInsights.config.disableTelemetry = consent.analytics !== true;",
            html,
        )
        self.assertIn(
            "window.appInsights.config.disableCookiesUsage = consent.analytics !== true;",
            html,
        )
        self.assertIn("clearAppInsightsCookies();", html)
        self.assertIn("['ai_user', 'ai_session']", html)
        self.assertIn("keepalive: true", html)

    def test_event_tag_queues_until_the_sdk_is_available(self):
        html = Template(
            '{% load analytics %}{% appinsights_event "dashboard_viewed" approved=True %}'
        ).render(
            Context({"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc"})
        )

        self.assertIn("window.__appInsightsPendingEvents =", html)
        self.assertIn("window.__appInsightsPendingEvents.push(event)", html)
        self.assertIn('name: "dashboard_viewed"', html)

    def test_consent_placeholder_flushes_queued_events_to_the_sdk(self):
        html = self._render({})

        self.assertIn("function flushPendingEvents()", html)
        self.assertIn("window.appInsights.trackEvent(pending.shift())", html)
        self.assertIn("flushPendingEvents();", html)


class AppInsightsCookieRegistrationTests(TestCase):
    def test_setup_command_registers_sdk_cookies_for_native_cleanup(self):
        from io import StringIO

        from django.core.management import call_command

        from cookie_consent.models import Cookie

        call_command("setup_cookie_groups", stdout=StringIO())

        self.assertEqual(
            set(
                Cookie.objects.filter(name__in=("ai_user", "ai_session")).values_list(
                    "name", flat=True
                )
            ),
            {"ai_user", "ai_session"},
        )


class SetupCookieGroupsCommandTests(TestCase):
    """startup.sh runs setup_cookie_groups on every container start (Codex P2
    on #1028: nothing else puts the ai_user/ai_session rows into a deployed
    database, and the library's own decline only deletes registered cookies).
    So the command must change nothing on a re-run (a moved group version
    asks every visitor again), survive the rows an admin may have added, and
    leave the library's cached groups fresh."""

    def setUp(self):
        from cookie_consent.cache import delete_cache

        cache.clear()
        delete_cache()

    def _run(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("setup_cookie_groups", stdout=out)
        return out.getvalue()

    def _versions(self):
        from cookie_consent.cache import delete_cache, get_cookie_group

        delete_cache()
        return {
            g: get_cookie_group(g).get_version() for g in ("analytics", "marketing")
        }

    def test_a_second_run_changes_nothing(self):
        from cookie_consent.models import Cookie, CookieGroup

        def snapshot():
            return (
                list(CookieGroup.objects.order_by("pk").values_list("pk", "varname")),
                list(
                    Cookie.objects.order_by("pk").values_list(
                        "pk", "cookiegroup_id", "name", "domain", "created"
                    )
                ),
            )

        self._run()
        before, versions = snapshot(), self._versions()
        self._run()

        self.assertEqual(snapshot(), before)
        self.assertEqual(self._versions(), versions)

    def test_the_run_ends_by_clearing_the_library_cache(self):
        """Each save clears the cache inside get_or_create's transaction,
        before the commit, so a request served meanwhile can cache the groups
        without the new row for an hour. The command clears it again once
        every write has committed, even when it wrote nothing."""
        from cookie_consent.cache import CACHE_KEY, _get_cache

        self._run()
        _get_cache().set(CACHE_KEY, {"stale": "snapshot"})
        self._run()  # every row exists: no save() clears the cache this time

        self.assertIsNone(_get_cache().get(CACHE_KEY))

    def test_a_same_name_row_on_another_domain_does_not_stop_the_run(self):
        """Cookies are looked up by (group, name), so an admin-added row that
        differs only in domain would make get_or_create raise; the command
        leaves those rows alone and still registers the rest."""
        from cookie_consent.models import Cookie, CookieGroup

        analytics = CookieGroup.objects.create(varname="analytics", name="Analytics")
        Cookie.objects.create(cookiegroup=analytics, name="_ga", domain="")
        Cookie.objects.create(cookiegroup=analytics, name="_ga", domain=".crush.lu")

        output = self._run()

        self.assertIn("Several analytics cookies named _ga", output)
        self.assertEqual(
            Cookie.objects.filter(cookiegroup=analytics, name="_ga").count(), 2
        )
        self.assertTrue(
            Cookie.objects.filter(cookiegroup=analytics, name="ai_user").exists()
        )
        self.assertTrue(Cookie.objects.filter(name="fr").exists())

    def _native_decline(self):
        client = Client()
        client.cookies["ai_user"] = "visitor-id"
        client.cookies["ai_session"] = "session-id"
        return client.post(
            "/cookies/decline/",
            {"cookie_groups": "analytics"},
            HTTP_HOST="crush.lu",
            HTTP_X_COOKIE_CONSENT_FETCH="1",
        )

    def test_native_decline_deletes_the_app_insights_cookies(self):
        from cookie_consent.cache import delete_cache
        from cookie_consent.models import Cookie, CookieGroup

        # Control: a group without the rows leaves them in the browser.
        analytics = CookieGroup.objects.create(varname="analytics", name="Analytics")
        Cookie.objects.create(cookiegroup=analytics, name="_ga", domain="")
        delete_cache()
        response = self._native_decline()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("ai_user", response.cookies)

        self._run()
        response = self._native_decline()

        self.assertEqual(response.status_code, 200)
        for name in ("ai_user", "ai_session"):
            self.assertIn(name, response.cookies)
            self.assertEqual(response.cookies[name]["max-age"], 0)


class StartupSeedsCookieGroupsTests(SimpleTestCase):
    """Nothing in CI runs startup.sh, so its seeding step is checked where it
    is written: after migrate, before Gunicorn, at the top level (every start,
    both slots), and unable to abort startup under `set -e`."""

    def _script(self):
        from pathlib import Path

        return (Path(__file__).resolve().parents[2] / "startup.sh").read_text(
            encoding="utf-8"
        )

    def test_startup_runs_setup_cookie_groups_after_migrate(self):
        script = self._script()
        lines = script.splitlines()

        def first(prefix):
            return next(i for i, line in enumerate(lines) if line.startswith(prefix))

        seeds = [
            i
            for i, line in enumerate(lines)
            if "manage.py setup_cookie_groups" in line and not line.startswith("#")
        ]
        self.assertEqual(len(seeds), 1, seeds)
        seed = lines[seeds[0]]
        # Unindented: not inside an `if` that would skip it on some starts.
        self.assertTrue(seed.startswith("$PYTHON manage.py setup_cookie_groups"))
        self.assertLess(first("$PYTHON manage.py migrate --no-input"), seeds[0])
        self.assertLess(seeds[0], first("gunicorn "))
        # startup.sh runs under `set -e`: a failure must be caught on the line.
        self.assertIn("set -e", script)
        self.assertRegex(seed, r"\|\| echo ")

    def test_startup_script_parses(self):
        import shutil
        import subprocess
        from pathlib import Path

        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not available")
        startup = Path(__file__).resolve().parents[2] / "startup.sh"
        result = subprocess.run(
            [bash, "-n", str(startup)], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class CookieConsentFlagSyncTests(TestCase):
    """A choice made through django-cookie-consent's own /cookies/ forms must
    not lose to a stale banner flag: the middleware writes the same choice into
    the flags the server reads first."""

    def setUp(self):
        from cookie_consent.cache import delete_cache, get_cookie_group
        from cookie_consent.models import Cookie, CookieGroup

        cache.clear()
        analytics, _ = CookieGroup.objects.get_or_create(
            varname="analytics", defaults={"name": "Analytics"}
        )
        CookieGroup.objects.get_or_create(
            varname="marketing", defaults={"name": "Marketing"}
        )
        Cookie.objects.get_or_create(cookiegroup=analytics, name="_ga", domain="")
        delete_cache()
        self.version = get_cookie_group("analytics").get_version()
        self.assertTrue(self.version)

    def _post(self, action, data):
        client = Client()
        client.cookies["cookie_consent_marketing"] = "accept"
        return client.post(
            f"/cookies/{action}/",
            data,
            HTTP_HOST="crush.lu",
            HTTP_X_COOKIE_CONSENT_FETCH="1",
        )

    def test_native_decline_updates_the_banner_flag(self):
        response = self._post("decline", {"cookie_groups": "marketing"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("cookie_consent", response.cookies)  # the library wrote its own
        self.assertEqual(response.cookies["cookie_consent_marketing"].value, "decline")
        self.assertNotIn("cookie_consent_analytics", response.cookies)

    def test_native_accept_all_updates_every_flag(self):
        """The flag carries the group version the library stored, so it goes
        stale exactly when the library's own cookie would (a group without
        cookies has the empty version, as in the library's cookie)."""
        response = self._post("accept", {"all_groups": "on"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.cookies["cookie_consent_analytics"].value, f"accept:{self.version}"
        )
        self.assertEqual(response.cookies["cookie_consent_marketing"].value, "accept:")

    def test_a_rejected_form_leaves_the_flags_alone(self):
        response = self._post("decline", {"cookie_groups": "nonexistent"})

        self.assertNotIn("cookie_consent", response.cookies)
        self.assertNotIn("cookie_consent_marketing", response.cookies)


class GoogleConsentDefaultsTests(SimpleTestCase):
    """The Consent Mode defaults GA4 boots with must come from the same
    version-checked reading of the stored choice as everything else. Read the
    library's HttpOnly cookie alone and a stale acceptance in it outranks a
    newer refusal the banner recorded while its post to the library was lost,
    and gtag('config') sends the page view with analytics_storage granted."""

    def setUp(self):
        _pin_versions(self)

    def _render(self, cookies=None, library=None, with_request=True):
        context = {"GOOGLE_ANALYTICS_GTAG_PROPERTY_ID": "G-TEST"}
        if with_request:
            request = RequestFactory().get("/")
            request.COOKIES.update(cookies or {})
            context["request"] = request
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=library
        ):
            return Template("{% load analytics %}{% analytics_head %}").render(
                Context(context)
            )

    def _defaults(self, rendered):
        block = rendered[
            rendered.index("gtag('consent', 'default', {") : rendered.index(
                "'wait_for_update'"
            )
        ]
        return dict(re.findall(r"'(\w+)': '(granted|denied)'", block))

    def test_undecided_and_no_request_are_denied(self):
        for rendered in (self._render({}), self._render(with_request=False)):
            defaults = self._defaults(rendered)
            self.assertEqual(len(defaults), 4)
            self.assertEqual(set(defaults.values()), {"denied"})

    def test_a_banner_refusal_outranks_a_stale_library_acceptance(self):
        rendered = self._render(
            {
                "cookie_consent_analytics": "decline",
                "cookie_consent_marketing": "decline",
            },
            library=True,
        )

        self.assertEqual(set(self._defaults(rendered).values()), {"denied"})

    def test_a_current_acceptance_grants_per_group(self):
        rendered = self._render(
            {
                "cookie_consent_analytics": "accept:",
                "cookie_consent_marketing": "decline",
            }
        )

        defaults = self._defaults(rendered)
        self.assertEqual(defaults["analytics_storage"], "granted")
        self.assertEqual(defaults["ad_storage"], "denied")
        self.assertEqual(defaults["ad_user_data"], "denied")
        self.assertEqual(defaults["ad_personalization"], "denied")

    def test_the_library_cookie_alone_still_counts(self):
        rendered = self._render({}, library=True)

        self.assertEqual(set(self._defaults(rendered).values()), {"granted"})

    def test_a_granted_default_yields_to_a_later_refusal_in_the_browser(self):
        """A granted default is only true for this response. Served again
        later (the service worker's copy of the page), a refusal recorded
        since must turn the group back to denied before gtag('config') sends
        the page view. The defaults themselves stay as rendered."""
        rendered = self._render(
            {
                "cookie_consent_analytics": "accept:",
                "cookie_consent_marketing": "accept:",
            }
        )

        self.assertEqual(set(self._defaults(rendered).values()), {"granted"})
        updates = (
            "if (%s) gtag('consent', 'update', {'analytics_storage': 'denied'});"
            % ANALYTICS_DECLINED_JS,
            "if (%s) gtag('consent', 'update', {'ad_storage': 'denied', "
            "'ad_user_data': 'denied', 'ad_personalization': 'denied'});"
            % MARKETING_DECLINED_JS,
        )
        for update in updates:
            self.assertIn(update, rendered)
            self.assertLess(
                rendered.index("gtag('consent', 'default'"), rendered.index(update)
            )
            self.assertLess(rendered.index(update), rendered.index("gtag('config'"))

    def test_only_a_granted_group_gets_the_refusal_check(self):
        """A denied default has nothing to take back: a live acceptance never
        outranks the server's refusal or its "ask again"."""
        mixed = self._render(
            {
                "cookie_consent_analytics": "accept:",
                "cookie_consent_marketing": "decline",
            }
        )
        self.assertIn(ANALYTICS_DECLINED_JS, mixed)
        self.assertNotIn("cookie_consent_marketing=decline", mixed)
        for denied in (self._render({}), self._render(with_request=False)):
            self.assertNotIn("=decline", denied)
            self.assertNotIn("gtag('consent', 'update'", denied)


class ConsentVersionTests(TestCase):
    """django-cookie-consent dates a group by its newest cookie and treats an
    acceptance recorded before that date as undecided, so adding a cookie to a
    group asks everyone again. The banner's own cookies follow the same rule:
    the flag carries the group version it was accepted under, the banner JSON
    its own date, and a stale acceptance is undecided, never a refusal."""

    def setUp(self):
        from cookie_consent.cache import get_cookie_group
        from cookie_consent.models import CookieGroup

        cache.clear()
        self.analytics, _ = CookieGroup.objects.get_or_create(
            varname="analytics", defaults={"name": "Analytics"}
        )
        CookieGroup.objects.get_or_create(
            varname="marketing", defaults={"name": "Marketing"}
        )
        self._add_cookie("_ga", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.version = get_cookie_group("analytics").get_version()
        self.assertTrue(self.version)

    def _add_cookie(self, name, created):
        from cookie_consent.cache import delete_cache
        from cookie_consent.models import Cookie

        cookie = Cookie.objects.create(cookiegroup=self.analytics, name=name, domain="")
        # auto_now_add ignores a value passed to create().
        Cookie.objects.filter(pk=cookie.pk).update(created=created)
        delete_cache()

    def _choice(self, cookies, group="analytics"):
        from azureproject.templatetags.analytics import stored_cookie_choice

        request = RequestFactory().get("/")
        request.COOKIES.update(cookies)
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=None
        ):
            return stored_cookie_choice(request, group)

    def test_a_current_acceptance_counts(self):
        self.assertIs(
            self._choice({"cookie_consent_analytics": f"accept:{self.version}"}), True
        )

    def test_adding_a_cookie_makes_an_older_acceptance_undecided(self):
        from cookie_consent.cache import get_cookie_group

        old = f"accept:{self.version}"
        self._add_cookie("_ga_NEW", datetime(2026, 6, 1, tzinfo=timezone.utc))
        new_version = get_cookie_group("analytics").get_version()
        self.assertGreater(new_version, self.version)

        self.assertIsNone(self._choice({"cookie_consent_analytics": old}))
        self.assertIs(
            self._choice({"cookie_consent_analytics": f"accept:{new_version}"}), True
        )

    def test_a_flag_without_a_version_falls_back_to_the_banner_json_date(self):
        """What visitors have today: a bare "accept" flag (written before flags
        carried a version) beside the banner JSON with its own timestamp, in
        the browser's ISO shape."""
        after = (
            '{"essential":true,"analytics":true,"marketing":true,'
            '"timestamp":"2026-03-01T12:00:00.000Z"}'
        )
        before = (
            '{"essential":true,"analytics":true,"marketing":true,'
            '"timestamp":"2025-12-01T12:00:00.000Z"}'
        )

        self.assertIs(
            self._choice(
                {"cookie_consent_analytics": "accept", "cookie_consent": after}
            ),
            True,
        )
        self.assertIsNone(
            self._choice(
                {"cookie_consent_analytics": "accept", "cookie_consent": before}
            )
        )
        self.assertIsNone(self._choice({"cookie_consent_analytics": "accept"}))

    def test_a_refusal_never_goes_stale(self):
        self._add_cookie("_ga_NEW", datetime(2026, 6, 1, tzinfo=timezone.utc))

        self.assertIs(self._choice({"cookie_consent_analytics": "decline"}), False)
        self.assertIs(
            self._choice(
                {
                    "cookie_consent": (
                        '{"analytics":false,"timestamp":"2025-01-01T00:00:00.000Z"}'
                    )
                }
            ),
            False,
        )

    def test_a_group_without_cookies_has_nothing_to_renew(self):
        for flag in ("accept:", "accept"):
            self.assertIs(
                self._choice({"cookie_consent_marketing": flag}, group="marketing"),
                True,
                flag,
            )

    def test_dates_are_compared_as_datetimes_not_strings(self):
        """The banner's script writes "...123Z", the library "...123456+00:00":
        as strings "Z" sorts above any digit, so a string compare would call an
        acceptance 456 microseconds too old current."""
        from azureproject.templatetags.analytics import _stamp_is_current

        self.assertFalse(
            _stamp_is_current(
                "2026-01-01T00:00:00.123Z", "2026-01-01T00:00:00.123456+00:00"
            )
        )
        self.assertTrue(
            _stamp_is_current(
                "2026-01-01T00:00:00.124Z", "2026-01-01T00:00:00.123456+00:00"
            )
        )
        self.assertTrue(_stamp_is_current("", ""))
        self.assertFalse(_stamp_is_current("", "2026-01-01T00:00:00+00:00"))

    def test_one_stale_group_makes_the_state_undecided(self):
        """A cookie added to the analytics group only: the analytics
        acceptance is stale, marketing's stays current, and the state is not
        decided, so the banner asks again for analytics."""
        import json

        old = f"accept:{self.version}"
        self._add_cookie("_ga_NEW", datetime(2026, 6, 1, tzinfo=timezone.utc))
        request = RequestFactory().get("/")
        request.COOKIES.update(
            {"cookie_consent_analytics": old, "cookie_consent_marketing": "accept:"}
        )
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=None
        ):
            rendered = Template(
                "{% load analytics %}{% cookie_consent_state %}"
            ).render(Context({"request": request}))

        state = json.loads(html.unescape(rendered))
        self.assertIsNone(state["analytics"])
        self.assertIs(state["marketing"], True)
        self.assertIs(state["decided"], False)

    def test_the_state_tag_carries_the_current_versions(self):
        import json

        request = RequestFactory().get("/")
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=None
        ):
            rendered = Template(
                "{% load analytics %}{% cookie_consent_state %}"
            ).render(Context({"request": request}))

        state = json.loads(html.unescape(rendered))
        self.assertEqual(
            state["versions"], {"analytics": self.version, "marketing": ""}
        )

    def test_the_library_form_writes_the_version_into_the_flag(self):
        client = Client()
        response = client.post(
            "/cookies/accept/",
            {"cookie_groups": "analytics"},
            HTTP_HOST="crush.lu",
            HTTP_X_COOKIE_CONSENT_FETCH="1",
        )

        self.assertEqual(response.status_code, 200)
        flag = response.cookies["cookie_consent_analytics"].value
        self.assertEqual(flag, f"accept:{self.version}")
        # ...and that flag is current for the server on the next request.
        self.assertIs(self._choice({"cookie_consent_analytics": flag}), True)

        response = client.post(
            "/cookies/decline/",
            {"cookie_groups": "analytics"},
            HTTP_HOST="crush.lu",
            HTTP_X_COOKIE_CONSENT_FETCH="1",
        )

        self.assertEqual(response.cookies["cookie_consent_analytics"].value, "decline")


class ConsentStateTagTests(SimpleTestCase):
    """The banner reads the server's view of the stored choice from
    data-consent-state, because the library's own cookie is HttpOnly."""

    def setUp(self):
        _pin_versions(self)

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
        return json.loads(html.unescape(rendered)) if rendered else None

    def test_undecided_without_a_request_or_a_cookie(self):
        # Without a request the server has no view to offer: empty, so the
        # banner's script falls back to the cookie it can read.
        self.assertIsNone(self._state(with_request=False))
        self.assertEqual(
            self._state({}),
            {
                "analytics": None,
                "marketing": None,
                "decided": False,
                "versions": NO_VERSIONS,
            },
        )

    def test_reflects_the_banner_json(self):
        self.assertEqual(
            self._state({"cookie_consent": '{"analytics":true,"marketing":false}'}),
            {
                "analytics": True,
                "marketing": False,
                "decided": True,
                "versions": NO_VERSIONS,
            },
        )

    def test_one_undecided_group_is_not_decided(self):
        """One group's acceptance can go stale while the other stays current
        (a cookie was added to that group only): the banner must ask again
        for it, so the state counts as decided only when every group holds a
        choice."""
        self.assertEqual(
            self._state({"cookie_consent_analytics": "accept:"}),
            {
                "analytics": True,
                "marketing": None,
                "decided": False,
                "versions": NO_VERSIONS,
            },
        )
        self.assertEqual(
            self._state(
                {
                    "cookie_consent_analytics": "decline",
                    "cookie_consent_marketing": "accept:",
                }
            )["decided"],
            True,
        )

    def test_carries_the_groups_current_versions(self):
        with patch(
            VERSION_SEAM,
            side_effect=lambda group: {"analytics": "2026-01-01T00:00:00+00:00"}.get(
                group
            ),
        ):
            state = self._state({})
        self.assertEqual(
            state["versions"],
            {"analytics": "2026-01-01T00:00:00+00:00", "marketing": ""},
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
            {
                "analytics": False,
                "marketing": True,
                "decided": True,
                "versions": NO_VERSIONS,
            },
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
        import json

        request = RequestFactory().get("/")
        request.COOKIES["cookie_consent"] = '{"analytics":false,"marketing":true}'
        with patch(
            "cookie_consent.util.get_cookie_value_from_request", return_value=None
        ):
            rendered = render_to_string(BANNER_TEMPLATE, {"request": request})
        attribute = re.search(r'data-consent-state="([^"]*)"', rendered).group(1)
        self.assertEqual(
            json.loads(html.unescape(attribute)),
            {
                "analytics": False,
                "marketing": True,
                "decided": True,
                "versions": NO_VERSIONS,
            },
        )
        self.assertIn("function serverState()", rendered)
        # On load the server's (version-checked) state wins over a readable
        # cookie: undecided server-side shows the banner whatever the cookie
        # says; a decision is dispatched to the analytics scripts and put into
        # Google Consent Mode.
        choice = _js_function_body(rendered, "choiceOnLoad")
        self.assertIn("const server = serverState();", choice)
        self.assertIn(
            "if (server) return server.decided ? storedConsent() : null;", choice
        )
        load = rendered[
            rendered.index("document.addEventListener('DOMContentLoaded'") :
        ]
        self.assertIn("const stored = choiceOnLoad();", load)
        self.assertIn("dispatchConsentEvent(stored);", load)
        self.assertIn("updateGoogleConsent(stored);", load)
        self.assertLess(
            load.index("dispatchConsentEvent(stored);"),
            load.index("updateGoogleConsent(stored);"),
        )
        # Rendered without a request the attribute is empty: the script falls
        # back to the cookie it can read.
        self.assertIn('data-consent-state=""', render_to_string(BANNER_TEMPLATE, {}))

    def test_saving_writes_a_versioned_flag(self):
        """An acceptance flag carries the group version from data-consent-state
        (the save's own date when none is known), and the state rewritten
        after a save keeps the versions for a second save on the same page."""
        rendered = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
        flag = rendered[
            rendered.index("function setDjangoCookieConsent(") : rendered.index(
                "const NATIVE_STATUS_URL"
            )
        ]
        self.assertIn(
            "'accept:' + (serverVersions()[groupName] || new Date().toISOString())",
            flag,
        )
        self.assertIn(": 'decline'", flag)
        sync = rendered[
            rendered.index("function syncServerState(") : rendered.index(
                "function acceptAllCookies()"
            )
        ]
        self.assertIn("versions: serverVersions()", sync)
        # The modal reflects the server's choice group by group: a stale
        # acceptance (null server-side) is unticked, the other group's current
        # choice stays ticked, whether or not the whole state is decided.
        stored = _js_function_body(rendered, "storedConsent")
        self.assertIn("analytics: server.analytics === true", stored)
        self.assertNotIn("server.decided", stored)


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

    def test_entreprinder_account_pages_carry_the_banner_and_the_trigger(self):
        """Entreprinder's signup and email-confirmation pages extend their own
        account base, which carries the gated analytics tags: without the
        banner there a visitor landing on them could never choose."""
        # Rendered directly: which URL serves it depends on allauth's template
        # resolution order (crush_lu's account/ templates shadow this base on
        # the shared routes), and the base is what those pages inherit.
        request = RequestFactory().get("/", HTTP_HOST="entreprinder.lu")
        html = render_to_string("account/base_account.html", {"request": request})

        self.assertIn('id="cookie-consent-banner"', html)
        self.assertIn("data-cookie-settings", html)
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
def test_appinsights_event_waits_for_consent_then_replays(page):
    """Page events rendered before opt-in must be buffered in memory and
    delivered when the consent listener can hand them to the initialized SDK."""
    head = Template("{% load analytics %}{% appinsights_head %}").render(
        Context({"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc"})
    )
    event = Template(
        '{% load analytics %}{% appinsights_event "dashboard_viewed" approved=True %}'
    ).render(
        Context({"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc"})
    )
    banner = render_to_string(BANNER_TEMPLATE, {"cookie_banner_variant": "crush"})
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body="<!doctype html><html><head>%s</head><body>%s%s</body></html>"
            % (head, event, banner),
        ),
    )
    page.route(
        "**/cookies/**",
        lambda route: route.fulfill(
            content_type="application/json",
            body='{"csrftoken":"tok","acceptUrl":"/cookies/accept/","declineUrl":"/cookies/decline/"}',
        ),
    )
    page.add_init_script("window.__deliveredAppInsights = [];")

    page.goto(url)
    assert page.evaluate("window.__appInsightsPendingEvents") == [
        {"name": "dashboard_viewed", "properties": {"approved": True}}
    ]
    assert page.evaluate("typeof window.appInsights") == "undefined"

    # Stand in for the SDK queueing stub installed by the consent-triggered
    # loader. The custom event must pass through trackEvent only after opt-in.
    page.evaluate(
        "window.appInsights = { config: {}, trackEvent: function (event) { "
        "window.__deliveredAppInsights.push(event); } };"
    )
    page.click("#cookie-btn-accept")

    assert page.evaluate("window.__deliveredAppInsights") == [
        {"name": "dashboard_viewed", "properties": {"approved": True}}
    ]
    assert page.evaluate("window.__appInsightsPendingEvents") == []


@pytest.mark.playwright
def test_withdrawing_analytics_disables_sdk_and_clears_its_cookies(page):
    """Withdrawing analytics stops the SDK and expires the identifiers it had
    already written, even before the native cookie-consent POST completes."""
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
    page.route(
        "**/cookies/**",
        lambda route: route.fulfill(
            content_type="application/json",
            body='{"csrftoken":"tok","acceptUrl":"/cookies/accept/","declineUrl":"/cookies/decline/"}',
        ),
    )
    page.context.add_cookies(
        [
            {
                "name": "cookie_consent",
                "value": '{"essential":true,"analytics":true,"marketing":false}',
                "url": url,
            },
            {"name": "ai_user", "value": "visitor-id", "url": url},
            {"name": "ai_session", "value": "session-id", "url": url},
        ]
    )
    page.add_init_script(
        "window.appInsights = { config: { disableTelemetry: false, "
        "disableCookiesUsage: false } };"
    )

    page.goto(url)
    page.click("#open-cookie-settings")
    assert page.is_checked("#cookie-analytics")
    page.click("label.cookie-toggle:has(#cookie-analytics)")
    page.click("#cookie-btn-save")

    assert page.evaluate("window.appInsights.config.disableTelemetry") is True
    assert page.evaluate("window.appInsights.config.disableCookiesUsage") is True
    names = {cookie["name"] for cookie in page.context.cookies()}
    assert "ai_user" not in names
    assert "ai_session" not in names


@pytest.mark.playwright
def test_server_side_choice_drives_the_banner_when_the_cookie_is_httponly(page):
    """A choice stored only where document.cookie cannot see it (the
    library's HttpOnly cookie) must still keep the banner closed and show up
    in the reopened settings: the page reads data-consent-state instead."""
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent"] = (
        '{"essential":true,"analytics":false,"marketing":true}'
    )
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(VERSION_SEAM, return_value=""):
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
    page.route("**/cookies/**", lambda route: route.fulfill(status=200, body=""))
    page.add_init_script(
        "window.__gtagCalls = [];"
        "window.gtag = function () { window.__gtagCalls.push([].slice.call(arguments)); };"
    )
    # No cookie at all in the browser: the server-rendered state is the only source.
    page.goto(url)
    assert (
        page.evaluate(
            "getComputedStyle(document.getElementById('cookie-consent-banner')).display"
        )
        == "none"
    )
    # On load the stored choice is put into Google Consent Mode as well.
    updates = [
        c for c in page.evaluate("window.__gtagCalls") if c[:2] == ["consent", "update"]
    ]
    assert updates and updates[-1][2]["analytics_storage"] == "denied"
    assert updates[-1][2]["ad_storage"] == "granted"
    page.click("#open-cookie-settings")
    checked = page.evaluate(
        "[document.getElementById('cookie-analytics').checked, "
        "document.getElementById('cookie-marketing').checked]"
    )
    assert checked == [False, True]
    # Saving writes the flags the server reads first: a refusal as "decline",
    # an acceptance dated (no group version was known here, so the save's own
    # date, in the browser's ISO shape).
    page.click("#cookie-btn-save")
    flags = {c["name"]: c["value"] for c in page.context.cookies()}
    assert flags["cookie_consent_analytics"] == "decline"
    assert re.fullmatch(
        r"accept:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z",
        flags["cookie_consent_marketing"],
    )


@pytest.mark.playwright
def test_one_stale_group_reopens_the_banner_and_keeps_the_other(page):
    """A cookie was added to the marketing group only: its acceptance is
    stale, analytics' is current. The banner must ask again, the modal keeps
    analytics ticked and marketing off, and a new acceptance dates each group
    with its own current version."""
    versions = {
        "analytics": "2026-01-01T00:00:00+00:00",
        "marketing": "2026-06-01T00:00:00+00:00",
    }
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent_analytics"] = "accept:2026-01-01T00:00:00+00:00"
    request.COOKIES["cookie_consent_marketing"] = "accept:2026-01-01T00:00:00+00:00"
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(VERSION_SEAM, side_effect=versions.get):
        html = render_to_string(
            BANNER_TEMPLATE, {"cookie_banner_variant": "crush", "request": request}
        )
    assert "&quot;decided&quot;: false" in html
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
        "window.__consentEvents = [];"
        "document.addEventListener('cookie_consent_updated', function (e) {"
        "  window.__consentEvents.push(e.detail);"
        "});"
    )
    page.goto(url)
    assert (
        page.evaluate(
            "getComputedStyle(document.getElementById('cookie-consent-banner')).display"
        )
        == "block"
    )
    assert page.evaluate("window.__consentEvents") == []
    page.click("#open-cookie-settings")
    checked = page.evaluate(
        "[document.getElementById('cookie-analytics').checked, "
        "document.getElementById('cookie-marketing').checked]"
    )
    assert checked == [True, False]
    page.click("#cookie-modal-close")
    page.click("#cookie-btn-accept")
    flags = {c["name"]: c["value"] for c in page.context.cookies()}
    assert flags["cookie_consent_analytics"] == "accept:" + versions["analytics"]
    assert flags["cookie_consent_marketing"] == "accept:" + versions["marketing"]


@pytest.mark.playwright
def test_a_stale_acceptance_reopens_the_banner(page):
    """After a cookie is added to a group, django-cookie-consent treats an
    older acceptance as undecided. The server applies that rule to the
    banner's cookies too (data-consent-state), and on load the page must
    follow the server, not the readable cookies that still say accepted: the
    banner shows, nothing is granted, the modal pre-ticks nothing, and a new
    acceptance is dated with the group's current version."""
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent_analytics"] = "accept:2026-01-01T00:00:00+00:00"
    request.COOKIES["cookie_consent_marketing"] = "accept:2026-01-01T00:00:00+00:00"
    current = "2026-06-01T00:00:00+00:00"
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(VERSION_SEAM, return_value=current):
        html = render_to_string(
            BANNER_TEMPLATE, {"cookie_banner_variant": "crush", "request": request}
        )
    assert "&quot;decided&quot;: false" in html
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
    page.route("**/cookies/**", lambda route: route.fulfill(status=200, body=""))
    # The browser still holds the old acceptance in every readable form.
    page.context.add_cookies(
        [
            {
                "name": "cookie_consent",
                "value": '{"essential":true,"analytics":true,"marketing":true}',
                "url": url,
            },
            {
                "name": "cookie_consent_analytics",
                "value": "accept:2026-01-01T00:00:00+00:00",
                "url": url,
            },
            {
                "name": "cookie_consent_marketing",
                "value": "accept:2026-01-01T00:00:00+00:00",
                "url": url,
            },
        ]
    )
    page.add_init_script(
        "window.__consentEvents = [];"
        "document.addEventListener('cookie_consent_updated', function (e) {"
        "  window.__consentEvents.push(e.detail);"
        "});"
        "window.__fbqCalls = [];"
        "window.fbq = function () { window.__fbqCalls.push([].slice.call(arguments)); };"
    )
    page.goto(url)
    assert (
        page.evaluate(
            "getComputedStyle(document.getElementById('cookie-consent-banner')).display"
        )
        == "block"
    )
    assert page.evaluate("window.__consentEvents") == []
    assert page.evaluate("window.__fbqCalls") == []
    page.click("#open-cookie-settings")
    checked = page.evaluate(
        "[document.getElementById('cookie-analytics').checked, "
        "document.getElementById('cookie-marketing').checked]"
    )
    assert checked == [False, False]
    page.click("#cookie-modal-close")
    page.click("#cookie-btn-accept")
    flags = {c["name"]: c["value"] for c in page.context.cookies()}
    assert flags["cookie_consent_analytics"] == "accept:" + current
    assert flags["cookie_consent_marketing"] == "accept:" + current
    assert page.evaluate("window.__consentEvents")[-1]["analytics"] is True


def _cached_accepted_page():
    """The whole consent surface of a page rendered for a visitor who had
    accepted both groups: the copy the service worker keeps for offline use
    (crush-pages for a day, crush-tickets-v2 for a year)."""
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent_analytics"] = "accept:"
    request.COOKIES["cookie_consent_marketing"] = "accept:"
    context = {
        "request": request,
        "GOOGLE_ANALYTICS_GTAG_PROPERTY_ID": "G-TEST123",
        "FACEBOOK_PIXEL_ID": "999",
        "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc",
        "cookie_banner_variant": "crush",
    }
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(VERSION_SEAM, return_value=""):
        head = Template(
            "{% load analytics %}{% analytics_head %}{% appinsights_head %}"
        ).render(Context(context))
        body = Template("{% load analytics %}{% analytics_body %}").render(
            Context(context)
        )
        banner = render_to_string(BANNER_TEMPLATE, context)
    # The granted branches, not the placeholders.
    assert "fbq('init', '999')" in body and "waiting for" not in head + body
    assert "&quot;decided&quot;: true" in banner
    footer = (
        '<a href="#" data-cookie-settings id="open-cookie-settings">Cookie Settings</a>'
    )
    return "<!doctype html><html><head>%s</head><body>%s%s%s</body></html>" % (
        head,
        body,
        footer,
        banner,
    )


def _serve_kept_copy(page, html, url):
    page.route(
        url,
        lambda route: route.fulfill(content_type="text/html", body=html),
    )
    page.route("**/cookies/**", lambda route: route.fulfill(status=200, body=""))
    # The trackers' own scripts: never fetched, whatever the page decides.
    for pattern in (
        "https://www.googletagmanager.com/**",
        "https://connect.facebook.net/**",
        "https://js.monitor.azure.com/**",
    ):
        page.route(pattern, lambda route: route.abort())


def _data_layer(page):
    return page.evaluate(
        "window.dataLayer.map(function (a) { return Array.prototype.slice.call(a); })"
    )


@pytest.mark.playwright
def test_a_cached_accepted_page_honours_a_later_refusal(page):
    """Codex P1 on #1028: the visitor accepted both groups, the worker kept
    the page, the visitor then withdrew both (flags now "decline"), and the
    worker serves the kept copy (offline, or a slow network at the venue door
    for a ticket). Nothing on that copy may track or re-grant: GA4 must be
    denied before its page view, the Pixel and App Insights must not start,
    the banner must dispatch the refusal, and the reopened modal must show
    it, or Save would write a fresh acceptance over the withdrawal."""
    url = "http://crush.test/"
    _serve_kept_copy(page, _cached_accepted_page(), url)
    page.context.add_cookies(
        [
            {"name": "cookie_consent_analytics", "value": "decline", "url": url},
            {"name": "cookie_consent_marketing", "value": "decline", "url": url},
            {
                "name": "cookie_consent",
                "value": '{"essential":true,"analytics":false,"marketing":false}',
                "url": url,
            },
        ]
    )
    page.add_init_script(
        "window.__consentEvents = [];"
        "document.addEventListener('cookie_consent_updated', function (e) {"
        "  window.__consentEvents.push(e.detail);"
        "});"
    )
    page.goto(url)

    calls = _data_layer(page)
    commands = [c[:2] for c in calls]
    config = commands.index(["config", "G-TEST123"])
    # The kept copy's default still says granted (it is the rendered HTML)...
    default = calls[commands.index(["consent", "default"])][2]
    assert default["analytics_storage"] == "granted"
    # ...but both groups are denied again before the page view is configured.
    before_config = [c[2] for c in calls[:config] if c[:2] == ["consent", "update"]]
    assert {"analytics_storage": "denied"} in before_config
    assert {
        "ad_storage": "denied",
        "ad_user_data": "denied",
        "ad_personalization": "denied",
    } in before_config
    # The banner's own update on load says denied too.
    last_update = [c[2] for c in calls if c[:2] == ["consent", "update"]][-1]
    assert last_update["analytics_storage"] == "denied"
    assert last_update["ad_storage"] == "denied"

    assert page.evaluate("typeof window.fbq") == "undefined"
    assert page.evaluate("typeof window.appInsights") == "undefined"
    events = page.evaluate("window.__consentEvents")
    assert events, "the decided state must still be dispatched"
    assert all(e["analytics"] is False and e["marketing"] is False for e in events)

    page.click("#open-cookie-settings")
    checked = page.evaluate(
        "[document.getElementById('cookie-analytics').checked, "
        "document.getElementById('cookie-marketing').checked]"
    )
    assert checked == [False, False]


@pytest.mark.playwright
def test_the_same_cached_page_still_tracks_while_the_choice_stands(page):
    """Guards the test above: with the acceptance still in place the kept
    copy behaves like a fresh page (no refusal, nothing held back)."""
    url = "http://crush.test/"
    _serve_kept_copy(page, _cached_accepted_page(), url)
    page.context.add_cookies(
        [
            {"name": "cookie_consent_analytics", "value": "accept:", "url": url},
            {"name": "cookie_consent_marketing", "value": "accept:", "url": url},
        ]
    )
    page.goto(url)

    updates = [c[2] for c in _data_layer(page) if c[:2] == ["consent", "update"]]
    assert updates and all(set(u.values()) == {"granted"} for u in updates)
    assert page.evaluate("typeof window.fbq") == "function"
    assert page.evaluate("typeof window.appInsights") == "object"


@pytest.mark.playwright
def test_back_forward_restore_applies_a_later_refusal(page):
    """A page restored from the back/forward cache runs no load handler
    again: its trackers keep the grant they had when the visitor left it. A
    withdrawal made on another page since must reach them on pageshow."""
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent_analytics"] = "accept:"
    request.COOKIES["cookie_consent_marketing"] = "accept:"
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(VERSION_SEAM, return_value=""):
        html = render_to_string(
            BANNER_TEMPLATE, {"cookie_banner_variant": "crush", "request": request}
        )
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body="<!doctype html><html><body>%s</body></html>" % html,
        ),
    )
    page.route("**/cookies/**", lambda route: route.fulfill(status=200, body=""))
    page.context.add_cookies(
        [
            {"name": "cookie_consent_analytics", "value": "accept:", "url": url},
            {"name": "cookie_consent_marketing", "value": "accept:", "url": url},
        ]
    )
    page.add_init_script(
        "window.__consentEvents = [];"
        "document.addEventListener('cookie_consent_updated', function (e) {"
        "  window.__consentEvents.push(e.detail);"
        "});"
        "window.__fbqCalls = [];"
        "window.fbq = function () { window.__fbqCalls.push([].slice.call(arguments)); };"
        "window.appInsights = { config: { disableTelemetry: false } };"
    )
    page.goto(url)
    assert page.evaluate("window.__consentEvents") == [
        {"analytics": True, "marketing": True}
    ]

    # An ordinary pageshow (a fresh load) re-dispatches nothing.
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: false}))"
    )
    assert len(page.evaluate("window.__consentEvents")) == 1

    # Withdrawn on another page, then back here from the back/forward cache.
    page.context.add_cookies(
        [
            {"name": "cookie_consent_analytics", "value": "decline", "url": url},
            {"name": "cookie_consent_marketing", "value": "decline", "url": url},
        ]
    )
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    assert page.evaluate("window.__consentEvents")[-1] == {
        "analytics": False,
        "marketing": False,
    }
    assert page.evaluate("window.__fbqCalls")[-1] == ["consent", "revoke"]
    assert page.evaluate("window.appInsights.config.disableTelemetry") is True


def _fbq_queue(page):
    return page.evaluate(
        "window.fbq.queue.map(function (a) { return Array.prototype.slice.call(a); })"
    )


@pytest.mark.playwright
def test_back_forward_restore_revokes_a_tracker_on_a_page_still_asking(page):
    """Codex P3 on #1028: after a cookie is added to the analytics group, an
    earlier "accept all" visitor's analytics acceptance is stale while the
    marketing one is current. The page is not decided (the banner asks about
    analytics), yet it runs the full Pixel. The visitor declines everything
    on another page and comes back: the restored page must revoke the Pixel,
    not skip itself for being undecided."""
    versions = {"analytics": "2026-06-01T00:00:00+00:00", "marketing": ""}
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent_analytics"] = "accept:2026-01-01T00:00:00+00:00"
    request.COOKIES["cookie_consent_marketing"] = "accept:"
    context = {
        "request": request,
        "FACEBOOK_PIXEL_ID": "999",
        "cookie_banner_variant": "crush",
    }
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(VERSION_SEAM, side_effect=versions.get):
        body = Template("{% load analytics %}{% analytics_body %}").render(
            Context(context)
        )
        banner = render_to_string(BANNER_TEMPLATE, context)
    assert "fbq('init', '999')" in body and "waiting for" not in body
    assert "&quot;decided&quot;: false" in banner
    assert "&quot;marketing&quot;: true" in banner

    url = "http://crush.test/"
    _serve_kept_copy(
        page, "<!doctype html><html><body>%s%s</body></html>" % (body, banner), url
    )
    page.context.add_cookies(
        [
            {
                "name": "cookie_consent_analytics",
                "value": "accept:2026-01-01T00:00:00+00:00",
                "url": url,
            },
            {"name": "cookie_consent_marketing", "value": "accept:", "url": url},
        ]
    )
    page.add_init_script(
        "window.__consentEvents = [];"
        "document.addEventListener('cookie_consent_updated', function (e) {"
        "  window.__consentEvents.push(e.detail);"
        "});"
    )
    page.goto(url)
    # The banner asks; the Pixel queued its PageView under the marketing grant.
    assert page.is_visible("#cookie-consent-banner")
    assert page.evaluate("window.__consentEvents") == []
    assert ["track", "PageView"] in _fbq_queue(page)

    # "Decline all" on another page, then back here from the back/forward cache.
    page.context.add_cookies(
        [
            {"name": "cookie_consent_analytics", "value": "decline", "url": url},
            {"name": "cookie_consent_marketing", "value": "decline", "url": url},
        ]
    )
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    assert _fbq_queue(page)[-1] == ["consent", "revoke"]
    assert page.evaluate("window.__consentEvents") == [
        {"analytics": False, "marketing": False}
    ]


def _embedded_refusal_page():
    """The banner of a page rendered for a visitor who had declined both groups."""
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent_analytics"] = "decline"
    request.COOKIES["cookie_consent_marketing"] = "decline"
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(VERSION_SEAM, return_value=""):
        html = render_to_string(
            BANNER_TEMPLATE, {"cookie_banner_variant": "crush", "request": request}
        )
    assert "&quot;decided&quot;: true" in html
    footer = (
        '<a href="#" data-cookie-settings id="open-cookie-settings">Cookie Settings</a>'
    )
    return "<!doctype html><html><body>%s%s</body></html>" % (footer, html)


def _app_insights_ids(page):
    return sorted(
        c["name"]
        for c in page.context.cookies()
        if c["name"] in ("ai_user", "ai_session")
    )


@pytest.mark.playwright
def test_back_forward_restore_keeps_identifiers_set_under_a_later_acceptance(page):
    """Codex P3 on #1028: the visitor declined on this page, accepted all on
    another (whose SDK set ai_user/ai_session), then came back from the
    back/forward cache. The refusal this page embeds is older than that
    acceptance: replaying it would wipe identifiers the live choice allows."""
    url = "http://crush.test/"
    _serve_kept_copy(page, _embedded_refusal_page(), url)
    page.context.add_cookies(
        [
            {"name": "cookie_consent_analytics", "value": "decline", "url": url},
            {"name": "cookie_consent_marketing", "value": "decline", "url": url},
        ]
    )
    page.add_init_script(
        "window.__consentEvents = [];"
        "document.addEventListener('cookie_consent_updated', function (e) {"
        "  window.__consentEvents.push(e.detail);"
        "});"
    )
    page.goto(url)
    assert page.evaluate("window.__consentEvents") == [
        {"analytics": False, "marketing": False}
    ]

    page.context.add_cookies(
        [
            {"name": "cookie_consent_analytics", "value": "accept:", "url": url},
            {"name": "cookie_consent_marketing", "value": "accept:", "url": url},
            {"name": "ai_user", "value": "u1", "url": url},
            {"name": "ai_session", "value": "s1", "url": url},
        ]
    )
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    assert _app_insights_ids(page) == ["ai_session", "ai_user"]
    # Nothing replayed: the page's own refusal stands for its trackers.
    assert len(page.evaluate("window.__consentEvents")) == 1


@pytest.mark.playwright
def test_a_kept_copy_embedding_a_refusal_keeps_identifiers_of_a_later_acceptance(
    page,
):
    """The same on load: the service worker serves a copy rendered under a
    refusal after the visitor accepted elsewhere. The copy acts on its own
    refusal (under-tracking), but the identifiers stay for the live choice."""
    url = "http://crush.test/"
    _serve_kept_copy(page, _embedded_refusal_page(), url)
    page.context.add_cookies(
        [
            {"name": "cookie_consent_analytics", "value": "accept:", "url": url},
            {"name": "cookie_consent_marketing", "value": "accept:", "url": url},
            {"name": "ai_user", "value": "u1", "url": url},
            {"name": "ai_session", "value": "s1", "url": url},
        ]
    )
    page.add_init_script(
        "window.__consentEvents = [];"
        "document.addEventListener('cookie_consent_updated', function (e) {"
        "  window.__consentEvents.push(e.detail);"
        "});"
    )
    page.goto(url)
    assert page.evaluate("window.__consentEvents") == [
        {"analytics": False, "marketing": False}
    ]
    assert _app_insights_ids(page) == ["ai_session", "ai_user"]

    # Guards the two tests above: a refusal saved on this page still clears
    # them (the save writes its flags before it dispatches).
    page.click("#open-cookie-settings")
    page.click("#cookie-btn-save")  # both boxes unticked: the stored refusal
    last = page.evaluate("window.__consentEvents")[-1]
    assert last["analytics"] is False and last["marketing"] is False
    assert _app_insights_ids(page) == []
