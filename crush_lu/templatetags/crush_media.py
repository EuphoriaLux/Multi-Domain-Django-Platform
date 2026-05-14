"""
Template tags for secure media access in Crush.lu
"""

from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag
def profile_photo_url(profile, photo_field):
    """
    Generate secure URL for profile photo

    Usage in template:
        {% load crush_media %}
        <img src="{% profile_photo_url profile 'photo_1' %}" alt="Profile photo">

    Args:
        profile: CrushProfile instance
        photo_field: 'photo_1', 'photo_2', or 'photo_3'

    Returns:
        Secure URL to the photo
    """
    if not profile or not getattr(profile, photo_field, None):
        return ''

    return reverse('crush_lu:serve_profile_photo', kwargs={
        'user_id': profile.user.id,
        'photo_field': photo_field
    })


@register.filter
def has_photo(profile, photo_field):
    """
    Check if profile has a photo

    Usage in template:
        {% if profile|has_photo:'photo_1' %}
            <img src="{% profile_photo_url profile 'photo_1' %}">
        {% endif %}

    Args:
        profile: CrushProfile instance
        photo_field: 'photo_1', 'photo_2', or 'photo_3'

    Returns:
        Boolean
    """
    if not profile:
        return False
    photo = getattr(profile, photo_field, None)
    return bool(photo)


@register.filter
def split_interests(value):
    """Split a comma-separated interests string into a list of trimmed items.

    Usage: {% for tag in profile.interests|split_interests %}
    """
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@register.inclusion_tag('crush_lu/components/profile_photo.html')
def profile_photo(profile, photo_field, css_class='', alt_text='Profile photo',
                  fallback='initials'):
    """
    Render a profile photo with consistent fallback.

    Usage in template:
        {% load crush_media %}
        {% profile_photo profile 'photo_1' css_class='w-14 h-14 rounded-full' %}
        {% profile_photo profile 'photo_1' css_class='w-10 h-10 rounded-full' fallback='icon' %}

    Args:
        profile: CrushProfile instance
        photo_field: 'photo_1', 'photo_2', or 'photo_3'
        css_class: CSS classes to apply (sizing + shape — e.g. 'w-14 h-14 rounded-full')
        alt_text: Alt text for accessibility
        fallback: 'initials' (default — gradient + initial letter) or 'icon'
                  (neutral user-circle for non-personal placeholders)

    Returns:
        Rendered component
    """
    photo = getattr(profile, photo_field, None) if profile else None

    if photo:
        photo_url = reverse('crush_lu:serve_profile_photo', kwargs={
            'user_id': profile.user.id,
            'photo_field': photo_field
        })
    else:
        photo_url = None

    return {
        'photo_url': photo_url,
        'has_photo': bool(photo),
        'css_class': css_class,
        'alt_text': alt_text,
        'display_name': profile.display_name if profile else 'User',
        'fallback': fallback,
    }
