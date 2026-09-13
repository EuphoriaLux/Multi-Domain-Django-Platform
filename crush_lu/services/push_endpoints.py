"""Restrict browser-supplied push endpoints before storage and HTTP delivery."""

import re
from contextlib import contextmanager
from urllib.parse import urlsplit

import requests

# Browser push-service origins, not arbitrary HTTPS destinations. Keep paths and
# queries opaque: WNS, for example, puts the channel token in the query string.
# Provider references:
# https://web.dev/articles/codelab-notifications-push-server
# https://mozilla-services.github.io/autopush-rs/
# https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers
# https://learn.microsoft.com/en-us/windows/apps/develop/notifications/push-notifications/wns-overview
PUSH_HOSTS = frozenset({"fcm.googleapis.com", "updates.push.services.mozilla.com"})
PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com")
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class InvalidPushEndpoint(ValueError):
    """An endpoint is not a supported browser push-service destination."""


def validate_push_endpoint(endpoint):
    """Reject untrusted origins without resolving attacker-controlled hostnames.

    Only provider-controlled DNS names are accepted, so users cannot supply IPs,
    local names or a DNS-rebinding domain. Never log the endpoint: its opaque
    path/query is a device capability. HTTPS certificate verification stays on.
    """
    error = "Unsupported push endpoint"
    if not isinstance(endpoint, str) or not endpoint:
        raise InvalidPushEndpoint(error)
    # urlsplit silently strips some whitespace/control characters. Reject them
    # first, along with backslashes that URL parsers can interpret differently.
    if any(ord(char) <= 32 or ord(char) == 127 for char in endpoint):
        raise InvalidPushEndpoint(error)
    if "\\" in endpoint or "#" in endpoint:
        raise InvalidPushEndpoint(error)
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or not all(HOST_LABEL.fullmatch(label) for label in host.split("."))
        ):
            raise InvalidPushEndpoint(error)
    except ValueError:
        raise InvalidPushEndpoint(error) from None
    if host not in PUSH_HOSTS and not host.endswith(PUSH_HOST_SUFFIXES):
        raise InvalidPushEndpoint(error)


class PushSession(requests.Session):
    """No redirects or implicit proxy/netrc credentials for push delivery."""

    def __init__(self):
        super().__init__()
        self.trust_env = False

    def request(self, method, url, **kwargs):
        validate_push_endpoint(url)
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


@contextmanager
def push_transport(endpoint):
    """Check stored rows too, before pywebpush encrypts or sends anything.

    pywebpush 2.5 accepts a requests_session and raises WebPushException for
    redirects (>202). A fresh session avoids sharing mutable state across
    request threads, and closes connections after each bounded send.
    """
    validate_push_endpoint(endpoint)
    with PushSession() as session:
        yield session
