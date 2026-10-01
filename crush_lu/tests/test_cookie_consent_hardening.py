"""Consent choices across native forms, rapid banner saves, and Pixel events."""

from unittest.mock import patch

import pytest
from cookie_consent.cache import delete_cache
from cookie_consent.models import CookieGroup
from django.core.cache import cache
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import Client, RequestFactory


@pytest.mark.django_db
def test_native_cookie_forms_sync_flags_without_overwriting_a_newer_banner_choice():
    cache.clear()
    CookieGroup.objects.create(varname="analytics", name="Analytics")
    CookieGroup.objects.create(varname="marketing", name="Marketing")
    delete_cache()
    client = Client()

    response = client.post(
        "/cookies/accept/", {"cookie_groups": "analytics"}, HTTP_HOST="crush.lu"
    )
    assert response.status_code == 302
    assert response.cookies["cookie_consent_analytics"].value.startswith("accept:")

    response = client.post(
        "/cookies/decline/", {"cookie_groups": "analytics"}, HTTP_HOST="crush.lu"
    )
    assert response.status_code == 302
    assert response.cookies["cookie_consent_analytics"].value == "decline"

    # The banner writes its own flag before its fetch. A late response from
    # that fetch must never reset a newer selection in the browser.
    response = client.post(
        "/cookies/accept/",
        {"cookie_groups": "analytics"},
        HTTP_HOST="crush.lu",
        HTTP_X_COOKIE_CONSENT_FETCH="1",
    )
    assert response.status_code == 200
    assert "cookie_consent_analytics" not in response.cookies

    response = client.post("/cookies/accept/", {}, HTTP_HOST="crush.lu")
    assert "cookie_consent_analytics" not in response.cookies

    response = client.post(
        "/cookies/accept/", {"all_groups": "true"}, HTTP_HOST="crush.lu"
    )
    assert response.cookies["cookie_consent_analytics"].value.startswith("accept:")
    assert response.cookies["cookie_consent_marketing"].value.startswith("accept:")

    response = client.post(
        "/cookies/decline/", {"all_groups": "true"}, HTTP_HOST="crush.lu"
    )
    assert response.cookies["cookie_consent_analytics"].value == "decline"
    assert response.cookies["cookie_consent_marketing"].value == "decline"


def test_new_banner_choice_beats_an_older_native_refusal():
    from azureproject.templatetags.analytics import stored_cookie_choice

    request = RequestFactory().get("/")
    request.COOKIES.update(
        {
            "cookie_consent_marketing": "accept:",
            "cookie_consent_marketing_banner": "1",
        }
    )
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=False
    ), patch(
        "azureproject.templatetags.analytics._cookie_group_version", return_value=""
    ):
        assert stored_cookie_choice(request, "marketing") is True


def test_group_versions_are_cached_for_one_template_request():
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent_analytics"] = "accept:"
    request.COOKIES["cookie_consent_marketing"] = "accept:"
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(
        "azureproject.templatetags.analytics._cookie_group_version", return_value=""
    ) as version:
        Template(
            "{% load analytics %}{% cookie_consent_state %}"
            "{% analytics_head %}{% analytics_body %}"
        ).render(
            Context(
                {
                    "request": request,
                    "GOOGLE_ANALYTICS_GTAG_PROPERTY_ID": "G-TEST",
                    "FACEBOOK_PIXEL_ID": "123",
                }
            )
        )
    assert sorted(call.args[0] for call in version.call_args_list) == [
        "analytics",
        "marketing",
    ]


@pytest.mark.playwright
def test_rapid_banner_saves_finish_with_the_last_native_choice(page):
    banner = render_to_string(
        "includes/cookie_banner.html", {"cookie_banner_variant": "crush"}
    )
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body=f"<!doctype html><html><body>{banner}</body></html>",
        ),
    )
    page.add_init_script("""
        window.__nativeCalls = [];
        window.__nativePosts = [];
        window.fetch = function(url, options) {
          window.__nativeCalls.push(url);
          if (url === '/cookies/status/') {
            return Promise.resolve({ok: true, json: () => Promise.resolve({
              csrftoken: 'tok',
              acceptUrl: '/cookies/accept/',
              declineUrl: '/cookies/decline/'
            })});
          }
          window.__nativePosts.push({url: url, keepalive: options.keepalive,
                                     body: options.body});
          if (url === '/cookies/accept/') {
            return new Promise(function(resolve) {
              window.__releaseFirst = () => resolve({ok: true});
            });
          }
          return Promise.resolve({ok: true});
        };
        """)
    page.goto(url)
    page.evaluate(
        "document.getElementById('cookie-btn-accept').click();"
        "document.getElementById('cookie-btn-decline').click();"
    )
    page.wait_for_function("window.__nativeCalls.length === 3")
    assert page.evaluate("window.__nativeCalls") == [
        "/cookies/status/",
        "/cookies/accept/",
        "/cookies/decline/",
    ]
    assert page.evaluate("window.__nativePosts[1]") == {
        "url": "/cookies/decline/",
        "keepalive": True,
        "body": "cookie_groups=analytics&cookie_groups=marketing",
    }
    page.evaluate("window.__releaseFirst()")
    assert page.evaluate("window.__nativeCalls.length") == 3


@pytest.mark.playwright
def test_pixel_event_before_consent_is_not_replayed_after_acceptance(page):
    context = {"FACEBOOK_PIXEL_ID": "123"}
    pixel = Template("{% load analytics %}{% analytics_body %}").render(
        Context(context)
    )
    event = Template('{% load analytics %}{% fb_event "Lead" %}').render(
        Context(context)
    )
    banner = render_to_string(
        "includes/cookie_banner.html", {"cookie_banner_variant": "crush"}
    )
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body=f"<!doctype html><html><body>{pixel}{event}{banner}</body></html>",
        ),
    )
    page.route("https://connect.facebook.net/**", lambda route: route.abort())
    page.route(
        "**/cookies/**",
        lambda route: route.fulfill(
            content_type="application/json",
            body='{"csrftoken":"tok","acceptUrl":"/cookies/accept/",'
            '"declineUrl":"/cookies/decline/"}',
        ),
    )
    page.goto(url)
    assert page.evaluate("window.__fbPendingEvents || []") == []
    page.click("#cookie-btn-accept")
    calls = page.evaluate("window.fbq.queue.map(args => Array.from(args))")
    assert ["track", "Lead"] not in calls
    assert ["track", "PageView"] in calls
    assert page.evaluate("window.__fbPendingEvents") == []


@pytest.mark.playwright
def test_pixel_event_with_prior_consent_replays_when_pixel_starts(page):
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent_marketing"] = "accept:"
    context = Context({"request": request, "FACEBOOK_PIXEL_ID": "123"})
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(
        "azureproject.templatetags.analytics._cookie_group_version", return_value=""
    ):
        event = Template('{% load analytics %}{% fb_event "Lead" %}').render(context)
        pixel = Template("{% load analytics %}{% analytics_body %}").render(context)
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body=f"<!doctype html><html><body>{event}{pixel}</body></html>",
        ),
    )
    page.route("https://connect.facebook.net/**", lambda route: route.abort())
    page.goto(url)
    calls = page.evaluate("window.fbq.queue.map(args => Array.from(args))")
    assert ["track", "Lead"] in calls
    assert calls.index(["track", "PageView"]) < calls.index(["track", "Lead"])
    assert page.evaluate("window.__fbPendingEvents") == []


@pytest.mark.playwright
def test_refusal_discards_pixel_events_buffered_under_prior_consent(page):
    request = RequestFactory().get("/")
    request.COOKIES["cookie_consent_analytics"] = "decline"
    request.COOKIES["cookie_consent_marketing"] = "accept:"
    context = Context({"request": request, "FACEBOOK_PIXEL_ID": "123"})
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(
        "azureproject.templatetags.analytics._cookie_group_version", return_value=""
    ):
        event = Template('{% load analytics %}{% fb_event "Lead" %}').render(context)
        banner = render_to_string("includes/cookie_banner.html", {"request": request})
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body=f"<!doctype html><html><body>{event}{banner}</body></html>",
        ),
    )
    page.route(
        "**/cookies/**",
        lambda route: route.fulfill(
            content_type="application/json",
            body='{"csrftoken":"tok","acceptUrl":"/cookies/accept/",'
            '"declineUrl":"/cookies/decline/"}',
        ),
    )
    page.goto(url)
    assert page.evaluate("window.__fbPendingEvents") == [["track", "Lead"]]
    page.evaluate("document.getElementById('cookie-btn-decline').click()")
    assert page.evaluate("window.__fbPendingEvents") == []
    page.evaluate("document.getElementById('cookie-btn-accept').click()")
    assert page.evaluate("window.__fbPendingEvents") == []


@pytest.mark.playwright
def test_kept_page_cannot_track_after_a_newer_group_version_was_seen(page):
    request = RequestFactory().get("/")
    old = "2026-01-01T00:00:00+00:00"
    newer = "2026-06-01T00:00:00+00:00"
    request.COOKIES["cookie_consent_analytics"] = f"accept:{old}"
    request.COOKIES["cookie_consent_marketing"] = f"accept:{old}"
    context = Context(
        {
            "request": request,
            "GOOGLE_ANALYTICS_GTAG_PROPERTY_ID": "G-TEST",
            "FACEBOOK_PIXEL_ID": "123",
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc",
        }
    )
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(
        "azureproject.templatetags.analytics._cookie_group_version",
        return_value=old,
    ):
        head = Template(
            "{% load analytics %}{% analytics_head %}{% appinsights_head %}"
        ).render(context)
        body = Template("{% load analytics %}{% analytics_body %}").render(context)
        banner = render_to_string("includes/cookie_banner.html", {"request": request})
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body=f"<!doctype html><html><head>{head}</head><body>{body}{banner}</body></html>",
        ),
    )
    for pattern in (
        "https://www.googletagmanager.com/**",
        "https://connect.facebook.net/**",
        "https://js.monitor.azure.com/**",
    ):
        page.route(pattern, lambda route: route.abort())
    page.context.add_cookies(
        [
            {"name": "cookie_consent_analytics", "value": f"accept:{old}", "url": url},
            {"name": "cookie_consent_marketing", "value": f"accept:{old}", "url": url},
        ]
    )
    page.add_init_script(
        f"localStorage.setItem('crush_consent_version_analytics', '{newer}');"
        f"localStorage.setItem('crush_consent_version_marketing', '{newer}');"
    )
    page.goto(url)
    calls = page.evaluate("window.dataLayer.map(args => Array.from(args))")
    config = next(i for i, call in enumerate(calls) if call[:2] == ["config", "G-TEST"])
    updates = [call[2] for call in calls[:config] if call[:2] == ["consent", "update"]]
    # Denied default; the stale grant update is skipped, nothing re-grants.
    assert not any("granted" in update.values() for update in updates)
    analytics_updates = [
        call[2]["analytics_storage"]
        for call in calls
        if call[:2] == ["consent", "update"] and "analytics_storage" in call[2]
    ]
    assert "granted" not in analytics_updates
    assert page.locator("#cookie-consent-banner").is_visible()
    assert page.evaluate("typeof window.fbq") == "undefined"
    assert page.evaluate("typeof window.appInsights") == "undefined"


@pytest.mark.playwright
def test_kept_ticket_honors_consent_renewed_at_the_newer_version(page):
    request = RequestFactory().get("/")
    old = "2026-01-01T00:00:00+00:00"
    newer = "2026-06-01T00:00:00+00:00"
    for group in ("analytics", "marketing"):
        request.COOKIES[f"cookie_consent_{group}"] = f"accept:{old}"
    context = Context(
        {
            "request": request,
            "GOOGLE_ANALYTICS_GTAG_PROPERTY_ID": "G-TEST",
            "FACEBOOK_PIXEL_ID": "123",
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc",
        }
    )
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(
        "azureproject.templatetags.analytics._cookie_group_version",
        return_value=old,
    ):
        head = Template(
            "{% load analytics %}{% analytics_head %}{% appinsights_head %}"
        ).render(context)
        body = Template("{% load analytics %}{% analytics_body %}").render(context)
        banner = render_to_string("includes/cookie_banner.html", {"request": request})
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body=f"<!doctype html><html><head>{head}</head><body>{body}{banner}</body></html>",
        ),
    )
    for pattern in (
        "https://www.googletagmanager.com/**",
        "https://connect.facebook.net/**",
        "https://js.monitor.azure.com/**",
    ):
        page.route(pattern, lambda route: route.abort())
    page.context.add_cookies(
        [
            {"name": f"cookie_consent_{group}", "value": f"accept:{newer}", "url": url}
            for group in ("analytics", "marketing")
        ]
    )
    page.add_init_script(
        "localStorage.setItem('crush_consent_version_analytics', "
        f"'{newer}');"
        "localStorage.setItem('crush_consent_version_marketing', "
        f"'{newer}');"
    )
    page.goto(url)
    assert not page.locator("#cookie-consent-banner").is_visible()
    updates = [
        call[2]
        for call in page.evaluate("window.dataLayer.map(args => Array.from(args))")
        if call[:2] == ["consent", "update"]
    ]
    assert updates[-1]["analytics_storage"] == "granted"
    assert updates[-1]["ad_storage"] == "granted"
    assert page.evaluate("typeof window.fbq") == "function"
    assert page.evaluate("typeof window.appInsights") == "object"

    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    assert not page.locator("#cookie-consent-banner").is_visible()
    assert page.evaluate("window.fbq.queue.map(args => Array.from(args))")[-1] != [
        "consent",
        "revoke",
    ]


@pytest.mark.playwright
@pytest.mark.parametrize(
    "stale_groups, expected_checked",
    [
        (["analytics"], [False, True]),
        (["marketing"], [True, False]),
        (["analytics", "marketing"], [False, False]),
    ],
)
def test_restored_page_revokes_stale_groups_and_unticks_them(
    page, stale_groups, expected_checked
):
    request = RequestFactory().get("/")
    old = "2026-01-01T00:00:00+00:00"
    newer = "2026-06-01T00:00:00+00:00"
    for group in ("analytics", "marketing"):
        request.COOKIES[f"cookie_consent_{group}"] = f"accept:{old}"
    context = Context(
        {
            "request": request,
            "GOOGLE_ANALYTICS_GTAG_PROPERTY_ID": "G-TEST",
            "FACEBOOK_PIXEL_ID": "123",
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc",
        }
    )
    with patch(
        "cookie_consent.util.get_cookie_value_from_request", return_value=None
    ), patch(
        "azureproject.templatetags.analytics._cookie_group_version",
        return_value=old,
    ):
        head = Template(
            "{% load analytics %}{% analytics_head %}{% appinsights_head %}"
        ).render(context)
        body = Template("{% load analytics %}{% analytics_body %}").render(context)
        banner = render_to_string("includes/cookie_banner.html", {"request": request})
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body=f"<!doctype html><html><head>{head}</head><body>{body}{banner}</body></html>",
        ),
    )
    for pattern in (
        "https://www.googletagmanager.com/**",
        "https://connect.facebook.net/**",
        "https://js.monitor.azure.com/**",
    ):
        page.route(pattern, lambda route: route.abort())
    page.route("**/cookies/**", lambda route: route.fulfill(status=200, body=""))
    page.context.add_cookies(
        [
            {"name": f"cookie_consent_{group}", "value": f"accept:{old}", "url": url}
            for group in ("analytics", "marketing")
        ]
    )
    page.goto(url)
    assert page.evaluate("typeof window.fbq") == "function"
    assert page.evaluate("typeof window.appInsights") == "object"

    page.evaluate(
        "groups => groups.forEach(group => "
        "localStorage.setItem('crush_consent_version_' + group, "
        f"'{newer}'))",
        stale_groups,
    )
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    assert page.locator("#cookie-consent-banner").is_visible()
    updates = [
        call[2]
        for call in page.evaluate("window.dataLayer.map(args => Array.from(args))")
        if call[:2] == ["consent", "update"]
    ]
    assert updates[-1]["analytics_storage"] == (
        "denied" if "analytics" in stale_groups else "granted"
    )
    assert updates[-1]["ad_storage"] == (
        "denied" if "marketing" in stale_groups else "granted"
    )
    if "analytics" in stale_groups:
        assert page.evaluate("window.appInsights.config.disableTelemetry") is True
        assert page.evaluate("window.appInsights.config.disableCookiesUsage") is True
    if "marketing" in stale_groups:
        assert page.evaluate("window.fbq.queue.map(args => Array.from(args))")[-1] == [
            "consent",
            "revoke",
        ]

    page.click("#cookie-btn-customize")
    checked = page.evaluate(
        "[document.getElementById('cookie-analytics').checked, "
        "document.getElementById('cookie-marketing').checked]"
    )
    assert checked == expected_checked

    page.evaluate(
        "groups => { groups.forEach(group => {"
        "  document.getElementById('cookie-' + group).checked = true;"
        "}); document.getElementById('cookie-btn-save').click(); }",
        stale_groups,
    )
    flags = {cookie["name"]: cookie["value"] for cookie in page.context.cookies(url)}
    for group in stale_groups:
        assert flags[f"cookie_consent_{group}"] == f"accept:{newer}"
    state = page.evaluate(
        "JSON.parse(document.getElementById('cookie-consent-banner').dataset.consentState)"
    )
    for group in stale_groups:
        assert state["versions"][group] == newer
