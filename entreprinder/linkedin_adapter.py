# entreprinder/linkedin_adapter.py
from allauth.socialaccount.providers.oauth2.views import OAuth2Adapter
from allauth.socialaccount.providers.openid_connect.provider import OpenIDConnectProvider
import requests

class LinkedInOAuth2Adapter(OAuth2Adapter):
    provider_id = 'linkedin_oauth2'
    access_token_url = 'https://www.linkedin.com/oauth/v2/accessToken'
    authorize_url = 'https://www.linkedin.com/oauth/v2/authorization'
    profile_url = 'https://api.linkedin.com/v2/userinfo'
    
    def complete_login(self, request, app, token, **kwargs):
        headers = {'Authorization': f'Bearer {token.token}'}
        resp = requests.get(self.profile_url, headers=headers, timeout=30)
        resp.raise_for_status()
        extra_data = resp.json()
        return self.get_provider().sociallogin_from_response(request, extra_data)