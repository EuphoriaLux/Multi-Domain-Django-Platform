from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def get_email_providers(context):
    """Return dict mapping email addresses to their social provider names."""
    request = context.get("request")
    if not request or not request.user.is_authenticated:
        return {}
    email_providers = {}
    for sa in request.user.socialaccount_set.all():
        if sa.provider == "microsoft":
            email = (
                sa.extra_data.get("mail")
                or sa.extra_data.get("userPrincipalName")
                or sa.extra_data.get("email")
            )
        else:
            email = sa.extra_data.get("email")
        if email:
            email_providers.setdefault(email.lower(), []).append(sa.provider)
    return email_providers


@register.filter
def lookup(dictionary, key):
    """Look up a key in a dictionary. Returns None if not found."""
    if not isinstance(dictionary, dict):
        return None
    return dictionary.get(key)


@register.filter
def any_unverified(emailaddresses):
    """Return True if any email address in the list is unverified."""
    return any(not ea.verified for ea in emailaddresses)


# Fixed OAuth origins, keyed by allauth provider id. The browser is sent to
# these the instant a member taps a social button, so warming the TCP+TLS
# handshake while they are still reading the page removes it from the click.
# Only the authorization host is worth a preconnect: the token and userinfo
# hosts are talked to by Django, not the browser.
SOCIAL_LOGIN_ORIGINS = {
    "google": "https://accounts.google.com",
    "facebook": "https://www.facebook.com",
    "microsoft": "https://login.microsoftonline.com",
    "apple": "https://appleid.apple.com",
}

# LuxID is deliberately absent from the table above. Its host is environment
# dependent — staging points at login-uat.luxid.lu and production at
# login.luxid.lu, configured per SocialApp — so hardcoding either would send
# one of the two environments off to warm a connection it never uses.
LUXID_DEFAULT_ORIGIN = "https://login-uat.luxid.lu"


def _origin(url):
    """Reduce a URL to scheme://host, which is all preconnect uses."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except Exception:
        pass
    return None


@register.simple_tag(takes_context=True)
def social_login_preconnect(context):
    """Emit resource hints for the IdPs configured on this site.

    Rendered into the document head of the login/signup surfaces. Only
    providers actually configured are hinted — hinting one that is not offered
    would open a connection nobody uses and leak which providers were
    considered.

    LuxID gets a full ``preconnect`` (TCP + TLS); everyone else gets
    ``dns-prefetch`` only. That split is deliberate. A preconnect holds an open
    socket, and a member uses exactly one provider — or none, if they sign in
    with a password — so preconnecting all five would open four sockets that
    are never used, competing for bandwidth during page load and costing
    battery on mobile. LuxID earns the exception on two grounds: it is the
    promoted path (rendered first, badged "Recommended"), and it was the
    slowest callback in the telemetry this work is based on. DNS resolution is
    cheap enough to spend on the rest.

    Failures are swallowed and yield no hints: this is a pure optimization and
    must never be able to break a login page from rendering.
    """
    from django.contrib.sites.models import Site
    from django.utils.safestring import mark_safe
    from django.utils.html import format_html_join

    request = context.get("request")

    try:
        from allauth.socialaccount.models import SocialApp

        current_site = Site.objects.get_current(request)
        apps = SocialApp.objects.filter(sites=current_site)

        preconnect = []
        prefetch = []
        for app in apps:
            provider = app.provider
            provider_id = getattr(app, "provider_id", "") or ""

            # LuxID reaches this code under either spelling: the dedicated
            # "luxid" provider, or the generic openid_connect app whose
            # provider_id is "luxid" (both configurations exist in the wild
            # and crush_lu/signals.py already treats them interchangeably).
            is_luxid = provider == "luxid" or provider_id == "luxid"
            if is_luxid:
                server_url = (app.settings or {}).get(
                    "server_url"
                ) or LUXID_DEFAULT_ORIGIN
                origin = _origin(server_url)
            else:
                origin = SOCIAL_LOGIN_ORIGINS.get(provider)

            if not origin:
                continue
            if is_luxid and origin not in preconnect:
                preconnect.append(origin)
            if origin not in prefetch:
                prefetch.append(origin)
    except Exception:
        return mark_safe("")

    if not prefetch:
        return mark_safe("")

    # crossorigin is required on the preconnect: the browser keeps a separate
    # connection pool for credentialed requests and an OAuth navigation is
    # credentialed, so without it the warmed socket is the wrong one and the
    # handshake is paid again at click time. dns-prefetch takes no such
    # attribute — it resolves a name and nothing more.
    return mark_safe(
        "\n".join(
            filter(
                None,
                [
                    format_html_join(
                        "\n",
                        '<link rel="preconnect" href="{}" crossorigin>',
                        ((o,) for o in preconnect),
                    ),
                    format_html_join(
                        "\n",
                        '<link rel="dns-prefetch" href="{}">',
                        ((o,) for o in prefetch),
                    ),
                ],
            )
        )
    )
