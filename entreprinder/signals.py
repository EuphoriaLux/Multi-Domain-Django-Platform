from allauth.account.signals import user_logged_in
from django.dispatch import receiver
from rest_framework_simplejwt.tokens import RefreshToken

@receiver(user_logged_in)
def generate_jwt_token(sender, request, user, **kwargs):
    refresh = RefreshToken.for_user(user)
    access_token = str(refresh.access_token)
    # Implement a mechanism to send the token to the Chrome extension
    # This could be via a secure API endpoint or other methods

