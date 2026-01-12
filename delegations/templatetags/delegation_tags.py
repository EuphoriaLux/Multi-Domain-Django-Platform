"""Custom template tags for Crush Delegation app."""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def get_microsoft_login_url(context):
    """
    Safely get the Microsoft OAuth login URL.

    Returns the login URL if Microsoft provider is configured, otherwise None.
    This prevents SocialApp.DoesNotExist errors from crashing the template.
    """
    from allauth.socialaccount.models import SocialApp

    request = context.get('request')
    if not request:
        return None

    try:
        # Check if Microsoft SocialApp exists for any site
        if not SocialApp.objects.filter(provider='microsoft').exists():
            return None

        # Get the URL using allauth's helper
        from allauth.socialaccount.templatetags.socialaccount import provider_login_url
        return provider_login_url(context, 'microsoft')
    except SocialApp.DoesNotExist:
        return None
    except Exception:
        return None
