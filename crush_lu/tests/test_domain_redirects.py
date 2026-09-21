"""Tests for redirect-only domains (REDIRECT_DOMAINS).

A redirect-only domain is a name that is pointed at the App Service but never
serves content of its own: RedirectWWWToRootDomainMiddleware answers it with a
301 to another site. Two things have to line up for that to work in production,
and both are easy to break independently:

  * the host must be in ALLOWED_HOSTS, because host_validation.py runs ahead of
    every application middleware and would otherwise 400 the request, and
  * the redirect must fire before DomainURLRoutingMiddleware picks a urlconf,
    or the parked domain quietly serves the PRODUCTION_DEFAULT site instead.
"""

from urllib.parse import urlsplit

import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory

from azureproject.domains import (
    REDIRECT_DOMAINS,
    get_all_hosts,
    get_domain_config,
    get_redirect_target,
)
from azureproject.redirect_www_middleware import RedirectWWWToRootDomainMiddleware

SENTINEL = "served by the application"
POWER_UP_SOLUTIONS = "https://power-up.lu/solutions/"


def _response(request):
    return HttpResponse(SENTINEL)


def _get(path, host):
    middleware = RedirectWWWToRootDomainMiddleware(_response)
    return middleware(RequestFactory().get(path, HTTP_HOST=host))


@pytest.mark.parametrize("host", sorted(REDIRECT_DOMAINS))
def test_redirect_only_domains_301_to_their_target(host):
    response = _get("/", host)

    assert response.status_code == 301
    assert response["Location"] == REDIRECT_DOMAINS[host]


def test_moonlightdating_redirects_to_power_up_solutions():
    response = _get("/", "moonlightdating.lu")

    assert response.status_code == 301
    assert response["Location"] == POWER_UP_SOLUTIONS


def test_www_variant_redirects_to_power_up_solutions_without_a_stop_on_the_apex():
    response = _get("/", "www.moonlightdating.lu")

    assert response.status_code == 301
    assert response["Location"] == POWER_UP_SOLUTIONS


@pytest.mark.parametrize(
    "path", ["/events/?utm_source=flyer", "/profil/", "/a/deep/link"]
)
def test_paths_are_not_carried_to_the_target(path):
    """Every path lands on the target itself, not on a copy of the path.

    The sites' user-facing routes live inside
    i18n_patterns(prefix_default_language=True), so a copied path only
    resolves if that exact page exists on the target (AGENTS.md). Copying
    paths from a parked domain that never served them would send visitors to
    404s.
    """
    response = _get(path, "moonlightdating.lu")

    assert response.status_code == 301
    assert response["Location"] == POWER_UP_SOLUTIONS


@pytest.mark.django_db
@pytest.mark.parametrize("target", sorted(set(REDIRECT_DOMAINS.values())))
def test_redirect_targets_land_on_a_live_page(target):
    """A 301 is cached for good, so it must never point at a 404.

    The target host is one of our own sites: its LocaleMiddleware should send
    the unprefixed path on to a language-prefixed page that renders.
    """
    url = urlsplit(target)
    response = Client(HTTP_HOST=url.netloc).get(url.path)

    assert response.status_code == 302
    assert response["Location"] == f"/en{url.path}"

    response = Client(HTTP_HOST=url.netloc).get(response["Location"])

    assert response.status_code == 200


def test_redirect_fires_before_the_application_is_reached():
    """The 301 must short-circuit: a parked domain never renders a page."""
    response = _get("/", "moonlightdating.lu")

    assert not response.content


def test_configured_sites_are_left_alone():
    response = _get("/", "crush.lu")

    assert response.status_code == 200
    assert response.content.decode() == SENTINEL


def test_health_probes_are_not_redirected():
    """The probe short-circuit at the top of the middleware still wins."""
    response = _get("/healthz/", "moonlightdating.lu")

    assert response.status_code == 200


def test_redirect_hosts_are_allowed_hosts():
    """Without this, host_validation.py 400s the request before the 301."""
    hosts = get_all_hosts()

    for host in REDIRECT_DOMAINS:
        assert host in hosts


def test_redirect_domains_are_not_routed_sites():
    """A redirect-only domain has no urlconf of its own."""
    for host in REDIRECT_DOMAINS:
        assert get_domain_config(host) is None


def test_get_redirect_target_normalises_the_host():
    assert get_redirect_target("MoonlightDating.LU:443") == POWER_UP_SOLUTIONS
    assert get_redirect_target("moonlightdating.lu.") == POWER_UP_SOLUTIONS
    assert get_redirect_target("crush.lu") is None
