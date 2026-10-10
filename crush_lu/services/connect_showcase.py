"""Connect Showcase: a coach swipes Crush Connect cards with a guest at an event.

The coach sets filters, then shows the guest a short shuffled deck of Connect
members on the coach's own phone, so the guest can see whether someone on
Crush Connect could fit. The guest is anonymous and nothing is stored: the
deck is a demonstration, not a matching flow.

Who can appear is the In the Mix catalogue (``filter_catalogue_eligible``:
verified identity, onboarded, not paused or excluded, photo-sharing consent,
active within 30 days, primary photo approved), narrowed further:

- **The live primary photo must be the published one.** The card is rendered
  for a coach, and ``serve_profile_photo`` hands active coaches the live
  upload. A member whose replacement photo is still waiting for review would
  otherwise show the guest a photo no coach has approved. Secondary photos
  follow the same rule per slot, in the card's gallery
  (``public_secondary_photo_fields`` with ``live_only``).
- **Nobody holding a seat at this event.** The Event Lobby only names people
  in the room to each other after mutual interest; a coach's phone must not
  point an anonymous guest at someone across the room.
- **Not the coach running the deck.**

The optional "guest" gender and age apply each candidate's own Connect
preferences, the reverse half of the mutual filter in ``get_eligible_pool``.
"""

from __future__ import annotations

import random
from datetime import timedelta

from django.contrib.auth import get_user_model

from crush_lu.models import EventRegistration
from crush_lu.models.events import SEAT_HOLDING_STATUSES
from crush_lu.services.crush_connect import _years_ago, filter_catalogue_eligible

User = get_user_model()

# A handful of cards per round, like the "few people" the photo consent names.
SHOWCASE_DECK_SIZE = 20


def get_showcase_pool(
    *,
    event,
    coach_user,
    genders=(),
    age_min=18,
    age_max=99,
    languages=(),
    guest_gender="",
    guest_age=None,
):
    """Every Connect member who matches the filters, as a list of users.

    Language overlap and the guest-gender check run in Python: JSON
    containment lookups are unreliable on SQLite (see ``get_eligible_pool``).
    """
    seat_holder_ids = EventRegistration.objects.filter(
        event=event, status__in=SEAT_HOLDING_STATUSES
    ).values("user_id")

    qs = (
        filter_catalogue_eligible(User.objects.all())
        .filter(_published_earlier_primary=False)
        .exclude(pk=coach_user.pk)
        .exclude(pk__in=seat_holder_ids)
    )
    if genders:
        qs = qs.filter(crushprofile__gender__in=genders)

    latest_dob = _years_ago(age_min)
    earliest_dob = _years_ago(age_max + 1) + timedelta(days=1)
    qs = qs.filter(
        crushprofile__date_of_birth__lte=latest_dob,
        crushprofile__date_of_birth__gte=earliest_dob,
    )

    if guest_age is not None:
        qs = qs.filter(
            crush_connect_membership__preferred_age_min__lte=guest_age,
            crush_connect_membership__preferred_age_max__gte=guest_age,
        )

    wanted_languages = set(languages)
    pool = []
    for user in qs.select_related("crushprofile", "crush_connect_membership"):
        membership = user.crush_connect_membership
        if (
            guest_gender
            and membership.preferred_genders
            and guest_gender not in membership.preferred_genders
        ):
            continue
        if wanted_languages:
            # The card falls back to the event profile's languages for members
            # who skipped the Connect languages step; filter on what it shows.
            spoken = set(
                membership.languages or user.crushprofile.event_languages or []
            )
            if not spoken & wanted_languages:
                continue
        pool.append(user)
    return pool


def draw_showcase_deck(pool, size=SHOWCASE_DECK_SIZE):
    """A fresh random sample of ``pool`` with the card data loaded."""
    picked_ids = [user.pk for user in random.sample(pool, min(size, len(pool)))]
    by_id = {
        user.pk: user
        for user in User.objects.filter(pk__in=picked_ids)
        .select_related("crushprofile", "crush_connect_membership")
        .prefetch_related(
            "crush_connect_membership__interests",
            # Read per photo slot by the card's gallery (photo_publication).
            "crushprofile__published_photos",
            "crushprofile__photo_review_states",
        )
    }
    return [by_id[pk] for pk in picked_ids if pk in by_id]
