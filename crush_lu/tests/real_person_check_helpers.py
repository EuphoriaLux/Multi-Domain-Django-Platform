"""Fixtures shared by the Connect real-person check tests.

Not collected by pytest (the name does not match ``test_*.py``).

``_make_user`` gives every member the same ``photo_1`` key and no Crush.lu
consent. The check compares exact photo keys and the coach queue filters on
consent, so these helpers give each member their own key (on the profile and
on its approved review state, or the catalogue gate drops them) and consent.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from django.contrib.auth import get_user_model
from django.test import override_settings

from crush_lu.models import CrushCoach, CrushProfile, ProfilePhotoReviewState
from crush_lu.tests.test_crush_connect import _grant_consent, _make_user

User = get_user_model()

FLAGS_ON = override_settings(
    CONNECT_REAL_PERSON_CHECK_ENABLED=True,
    CONNECT_REAL_PERSON_BADGE_VISIBLE=True,
)


def photo_key_for(user, suffix="a"):
    return f"users/{user.pk}/photos/{user.username}-{suffix}.jpg"


def set_photo(user, key):
    """Point photo_1 and its approved review state at ``key`` (no save())."""
    CrushProfile.objects.filter(user=user).update(photo_1=key)
    ProfilePhotoReviewState.objects.filter(
        profile__user=user, photo_field="photo_1"
    ).update(photo_key=key)


def make_member(username, **kwargs):
    """A catalogue member no coach has met: LuxID only, consent, own photo."""
    kwargs.setdefault("premium", False)
    user = _make_user(username=username, **kwargs)
    set_photo(user, photo_key_for(user))
    _grant_consent(user)
    return user


def make_coach(name="rpc_coach", active=True):
    user = User.objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password="pw12345678",
        first_name=name.title(),
    )
    return CrushCoach.objects.create(user=user, bio="coach", is_active=active)


def profile_of(user):
    return CrushProfile.objects.select_related("user").get(user=user)
