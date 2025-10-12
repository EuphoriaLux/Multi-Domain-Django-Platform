# Crush.lu Social Authentication Setup Guide

This guide shows how to add social sign-in (LinkedIn, Google, Facebook) to Crush.lu using Django AllAuth.

## Current Status

✅ **Already Configured**:
- Django AllAuth is installed and configured
- Multiple authentication backends are set up in [settings.py](azureproject/settings.py#L181-L184)
- Signup view is prepared for social auth (sets `user.backend` attribute)

## Supported Providers

The application already has AllAuth configured. You can easily add:
- LinkedIn OAuth2 (professional networking - good fit for Crush.lu)
- Google OAuth2 (most popular)
- Facebook Login
- Microsoft Account
- GitHub
- Twitter/X

## Quick Setup: LinkedIn OAuth2

LinkedIn is a good choice for Crush.lu since it's professional and includes profile photos.

### 1. LinkedIn Developer Setup (10 min)

#### 1.1 Create LinkedIn App

1. Go to [LinkedIn Developers](https://www.linkedin.com/developers/apps)
2. Click **Create app**
3. Fill in details:
   ```
   App name: Crush.lu
   LinkedIn Page: (your company page or create one)
   App logo: Upload your Crush.lu logo
   Legal agreement: ✓ Accept
   ```
4. Click **Create app**

#### 1.2 Configure OAuth Settings

1. Go to **Auth** tab
2. Add **Authorized redirect URLs**:
   ```
   https://crush.lu/accounts/linkedin_oauth2/login/callback/
   https://www.crush.lu/accounts/linkedin_oauth2/login/callback/
   ```
3. Copy **Client ID** and **Client Secret**

#### 1.3 Request Profile Permissions

1. Go to **Products** tab
2. Request access to:
   - ✓ Sign In with LinkedIn using OpenID Connect
   - ✓ Share on LinkedIn (optional)
3. Wait for approval (usually instant)

### 2. Update Django Settings

Add LinkedIn provider to [azureproject/settings.py](azureproject/settings.py):

```python
# Around line 46, INSTALLED_APPS already has:
'allauth.socialaccount.providers.linkedin_oauth2',  # Already installed!

# Add after line 224 (after ACCOUNT_DEFAULT_HTTP_PROTOCOL):
SOCIALACCOUNT_PROVIDERS = {
    'linkedin_oauth2': {
        'SCOPE': [
            'openid',
            'profile',
            'email',
        ],
        'PROFILE_FIELDS': [
            'id',
            'first-name',
            'last-name',
            'email-address',
            'picture-url',
        ],
    }
}
```

### 3. Add Social Auth to Azure Environment Variables

In Azure App Service → Configuration:

```bash
# LinkedIn OAuth
SOCIALACCOUNT_PROVIDERS_LINKEDIN_OAUTH2_APP_ID=<your-client-id>
SOCIALACCOUNT_PROVIDERS_LINKEDIN_OAUTH2_APP_SECRET=<your-client-secret>
```

**Or** configure via Django Admin (preferred for multiple providers):

### 4. Configure via Django Admin (Recommended)

1. Log into Django admin: https://crush.lu/admin/
2. Go to **Sites** → Click on `example.com`
3. Change to:
   ```
   Domain name: crush.lu
   Display name: Crush.lu
   ```
4. Go to **Social applications** → **Add social application**
5. Fill in:
   ```
   Provider: LinkedIn OAuth2
   Name: LinkedIn
   Client id: <your-client-id>
   Secret key: <your-client-secret>
   Sites: Move crush.lu to "Chosen sites" →
   ```
6. Click **Save**

### 5. Update Crush.lu Templates

Add LinkedIn sign-in button to [crush_lu/templates/crush_lu/signup.html](crush_lu/templates/crush_lu/signup.html):

```html
{% load socialaccount %}

<div class="social-auth-buttons mb-4">
    <a href="{% provider_login_url 'linkedin_oauth2' %}"
       class="btn btn-primary btn-block">
        <i class="fab fa-linkedin"></i> Continue with LinkedIn
    </a>
</div>

<div class="or-divider my-4">
    <span>OR</span>
</div>

<!-- Existing email/password form -->
<form method="post">
    <!-- ... -->
</form>
```

Also update [crush_lu/templates/crush_lu/login.html](crush_lu/templates/crush_lu/login.html) similarly.

### 6. Handle Social Auth Signup

Create a signal handler to create Crush profile for social signups:

Create `crush_lu/signals.py`:

```python
from django.dispatch import receiver
from allauth.socialaccount.signals import pre_social_login
from .models import CrushProfile

@receiver(pre_social_login)
def link_to_local_user(sender, request, sociallogin, **kwargs):
    """
    Auto-link social accounts to existing users by email.
    Redirect new social users to profile creation.
    """
    # Get email from social account
    email_address = sociallogin.account.extra_data.get('email')

    if email_address:
        from django.contrib.auth import get_user_model
        User = get_user_model()

        try:
            # Check if user with this email exists
            user = User.objects.get(email=email_address)
            # Link social account to existing user
            sociallogin.connect(request, user)
        except User.DoesNotExist:
            # New user - will be created by AllAuth
            pass
```

Register signals in `crush_lu/apps.py`:

```python
from django.apps import AppConfig

class CrushLuConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crush_lu'

    def ready(self):
        import crush_lu.signals  # Register signals
```

### 7. Update AllAuth Settings

Add to [azureproject/settings.py](azureproject/settings.py) (around line 190):

```python
# Social account settings
SOCIALACCOUNT_AUTO_SIGNUP = True  # Auto-create account from social login
SOCIALACCOUNT_EMAIL_REQUIRED = True  # Require email from provider
SOCIALACCOUNT_EMAIL_VERIFICATION = 'optional'  # Trust verified social emails
SOCIALACCOUNT_QUERY_EMAIL = True  # Ask for email if not provided

# Redirect after social login
SOCIALACCOUNT_LOGIN_ON_GET = True  # Allow GET requests for social auth
LOGIN_REDIRECT_URL = '/create-profile/'  # Redirect to profile creation
```

### 8. Testing

1. Go to https://crush.lu/signup/
2. Click "Continue with LinkedIn"
3. Authorize the app
4. Should redirect back and auto-login
5. Redirect to profile creation page

## Adding Google OAuth2

Google is the most popular social login provider.

### 1. Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create new project: "Crush.lu"
3. Enable **Google+ API**
4. Go to **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
5. Configure OAuth consent screen:
   ```
   App name: Crush.lu
   User support email: your-email@crush.lu
   Developer contact: your-email@crush.lu
   Scopes: email, profile, openid
   ```
6. Create OAuth Client ID:
   ```
   Application type: Web application
   Name: Crush.lu Production
   Authorized redirect URIs:
     https://crush.lu/accounts/google/login/callback/
     https://www.crush.lu/accounts/google/login/callback/
   ```
7. Copy **Client ID** and **Client Secret**

### 2. Install Google Provider

Already installed! Check [azureproject/settings.py](azureproject/settings.py) - AllAuth is configured.

Add to INSTALLED_APPS if not present:
```python
'allauth.socialaccount.providers.google',
```

### 3. Configure in Django Admin

1. Django Admin → **Social applications** → **Add**
2. Fill in:
   ```
   Provider: Google
   Name: Google
   Client id: <your-google-client-id>
   Secret key: <your-google-client-secret>
   Sites: crush.lu
   ```

### 4. Add Google Button to Templates

```html
<a href="{% provider_login_url 'google' %}"
   class="btn btn-light btn-block">
    <i class="fab fa-google"></i> Continue with Google
</a>
```

## Multiple Social Providers Example

Updated signup/login template with multiple providers:

```html
{% load socialaccount %}

<div class="social-auth-section mb-4">
    <h5 class="text-center mb-3">Sign in with</h5>

    <div class="d-grid gap-2">
        <!-- LinkedIn -->
        <a href="{% provider_login_url 'linkedin_oauth2' %}"
           class="btn btn-primary">
            <i class="fab fa-linkedin me-2"></i> LinkedIn
        </a>

        <!-- Google -->
        <a href="{% provider_login_url 'google' %}"
           class="btn btn-light border">
            <i class="fab fa-google me-2"></i> Google
        </a>

        <!-- Facebook (optional) -->
        <a href="{% provider_login_url 'facebook' %}"
           class="btn btn-primary" style="background-color: #1877f2;">
            <i class="fab fa-facebook me-2"></i> Facebook
        </a>
    </div>
</div>

<div class="text-center my-4">
    <span class="px-3 bg-white text-muted">OR</span>
    <hr class="mt-n3">
</div>

<!-- Email/Password Form -->
<form method="post" class="signup-form">
    {% csrf_token %}
    {{ form.as_p }}
    <button type="submit" class="btn btn-crush-primary w-100">
        Sign Up with Email
    </button>
</form>
```

## Profile Data from Social Auth

Access social account data in views:

```python
from allauth.socialaccount.models import SocialAccount

def create_profile(request):
    # Get social account data if available
    social_accounts = SocialAccount.objects.filter(user=request.user)

    if social_accounts.exists():
        social_account = social_accounts.first()
        extra_data = social_account.extra_data

        # LinkedIn data
        if social_account.provider == 'linkedin_oauth2':
            profile_picture = extra_data.get('picture')
            first_name = extra_data.get('localizedFirstName')
            last_name = extra_data.get('localizedLastName')

        # Google data
        elif social_account.provider == 'google':
            profile_picture = extra_data.get('picture')
            first_name = extra_data.get('given_name')
            last_name = extra_data.get('family_name')

        # Pre-fill form with social data
        initial_data = {
            'first_name': first_name,
            'last_name': last_name,
        }
        form = CrushProfileForm(initial=initial_data)
    else:
        form = CrushProfileForm()

    # ... rest of view
```

## Auto-Download Profile Pictures

Create a helper in [crush_lu/utils.py](crush_lu/utils.py):

```python
import requests
from django.core.files.base import ContentFile
from allauth.socialaccount.models import SocialAccount

def download_social_profile_picture(user):
    """Download profile picture from social account"""
    social_accounts = SocialAccount.objects.filter(user=user)

    if not social_accounts.exists():
        return None

    social_account = social_accounts.first()
    extra_data = social_account.extra_data
    picture_url = None

    # Get picture URL based on provider
    if social_account.provider == 'linkedin_oauth2':
        picture_url = extra_data.get('picture')
    elif social_account.provider == 'google':
        picture_url = extra_data.get('picture')
    elif social_account.provider == 'facebook':
        fb_id = extra_data.get('id')
        picture_url = f"https://graph.facebook.com/{fb_id}/picture?type=large"

    if picture_url:
        try:
            response = requests.get(picture_url, timeout=10)
            if response.status_code == 200:
                # Return file content
                filename = f"{user.username}_social_photo.jpg"
                return ContentFile(response.content, name=filename)
        except Exception as e:
            import logging
            logging.error(f"Failed to download social profile picture: {e}")

    return None

# Use in create_profile view:
def create_profile(request):
    if request.method == 'POST':
        form = CrushProfileForm(request.POST, request.FILES)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user

            # Auto-download social profile picture if no photo uploaded
            if not form.cleaned_data.get('photo1'):
                social_photo = download_social_profile_picture(request.user)
                if social_photo:
                    profile.photo1.save(social_photo.name, social_photo, save=False)

            profile.save()
            # ... rest of code
```

## URL Configuration

Ensure AllAuth URLs are included. Check [azureproject/urls_crush.py](azureproject/urls_crush.py):

```python
from django.urls import path, include

urlpatterns = [
    # ... existing crush.lu URLs

    # AllAuth URLs (for social auth)
    path('accounts/', include('allauth.urls')),

    # ... rest of URLs
]
```

## Privacy Considerations for Crush.lu

Since Crush.lu is privacy-focused, consider:

### 1. Don't Auto-Public Social Data

```python
# In profile creation, mark profiles from social auth as needing approval
if SocialAccount.objects.filter(user=request.user).exists():
    profile.is_from_social_auth = True  # Add this field to model
    profile.is_approved = False  # Still requires coach approval
```

### 2. Allow Disconnecting Social Accounts

Add to user settings:

```html
{% load socialaccount %}

<h3>Connected Accounts</h3>
{% get_social_accounts user as accounts %}
{% for account in accounts %}
    <div class="social-account-item">
        <strong>{{ account.get_provider_display }}</strong>
        <form method="post" action="{% url 'socialaccount_connections' %}">
            {% csrf_token %}
            <input type="hidden" name="account" value="{{ account.id }}">
            <button type="submit" name="action" value="remove">Disconnect</button>
        </form>
    </div>
{% endfor %}
```

### 3. Don't Share Social Data with Other Users

Only use social data for:
- Pre-filling signup forms
- Profile pictures (with user consent)
- Email verification

Never show "Connected via LinkedIn" or similar to other users.

## Testing Checklist

- [ ] Create LinkedIn/Google app
- [ ] Configure OAuth redirect URLs
- [ ] Add credentials to Django Admin
- [ ] Update signup/login templates with social buttons
- [ ] Test LinkedIn sign-in flow
- [ ] Test Google sign-in flow
- [ ] Verify profile creation redirect
- [ ] Test profile picture download
- [ ] Test email linking (existing user)
- [ ] Verify coach approval still required
- [ ] Test account disconnection

## Troubleshooting

### "Error 400: redirect_uri_mismatch"
**Fix**: Ensure redirect URI in provider settings exactly matches:
```
https://crush.lu/accounts/{provider}/login/callback/
```

### Social login works but redirects to wrong site
**Fix**: Update Site in Django Admin to use correct domain (crush.lu)

### Profile picture not downloading
**Fix**: Check Azure allows outbound HTTPS requests, verify URL in social data

### Email already exists error
**Fix**: Implement `pre_social_login` signal to link accounts by email

## Additional Resources

- [Django AllAuth Documentation](https://django-allauth.readthedocs.io/)
- [LinkedIn OAuth2 Guide](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authentication)
- [Google OAuth2 Setup](https://developers.google.com/identity/protocols/oauth2)
- [Facebook Login](https://developers.facebook.com/docs/facebook-login)

## Summary

The signup view is now ready for social authentication. When you're ready to add social login:

1. Choose provider(s): LinkedIn, Google, Facebook
2. Create app in provider's developer console
3. Add credentials to Django Admin → Social applications
4. Update templates with social login buttons
5. Test the flow

The infrastructure is already in place - just add the provider configs!
