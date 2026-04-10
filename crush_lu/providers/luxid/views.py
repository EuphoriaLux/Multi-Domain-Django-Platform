"""
LuxID OAuth views.

Subclasses the OpenID Connect adapter with a fixed provider_id="luxid"
so allauth uses /accounts/luxid/ URLs instead of /accounts/oidc/luxid/.
"""

from allauth.socialaccount.providers.oauth2.views import (
    OAuth2CallbackView,
    OAuth2LoginView,
)
from allauth.socialaccount.providers.openid_connect.views import (
    OpenIDConnectOAuth2Adapter,
)


class LuxIDOAuth2Adapter(OpenIDConnectOAuth2Adapter):
    provider_id = "luxid"

    def __init__(self, request):
        # Pass fixed provider_id to the OIDC adapter
        super().__init__(request, provider_id="luxid")


oauth2_login = OAuth2LoginView.adapter_view(LuxIDOAuth2Adapter)
oauth2_callback = OAuth2CallbackView.adapter_view(LuxIDOAuth2Adapter)
