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
        window.fetch = function(url) {
          window.__nativeCalls.push(url);
          if (url === '/cookies/status/') {
            return Promise.resolve({ok: true, json: () => Promise.resolve({
              csrftoken: 'tok',
              acceptUrl: '/cookies/accept/',
              declineUrl: '/cookies/decline/'
            })});
          }
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
    page.wait_for_function("window.__nativeCalls.length === 2")
    assert page.evaluate("window.__nativeCalls") == [
        "/cookies/status/",
        "/cookies/accept/",
    ]
    page.evaluate("window.__releaseFirst()")
    page.wait_for_function("window.__nativeCalls.length === 4")
    assert page.evaluate("window.__nativeCalls") == [
        "/cookies/status/",
        "/cookies/accept/",
        "/cookies/status/",
        "/cookies/decline/",
    ]


@pytest.mark.playwright
def test_pixel_event_replays_after_marketing_is_accepted(page):
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
    assert page.evaluate("window.__fbPendingEvents") == [["track", "Lead"]]
    page.click("#cookie-btn-accept")
    calls = page.evaluate("window.fbq.queue.map(args => Array.from(args))")
    assert ["track", "Lead"] in calls
    assert calls.index(["track", "PageView"]) < calls.index(["track", "Lead"])
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
    url = "http://crush.test/"
    page.route(
        url,
        lambda route: route.fulfill(
            content_type="text/html",
            body=f"<!doctype html><html><head>{head}</head><body>{body}</body></html>",
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
    assert {"analytics_storage": "denied"} in updates
    assert page.evaluate("typeof window.fbq") == "undefined"
    assert page.evaluate("typeof window.appInsights") == "undefined"
