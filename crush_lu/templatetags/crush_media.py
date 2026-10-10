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
def has_public_photo(profile, photo_field):
    """Whether *other members* may see this photo slot.

    Use instead of ``has_photo`` on pages that show another member: a photo
    that no coach has approved yet is refused by the photo endpoint, so the
    page should render its fallback rather than a broken image.

        {% if other_profile|has_public_photo:'photo_1' %}
    """
    from crush_lu.services.photo_publication import get_public_photo_key

    return bool(profile) and bool(get_public_photo_key(profile, photo_field))


def _viewer_sees_live_photo(context, profile):
    """The owner, active coaches and superusers see the live upload."""
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    if user.pk == profile.user_id or user.is_superuser:
        return True
    cached = getattr(request, "_crush_viewer_is_coach", None)
    if cached is None:
        from crush_lu.models import CrushCoach

        cached = CrushCoach.objects.filter(user=user, is_active=True).exists()
        request._crush_viewer_is_coach = cached
    return cached


@register.filter
def split_interests(value):
    """Split a comma-separated interests string into a list of trimmed items.

    Usage: {% for tag in profile.interests|split_interests %}
    """
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@register.inclusion_tag('crush_lu/components/profile_photo.html', takes_context=True)
def profile_photo(context, profile, photo_field, css_class='', alt_text='Profile photo',
                  fallback='initials', hide_photo=False, public_view=False):
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
        hide_photo: render the fallback even when a photo exists — for
                  surfaces where the viewer may no longer load it (see
                  views_media.can_view_profile_photo), instead of a 403 image
        public_view: show what other members see even to a coach, decided by
                  the endpoint when the image loads (``?view=public``) — for
                  a page a coach shows to someone else (Connect Showcase)

    Returns:
        Rendered component
    """
    photo = getattr(profile, photo_field, None) if profile and not hide_photo else None
    if photo and (public_view or not _viewer_sees_live_photo(context, profile)):
        # Other members only ever see an approved photo (photo_publication):
        # render the fallback instead of a URL the endpoint refuses.
        from crush_lu.services.photo_publication import get_public_photo_key

        if not get_public_photo_key(profile, photo_field):
            photo = None

    if photo:
        photo_url = reverse('crush_lu:serve_profile_photo', kwargs={
            'user_id': profile.user.id,
            'photo_field': photo_field
        })
        if public_view:
            photo_url += '?view=public'
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


@register.simple_tag(takes_context=True)
def member_photo_review(context, slot):
    """What the member should know about their own photo in ``slot``.

    Returns ``None`` when there is nothing to say, else a dict with
    ``state`` (``needs_replacement`` / ``in_review``), and for a replacement
    request the coach's ``reason_label`` and ``advice``; ``held`` is True when
    other members still see an earlier approved photo.
    """
    from crush_lu.models import CrushProfile, ProfilePhotoReviewLog
    from crush_lu.photo_review_reasons import photo_revision_items
    from crush_lu.services.photo_publication import get_published_key

    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    photo_field = f"photo_{slot}"
    profile = (
        CrushProfile.objects.filter(user=user)
        .prefetch_related("photo_review_states", "published_photos")
        .first()
    )
    if profile is None or photo_field not in ("photo_1", "photo_2", "photo_3"):
        return None
    live = getattr(getattr(profile, photo_field), "name", "") or ""
    if not live:
        return None
    status = profile.get_photo_field_review_status(photo_field)
    published = get_published_key(profile, photo_field)
    if status == "needs_revision":
        reason = (
            ProfilePhotoReviewLog.objects.filter(
                profile=profile,
                photo_field=photo_field,
                photo_key=live,
                decision="needs_revision",
                undone_at__isnull=True,
            )
            .order_by("-pk")
            .values_list("reason", flat=True)
            .first()
        )
        item = photo_revision_items([{"photo_field": photo_field, "reason": reason}])[0]
        return {
            "state": "needs_replacement",
            "reason_label": item["reason_label"],
            "advice": item["advice"],
            "held": bool(published and published != live),
        }
    if status == "pending":
        return {"state": "in_review", "held": bool(published and published != live)}
    return None
