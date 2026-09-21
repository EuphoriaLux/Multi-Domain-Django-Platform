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

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from azureproject.domains import (
    REDIRECT_DOMAINS,
    get_all_hosts,
    get_domain_config,
    get_redirect_target,
)
from azureproject.redirect_www_middleware import RedirectWWWToRootDomainMiddleware

SENTINEL = "served by the application"


def _response(request):
    return HttpResponse(SENTINEL)


def _get(path, host):
    middleware = RedirectWWWToRootDomainMiddleware(_response)
    return middleware(RequestFactory().get(path, HTTP_HOST=host))


@pytest.mark.parametrize("host", sorted(REDIRECT_DOMAINS))
def test_redirect_only_domains_301_to_their_target(host):
    response = _get("/", host)

    assert response.status_code == 301
    assert response["Location"] == REDIRECT_DOMAINS[host] + "/"


def test_moonlightdating_redirects_to_crush():
    response = _get("/", "moonlightdating.lu")

    assert response.status_code == 301
    assert response["Location"] == "https://crush.lu/"


def test_www_variant_redirects_without_a_stop_on_the_apex():
    response = _get("/", "www.moonlightdating.lu")

    assert response.status_code == 301
    assert response["Location"] == "https://crush.lu/"


def test_path_and_query_string_are_carried_to_the_target():
    response = _get("/events/?utm_source=flyer", "moonlightdating.lu")

    assert response["Location"] == "https://crush.lu/events/?utm_source=flyer"


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
    assert get_redirect_target("MoonlightDating.LU:443") == "https://crush.lu"
    assert get_redirect_target("moonlightdating.lu.") == "https://crush.lu"
    assert get_redirect_target("crush.lu") is None
