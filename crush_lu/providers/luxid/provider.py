"""
LuxID OAuth provider for Crush.lu

Dedicated allauth provider for POST Luxembourg's LuxID CIAM solution.
Extends the generic OpenID Connect provider with its own URL namespace
(/accounts/luxid/) so it doesn't conflict with other OIDC providers
like LinkedIn (/accounts/oidc/).

Django Admin setup:
    Provider: LuxID
    Provider ID: luxid  (auto-set)
    Name: LuxID
    Client ID: (from POST)
    Secret Key: (from POST)
    Settings: {"server_url": "https://login-uat.luxid.lu"}
    Sites: test.crush.lu (UAT) or crush.lu (Prod)
"""

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

    def __init__(self, request, app=None):
        # Skip OpenIDConnectProvider.__init__ which expects app.name
        # and call the grandparent instead, then set name explicitly
        super().__init__(request, app=app)
        if app and app.name:
            self.name = app.name


provider_classes = [LuxIDProvider]
