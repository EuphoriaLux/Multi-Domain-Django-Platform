"""Connect Showcase view: a coach swipes Crush Connect cards with a guest.

Who may appear, and why, lives in ``services.connect_showcase``. This view
only reads the coach's filters and renders the deck; swipes stay in the
browser and are never sent back.
"""

import logging

from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from crush_lu.decorators import coach_required
from crush_lu.forms_crush_connect import ConnectShowcaseFilterForm
from crush_lu.models import MeetupEvent
from crush_lu.services.connect_showcase import draw_showcase_deck, get_showcase_pool

logger = logging.getLogger(__name__)


@coach_required
@require_GET
def coach_connect_showcase(request, event_id):
    """Filter screen first; a shuffled deck once the coach presses Show.

    ``show`` in the query string asks for the deck. Without it, any filters
    in the query string only pre-fill the form ("Change filters").
    """
    event = get_object_or_404(MeetupEvent, pk=event_id)
    form = ConnectShowcaseFilterForm(request.GET or None)
    context = {"event": event, "form": form, "deck": None}

    if "show" in request.GET and form.is_valid():
        data = form.cleaned_data
        pool = get_showcase_pool(
            event=event,
            coach_user=request.user,
            genders=data["genders"],
            age_min=data["age_min"],
            age_max=data["age_max"],
            languages=data["languages"],
            guest_gender=data["guest_gender"],
            guest_age=data["guest_age"],
        )
        deck = draw_showcase_deck(pool)
        filters = request.GET.copy()
        filters.pop("show", None)
        context.update(
            deck=deck,
            pool_size=len(pool),
            filters_query=filters.urlencode(),
        )
        logger.info(
            "Connect showcase deck: coach=%s event=%s pool=%s shown=%s",
            request.coach.pk,
            event.pk,
            len(pool),
            len(deck),
        )

    return render(request, "crush_lu/crush_connect/coach_showcase.html", context)
