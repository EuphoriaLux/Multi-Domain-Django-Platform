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


@register.inclusion_tag('crush_lu/components/profile_photo.html')
def profile_photo(profile, photo_field, css_class='', alt_text='Profile photo'):
    """
    Render a profile photo with proper security and fallback

    Usage in template:
        {% load crush_media %}
        {% profile_photo profile 'photo_1' css_class='img-fluid rounded' %}

    Args:
        profile: CrushProfile instance
        photo_field: 'photo_1', 'photo_2', or 'photo_3'
        css_class: CSS classes to apply
        alt_text: Alt text for accessibility

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
        'display_name': profile.display_name if profile else 'User'
    }
