"""
LuxID OAuth views.

Subclasses the OpenID Connect adapter with a fixed provider_id="luxid"
so allauth uses /accounts/luxid/ URLs instead of /accounts/oidc/luxid/.
"""

from django.urls import reverse

from allauth.socialaccount.providers.oauth2.views import (
    OAuth2CallbackView,
    OAuth2LoginView,
)
from allauth.socialaccount.providers.openid_connect.views import (
    OpenIDConnectOAuth2Adapter,
)
from allauth.utils import build_absolute_uri


class LuxIDOAuth2Adapter(OpenIDConnectOAuth2Adapter):
    provider_id = "luxid"

    def __init__(self, request):
        super().__init__(request, provider_id="luxid")

    def get_callback_url(self, request, app):
        # Override OpenIDConnectOAuth2Adapter which reverses
        # "openid_connect_callback" with provider_id kwargs.
        # Our urls.py registers "luxid_callback" without kwargs.
        callback_url = reverse("luxid_callback")
        protocol = self.redirect_uri_protocol
        return build_absolute_uri(request, callback_url, protocol)


oauth2_login = OAuth2LoginView.adapter_view(LuxIDOAuth2Adapter)
oauth2_callback = OAuth2CallbackView.adapter_view(LuxIDOAuth2Adapter)
