"""
Shared LuxID helpers.

LuxID (POST Luxembourg's government CIAM) is reached two ways: the dedicated
``luxid`` allauth provider, or the generic ``openid_connect`` provider
configured as the LuxID SocialApp (``provider_id="luxid"``). These helpers
build the correct "connect LuxID" OAuth URL for whichever is configured, so
both the account-settings page and the Crush Connect teaser can offer the same
CTA without duplicating the provider-resolution logic.
"""

from django.conf import settings
from django.urls import reverse


def luxid_connect_url(available_providers, oidc_app=None):
    """Return the correct OAuth connect URL for LuxID given available providers.

    Prefers the custom 'luxid' provider (fixed URL, no path kwargs). Falls back
    to allauth's generic openid_connect URL when the SocialApp is configured
    with provider='openid_connect' instead. In that case the openid_connect URL
    requires a provider_id path kwarg (allauth 0.61+), so the caller must pass
    the SocialApp object as ``oidc_app`` to supply it. Returns ``None`` when no
    LuxID provider is configured for the current site.
    """
    # In local dev with the stub app, allauth would try to reach luxid.gov.lu to
    # fetch the OIDC discovery document — which doesn't exist locally. Redirect
    # to the dev simulation endpoint instead so the full approval flow can be
    # tested without real LuxID credentials.
    if settings.DEBUG and oidc_app is not None:
        if getattr(oidc_app, "client_id", None) == "dev-stub-client-id":
            try:
                return reverse("crush_lu:dev_simulate_luxid_connect")
            except Exception:
                pass

    if "luxid" in available_providers:
        try:
            return reverse("luxid_login") + "?process=connect"
        except Exception:
            pass
    if "openid_connect" in available_providers and oidc_app is not None:
        try:
            pid = getattr(oidc_app, "provider_id", None) or getattr(oidc_app, "slug", None)
            if pid:
                return (
                    reverse("openid_connect_login", kwargs={"provider_id": pid})
                    + "?process=connect"
                )
        except Exception:
            pass
    return None


def get_luxid_connect_url(request):
    """Resolve the LuxID connect URL for the current request's site.

    Mirrors the provider lookup in ``views_account.account_settings`` so callers
    that only have a ``request`` (e.g. the Crush Connect teaser) can render a
    "Connect LuxID" CTA. Returns ``None`` when LuxID isn't configured.
    """
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site

    try:
        current_site = Site.objects.get_current(request)
        available_providers = set(
            SocialApp.objects.filter(sites=current_site).values_list(
                "provider", flat=True
            )
        )
    except Exception:
        return None

    oidc_app = None
    if "openid_connect" in available_providers:
        try:
            oidc_app = SocialApp.objects.filter(
                provider="openid_connect", provider_id="luxid", sites=current_site
            ).first()
        except Exception:
            oidc_app = None

    return luxid_connect_url(available_providers, oidc_app=oidc_app)
