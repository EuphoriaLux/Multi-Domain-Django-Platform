"""Production host boundary, including Azure's internal probe addresses."""

from ipaddress import IPv4Address, IPv4Network

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import request as django_request

AZURE_INTERNAL_NETWORK = IPv4Network("169.254.0.0/16")
PROBE_PATHS = frozenset({"/healthz", "/healthz/", "/readyz", "/readyz/"})


def is_azure_internal_host(host):
    """Match IPv4 literals only, never DNS names beginning with 169.254."""
    try:
        return IPv4Address(host) in AZURE_INTERNAL_NETWORK
    except ValueError:
        return False


def validate_production_request_host(request):
    """Check both host headers before a redirect, probe or domain fallback.

    Azure forwards the original host in X-Forwarded-Host. Validate both it and
    Host against the same allowlist: a valid forwarded value must not hide an
    invalid DNS Host, nor may a www redirect hide an invalid forwarded value.
    Azure may use an internal IP as its transport Host while forwarding an
    explicitly allowed public host. An internal *effective* host reaches only
    probes; it must never select an application URL or redirect authority.
    """
    headers = [request.META.get("HTTP_HOST", request._get_raw_host())]
    if "HTTP_X_FORWARDED_HOST" in request.META:
        headers.append(request.META["HTTP_X_FORWARDED_HOST"])

    for header in headers:
        host, _port = django_request.split_domain_port(header)
        if not host or (
            not is_azure_internal_host(host)
            and not django_request.validate_host(host, settings.ALLOWED_HOSTS)
        ):
            raise DisallowedHost("Invalid production Host or X-Forwarded-Host header.")

    effective_host, _port = django_request.split_domain_port(request.get_host())
    if is_azure_internal_host(effective_host) and request.path not in PROBE_PATHS:
        raise DisallowedHost("Azure internal host is only valid for health probes.")
