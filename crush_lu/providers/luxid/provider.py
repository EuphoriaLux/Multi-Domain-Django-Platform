"""
LuxID OAuth provider for Crush.lu

Dedicated allauth provider for POST Luxembourg's LuxID CIAM solution.
Extends the generic OpenID Connect provider with its own URL namespace
(/accounts/luxid/) so it doesn't conflict with other OIDC providers
like LinkedIn (/accounts/oidc/).

Django Admin setup:
    Provider: LuxID
    Name: LuxID
    Client ID: (from POST)
    Secret Key: (from POST)
    Settings: {"server_url": "https://login-uat.luxid.lu"}
    Sites: test.crush.lu (UAT) or crush.lu (Prod)
"""

from urllib.parse import urlencode

from django.urls import reverse

from allauth.socialaccount.providers.openid_connect.provider import (
    OpenIDConnectProvider,
    OpenIDConnectProviderAccount,
)


class LuxIDAccount(OpenIDConnectProviderAccount):
    pass


class LuxIDProvider(OpenIDConnectProvider):
    id = "luxid"
    name = "LuxID"
    account_class = LuxIDAccount

    @property
    def oauth2_adapter_class(self):
        from .views import LuxIDOAuth2Adapter

        return LuxIDOAuth2Adapter

    def __init__(self, request, app=None):
        super().__init__(request, app=app)
        if app and app.name:
            self.name = app.name

    @property
    def server_url(self):
        # Allow admin override via SocialApp settings, but fall back to default
        url = (self.app.settings or {}).get("server_url", "")
        if not url:
            # Default to UAT; production uses login.luxid.lu (set via admin)
            url = "https://login-uat.luxid.lu"
        return self.wk_server_url(url)

    def get_login_url(self, request, **kwargs):
        # Override OpenIDConnectProvider which uses provider_id kwargs.
        # Our urls.py uses default_urlpatterns() which registers "luxid_login"
        # without a provider_id path parameter.
        url = reverse(f"{self.id}_login")
        if kwargs:
            url = f"{url}?{urlencode(kwargs)}"
        return url

    def get_callback_url(self):
        # Override to use "luxid_callback" instead of "openid_connect_callback"
        return reverse(f"{self.id}_callback")

    def extract_email_addresses(self, data):
        # allauth 65.x's _pick_data() prefers ``userinfo`` over ``id_token``
        # and ignores id_token entirely when userinfo is present.  If LuxID
        # releases ``email`` only in the id_token (not in the userinfo
        # endpoint response) the address list would be empty.  Merge both
        # sources first; _pick_data falls through to return ``data`` as-is
        # when the merged dict contains neither "userinfo" nor "id_token" key.
        id_token = data.get("id_token") or {}
        userinfo = data.get("userinfo") or {}
        merged = {**id_token, **userinfo}  # userinfo wins for overlapping keys
        flat_data = merged if merged else data
        addresses = super().extract_email_addresses(flat_data)
        # LuxID is POST Luxembourg's government-grade CIAM and the authoritative
        # trust anchor for the email it releases.  Force verified=True so that
        # SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT works correctly.
        for addr in addresses:
            addr.verified = True
        return addresses


provider_classes = [LuxIDProvider]
