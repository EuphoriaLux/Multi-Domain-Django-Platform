"""
Template tags for the Crush Connect feature flag.

Lets templates conditionally render Crush Connect UI without each template
having to import settings or reach into the request.

Usage:
    {% load crush_connect_tags %}

    {% if user|crush_connect_visible %}
        <a href="{% url 'crush_lu:crush_connect_home' %}">Crush Connect</a>
    {% endif %}
"""

from django import template
from django.conf import settings

register = template.Library()


@register.filter
def crush_connect_visible(user):
    """
    True if the Crush Connect entry point (teaser or app) should be visible.

    Visible when the candidate track is open (full launch OR the beta
    candidate-open phase), OR the user is staff (so internal review and beta
    walkthroughs work before public launch). Eligibility (approved profile +
    LuxID) is enforced at the view layer; this filter only controls nav/CTA
    visibility.
    """
    from crush_lu.connect_phase import candidate_access_open

    if candidate_access_open():
        return True
    return bool(user and user.is_authenticated and user.is_staff)


@register.filter
def crush_connect_nav_visible(user):
    """
    True if the persistent Crush Connect nav (navbar / bottom nav / subnav)
    should be visible to this user.

    Stricter than ``crush_connect_visible``: shown only to Connect-onboarded
    members, so the nav appears once someone is actually in — for BOTH tracks
    (candidates and receivers) whenever the candidate track is open (beta or full
    launch). The Drop link within routes a non-receiver to their catalogue
    status, so beta candidates keep their Connect / Profile / Sparks navigation.
    Staff bypass keeps internal review working before anyone has onboarded.
    """
    from crush_lu.connect_phase import candidate_access_open

    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    try:
        membership = getattr(user, "crush_connect_membership", None)
        if not (membership and membership.is_onboarded):
            return False
    except Exception:
        # The membership reverse lookup runs a DB query during template
        # rendering. A backend fault (DB error, or the broken async
        # sync-executor) must not 500 the whole page from the nav — just
        # hide the Connect link.
        return False
    return candidate_access_open()


@register.filter
def crush_connect_is_receiver(user):
    """
    True if this user is on the RECEIVER track (Today's Drop) right now, as
    opposed to the CANDIDATE track (In the Mix).

    Mirrors the view layer's source of truth — ``_user_can_receive_now``
    (active ``PremiumMembership`` AND a phase that lets them receive) — so the
    nav can never disagree with where a click actually lands.

    Deliberately NO ``or user.is_staff``. Creating a ``CrushCoach`` grants
    is_staff, so that bypass handed every coach a "Today's Drop" tab pointing at
    a page ``get_eligible_pool`` always renders empty for them — it has no staff
    bypass and gates on ``has_active_premium``. A coach without Premium is a
    candidate and belongs on In the Mix. Whether staff may PREVIEW the Drop page
    is a separate question, answered by ``_connect_access_blocker``.

    Never branch a Connect track on ``assigned_coach`` either: a coach is also
    assigned WITHOUT payment (the 0150 backfill,
    ``assign_coach_on_first_attendance`` on first event check-in), so a
    coach-based test offers Today's Drop to members whom
    ``_connect_access_blocker`` then bounces to their catalogue status.

    This lives here rather than in view context because the Connect sub-nav is a
    shared partial — only ``crush_connect_hub`` supplies ``is_receiver``, so a
    context variable would silently read as False on every other Connect page.
    """
    from crush_lu.views_crush_connect import _user_can_receive_now

    if not user or not user.is_authenticated:
        return False
    try:
        return bool(_user_can_receive_now(user))
    except Exception:
        # Same defensive stance as crush_connect_nav_visible: this runs DB
        # queries (profile + PremiumMembership) during template rendering, and
        # a backend fault must not 500 the page from the nav. Falling back to
        # the candidate tab is the safe default — it points at the surface a
        # non-receiver would be redirected to anyway.
        return False


@register.simple_tag
def crush_connect_launched():
    """Raw flag accessor for templates that need it directly."""
    return getattr(settings, "CRUSH_CONNECT_LAUNCHED", False)


# Per-option decoration for the shared _radio_tiles.html partial, keyed by the
# field's html_name so the partial needs nothing beyond the bound field.
# "prefer_not_say" deliberately shares one emoji across all fields so it reads
# as a single consistent gesture everywhere (including the height opt-out pill).
CHOICE_EMOJI = {
    "work_field": {
        "finance": "🏦",
        "eu_public": "🏛️",
        "it": "💻",
        "health": "🩺",
        "education": "📚",
        "legal": "⚖️",
        "construction": "🏗️",
        "hospitality": "🍽️",
        "logistics": "🚚",
        "creative": "🎨",
        "entrepreneur": "🚀",
        "student": "🎓",
        "other": "💼",
        "prefer_not_say": "🤫",
    },
    "education_level": {
        "high_school": "🏫",
        "vocational": "🛠️",
        "bachelor": "🎓",
        "master": "📜",
        "doctorate": "🔬",
        "prefer_not_say": "🤫",
    },
    "smoking": {
        "no": "🚭",
        "occasionally": "💨",
        "yes": "🚬",
        "prefer_not_say": "🤫",
    },
    "drinking": {
        "no": "🚱",
        "socially": "🥂",
        "regularly": "🍷",
        "prefer_not_say": "🤫",
    },
}

# Optional second line under a tile's label (lazily translated strings).
# Empty for the life section; future adopters (e.g. lifestyle/ideal-match
# steps) add entries here instead of passing dicts through the view.
CHOICE_DESC = {}


@register.filter
def choice_emoji(field_name, value):
    """Emoji for a choice tile: ``field.html_name|choice_emoji:value``."""
    return CHOICE_EMOJI.get(field_name, {}).get(value, "")


@register.filter
def choice_desc(field_name, value):
    """Description line for a choice tile: ``field.html_name|choice_desc:value``."""
    return str(CHOICE_DESC.get(field_name, {}).get(value, ""))
