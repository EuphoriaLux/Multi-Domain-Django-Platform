from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.http import HttpResponse
from django.db import transaction
from django.db.models import Q
from datetime import timedelta
import json
import logging

from .models import (
    CrushProfile,
    MeetupEvent,
    EventRegistration,
    EventRegistrationPreference,
    EventInvitation,
    EventFeedback,
    PremiumMembership,
)
from .models.event_polls import EventPoll
from .models.events import SEAT_HOLDING_STATUSES
from .models.payments import PaymentTransaction
from .models.credits import CrushCredit
from .forms import EventRegistrationForm, EventPreferenceForm, EventFeedbackForm
from .decorators import crush_login_required, ratelimit
from .services.credits import (
    available_credit_cents,
    is_late_cancellation,
    issue_cancellation_credits,
    paid_amount_cents,
    settle_pending_resale_credit,
)
from .email_helpers import (
    send_event_payment_pending_notification,
    send_event_registration_confirmation,
    send_event_waitlist_notification,
    send_event_cancellation_confirmation,
)

logger = logging.getLogger(__name__)


def _admitted_status(event, registration=None):
    """The status an admitted registration takes on this event.

    A paid event holds the seat as "pending" (Pending Payment) until the SumUp
    return handler confirms it; a free event confirms straight away. Used by
    both the signup path and waitlist promotion so the two cannot disagree --
    promoting someone off the waitlist must not hand them a confirmed seat on a
    paid event they have not paid for.

    An already-paid registration stays "confirmed". This matters when a
    cancelled row is reused on re-registration: the row keeps
    ``payment_confirmed``, so forcing it back to "pending" would ask the member
    to pay a second time for money we still hold, mail them a payment request,
    and then have the checkout reject them as already paid. Re-registration is
    "brand new" for *queue position* (registered_at is reset); that is a
    separate question from whether the seat has been paid for.
    """
    if registration is not None and registration.payment_confirmed:
        return "confirmed"
    return "pending" if event.registration_fee > 0 else "confirmed"


def _resale_claim_from(source, event):
    """Return the durable claim carried by one cancelled registration."""
    if source is None:
        return None

    # An unpaid replacement carries the original payer's claim. Forward that
    # first; the intermediary has paid nothing of its own yet.
    if source.resale_beneficiary_id and (
        source.resale_source_registration_id or source.resale_source_payment_id
    ):
        return (
            source.resale_source_registration_id,
            source.resale_source_payment_id,
            source.resale_beneficiary_id,
        )

    if (
        source.status != "cancelled"
        or source.cancelled_at is None
        or not is_late_cancellation(event, source.cancelled_at)
    ):
        return None

    source_payment = None
    if source.payment_confirmed:
        _amount, source_payment = paid_amount_cents(source)
    else:
        # The member can cancel while their SumUp widget is still payable.
        # This pending payment makes the released-seat claim contingent: it is
        # settled only if both that checkout and the replacement later capture.
        source_payment = (
            PaymentTransaction.objects.filter(
                event_registration=source,
                provider=PaymentTransaction.Provider.SUMUP,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                status=PaymentTransaction.Status.PENDING,
            )
            .order_by("-created_at", "-pk")
            .first()
        )
    if source_payment is not None:
        return (
            source.pk,
            source_payment.pk,
            source.user_id,
        )
    payment_returned = CrushCredit.objects.filter(
        source_registration=source,
        reason__in=(
            CrushCredit.Reason.MEMBER_CANCELLATION,
            CrushCredit.Reason.SEAT_RESOLD,
            CrushCredit.Reason.EVENT_CANCELLED,
        ),
    ).exists()
    captured_payment_exists = PaymentTransaction.objects.filter(
        event_registration=source,
        purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
        status=PaymentTransaction.Status.PAID,
    ).exists()
    if (
        not source.payment_confirmed
        and event.registration_fee > 0
        and not payment_returned
        and not captured_payment_exists
    ):
        # Cash/bank transfers can be recorded after the member cancelled. Keep
        # a source-only contingent claim now; settlement waits until an actual
        # MANUAL capture exists and payment_confirmed becomes true. A captured
        # or already-returned payment is a completed cycle, not future cash.
        return (source.pk, None, source.user_id)
    return None


def _set_resale_claim(registration, claim):
    source_id, payment_id, beneficiary_id = claim
    registration.resale_source_registration_id = source_id
    registration.resale_source_payment_id = payment_id
    registration.resale_beneficiary_id = beneficiary_id


def _attach_unclaimed_resale_claim(registration, event):
    """Attach the oldest released paid seat not already assigned elsewhere.

    The cancellation transaction can have no promotable waitlistee. A later
    newcomer who directly takes that released seat must still be able to earn
    the original member's 50% share once they pay.
    """
    sources = (
        EventRegistration.objects.select_for_update()
        .filter(event=event, status="cancelled")
        .exclude(pk=registration.pk)
        .order_by("cancelled_at", "pk")
    )
    for source in sources:
        claim = _resale_claim_from(source, event)
        if claim is None:
            continue
        source_id, payment_id, _beneficiary_id = claim
        assigned = Q()
        if source_id:
            assigned |= Q(resale_source_registration_id=source_id)
        if payment_id:
            assigned |= Q(resale_source_payment_id=payment_id)
        if (source_id or payment_id) and (
            EventRegistration.objects.filter(
                event=event, status__in=SEAT_HOLDING_STATUSES
            )
            .exclude(pk=registration.pk)
            # A cancelled, waitlisted, or no-show replacement has released the
            # seat; only someone currently holding it makes the claim busy.
            .filter(assigned)
            .exists()
        ):
            continue
        _set_resale_claim(registration, claim)
        registration.save(
            update_fields=[
                "resale_source_registration",
                "resale_source_payment",
                "resale_beneficiary",
            ]
        )
        return source
    return None


def _promote_from_waitlist(event, cancelled_user=None, resale_source_registration=None):
    """
    Promote the best waitlisted candidate to confirmed.

    When gender limits are active:
    1. Try to promote a waitlisted user from the same gender pool as the
       cancelled user (maintains balance).
    2. If no same-pool candidate, try any waitlisted user whose pool has room.

    When gender limits are inactive: simple FIFO.

    Must be called inside a transaction with the event locked via
    select_for_update().

    Returns the promoted EventRegistration, or None.
    """
    # NOTE: Do NOT use select_related("user__crushprofile") here — it creates
    # a LEFT OUTER JOIN, and PostgreSQL forbids FOR UPDATE on the nullable
    # side of an outer join.  Profiles are fetched in a separate query below.
    waitlisted = (
        EventRegistration.objects.select_for_update()
        .filter(event=event, status="waitlist")
        .order_by("registered_at")
    )

    if not waitlisted.exists():
        return None

    # Prefetch profiles in a separate query (no FOR UPDATE conflict). Used for
    # the gender pools; the premium check reads its own set just below.
    waitlisted_list = list(waitlisted)
    user_ids = [reg.user_id for reg in waitlisted_list]
    profiles_by_user = {
        p.user_id: p for p in CrushProfile.objects.filter(user_id__in=user_ids)
    }
    # Premium is an ACTIVE PremiumMembership -- the entitlement
    # `CrushProfile.has_active_premium` names -- and NOT `assigned_coach`, which
    # is also granted free on first attendance and by the 0150 backfill.
    # Read as one set rather than through that property: this decides every
    # waitlisted row and the property costs a query per call.
    premium_user_ids = set(
        PremiumMembership.objects.filter(
            user_id__in=user_ids, status="active"
        ).values_list("user_id", flat=True)
    )

    def _get_gender(reg):
        profile = profiles_by_user.get(reg.user_id)
        return profile.gender if profile else None

    def _is_premium(reg):
        # Premium members may take a reserved seat (measured against total
        # capacity); general members are capped at public_capacity so the
        # reserved block stays held back.
        return reg.user_id in premium_user_ids

    def _promote(candidate):
        candidate.status = _admitted_status(event, candidate)
        candidate.resale_source_registration = None
        candidate.resale_source_payment = None
        candidate.resale_beneficiary = None
        claim = _resale_claim_from(resale_source_registration, event)
        if claim is not None:
            _set_resale_claim(candidate, claim)
        candidate.save()
        if claim is None:
            _attach_unclaimed_resale_claim(candidate, event)
        # A reused waitlist row can already carry a valid payment. Settle that
        # rare case now; ordinary paid-event promotions remain pending until
        # _apply_paid_checkout confirms their replacement payment.
        if candidate.payment_confirmed:
            settle_pending_resale_credit(candidate)
        return candidate

    # If gender limits are not active, promote the first in line who still has
    # a seat under their own capacity (general → public, premium → total).
    if not event.gender_limits_active:
        for candidate in waitlisted_list:
            if not event.is_full_for(is_premium=_is_premium(candidate)):
                return _promote(candidate)
        return None

    # Gender-aware promotion
    cancelled_gender = None
    if cancelled_user:
        cancelled_profile = getattr(cancelled_user, "crushprofile", None)
        if cancelled_profile:
            cancelled_gender = cancelled_profile.gender

    # 1. Try same-pool candidates first
    if cancelled_gender:
        pool = event.get_gender_pool(cancelled_gender)
        if pool:
            pool_codes = event.POOL_TO_CODES.get(pool, [])
            for candidate in waitlisted_list:
                cand_gender = _get_gender(candidate)
                if cand_gender in pool_codes:
                    if not event.is_full_for(
                        is_premium=_is_premium(candidate)
                    ) and not event.is_gender_pool_full(cand_gender):
                        return _promote(candidate)

    # 2. Try any waitlisted user whose pool (and overall capacity) has room
    for candidate in waitlisted_list:
        cand_gender = _get_gender(candidate)
        if not cand_gender or event.is_gender_pool_full(cand_gender):
            continue
        if event.is_full_for(is_premium=_is_premium(candidate)):
            continue
        return _promote(candidate)

    return None


def _postal_address(event):
    """schema.org PostalAddress for an event, one field per component.

    Built per-component rather than from `full_address`: a composed one-liner
    in `streetAddress` is the same "structure recovered from free text" the
    structured fields exist to end, and search engines read these keys
    separately.

    Legacy rows with no structured street fall back to the legacy text, which
    is less precise but as informative as the record gets. Every key is dropped
    when empty rather than published blank.

    The fallback is the raw legacy text, *not* `full_address`: that composes
    the postcode and town into the string, so a part-transcribed row with no
    street would publish "L-2229 Luxembourg" as its street address while
    `postalCode` and `addressLocality` said the same thing again.
    """
    postal = {"@type": "PostalAddress", "addressCountry": "LU"}
    street = event.street_line or (event.address or "").strip()
    if street:
        postal["streetAddress"] = street
    if event.address_postcode:
        postal["postalCode"] = event.address_postcode
    # The locality is the town. It used to be `event.location`, which is the
    # venue *name* -- "Café Konrad" was being published as a town.
    locality = event.address_town or event.canton
    if locality:
        postal["addressLocality"] = locality
    if event.canton:
        postal["addressRegion"] = event.canton
    return postal


def _filter_private_events(events, user):
    """Filter out private invitation events unless user is invited."""
    if not user.is_authenticated:
        return [e for e in events if not e.is_private_invitation]

    # Batch-fetch invitation data to avoid N+1 queries
    private_events = [e for e in events if e.is_private_invitation]
    if private_events:
        private_ids = [e.id for e in private_events]
        invited_event_ids = set(
            MeetupEvent.objects.filter(
                id__in=private_ids, invited_users=user
            ).values_list("id", flat=True)
        )
        approved_event_ids = set(
            EventInvitation.objects.filter(
                event_id__in=private_ids,
                created_user=user,
                approval_status="approved",
            ).values_list("event_id", flat=True)
        )
        allowed_ids = invited_event_ids | approved_event_ids
    else:
        allowed_ids = set()

    return [e for e in events if not e.is_private_invitation or e.id in allowed_ids]


def event_list(request):
    """List of upcoming and past events"""
    now = timezone.now()

    # Fetch published, non-cancelled events and split into upcoming/past
    # in Python using the model's end_time property. This avoids
    # timedelta * F() which is not supported on SQLite. The cutoff is the
    # enforced max event duration, so an in-progress event is never dropped
    # regardless of its length (see MeetupEvent.live_lookback_cutoff).
    generous_cutoff = MeetupEvent.live_lookback_cutoff(now)
    upcoming_events = list(
        MeetupEvent.objects.with_registration_counts()
        .filter(is_published=True, is_cancelled=False, date_time__gte=generous_cutoff)
        .prefetch_related("coaches__user")
        .order_by("date_time")
    )
    upcoming_events = [e for e in upcoming_events if e.end_time >= now]

    boundary_past = [
        event
        for event in MeetupEvent.objects.with_registration_counts()
        .filter(
            is_published=True,
            is_cancelled=False,
            date_time__gte=generous_cutoff,
            date_time__lt=now,
        )
        .order_by("-date_time")
        if event.end_time < now
    ]
    older_past = list(
        MeetupEvent.objects.with_registration_counts()
        .filter(
            is_published=True,
            is_cancelled=False,
            date_time__lt=generous_cutoff,
        )
        .order_by("-date_time")[:10]
    )
    past_events = (boundary_past + older_past)[:10]

    visible_upcoming = _filter_private_events(upcoming_events, request.user)
    visible_past = _filter_private_events(past_events, request.user)

    # Build attendance lookup for past events (only 'attended' status)
    attended_ids = set()
    if request.user.is_authenticated:
        attended_ids = set(
            EventRegistration.objects.filter(
                event__in=visible_past,
                user=request.user,
                status="attended",
            ).values_list("event_id", flat=True)
        )

    past_events_with_attendance = [
        (event, event.id in attended_ids) for event in visible_past
    ]

    # Build ItemList JSON-LD in Python to avoid template rendering issues
    # (escapejs produces \x27 for apostrophes, which is invalid JSON)
    has_profile = False
    if request.user.is_authenticated:
        has_profile = CrushProfile.objects.filter(user=request.user).exists()

    item_list_elements = []
    for position, event in enumerate(visible_upcoming, start=1):
        if not event.id:
            continue
        event_url = reverse("crush_lu:event_detail", args=[event.id])
        description = event.description or ""
        words = description.split()
        if len(words) > 50:
            description = " ".join(words[:50]) + " \u2026"

        if request.user.is_authenticated and has_profile:
            location_data = {
                "@type": "Place",
                "name": event.location or "",
                "address": _postal_address(event),
            }
        else:
            canton = event.canton or "Luxembourg"
            location_data = {
                "@type": "Place",
                "name": canton,
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": canton,
                    "addressCountry": "LU",
                },
            }

        if event.is_full:
            availability = "https://schema.org/SoldOut"
        elif event.is_registration_open:
            availability = "https://schema.org/InStock"
        else:
            availability = "https://schema.org/OutOfStock"

        # Event image URL (fallback to social preview)
        if event.image:
            image_url = event.image.url
        else:
            image_url = "https://crush.lu/static/crush_lu/crush_social_preview.jpg"

        # Performer list from assigned coaches
        performers = [
            {"@type": "Person", "name": coach.user.first_name}
            for coach in event.coaches.all()
        ]

        # Description fallback for events with empty descriptions
        if not description:
            description = "Dating event in Luxembourg organized by Crush.lu"

        # Map event languages for inLanguage
        lang_map = {"en": "en", "de": "de", "fr": "fr"}
        event_languages = [
            lang_map[lang] for lang in (event.languages or []) if lang in lang_map
        ]

        event_item = {
            "@type": "SocialEvent",
            "name": event.title or "",
            "description": description,
            "startDate": event.date_time.isoformat(),
            "endDate": event.end_time.isoformat(),
            "image": image_url,
            "eventStatus": (
                "https://schema.org/EventCancelled"
                if event.is_cancelled
                else "https://schema.org/EventScheduled"
            ),
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "location": location_data,
            "organizer": {
                "@type": "Organization",
                "name": "Crush.lu",
                "url": "https://crush.lu",
            },
            "offers": {
                "@type": "Offer",
                "url": f"https://crush.lu{event_url}",
                "price": format(event.registration_fee, ".2f"),
                "priceCurrency": "EUR",
                "availability": availability,
                "validFrom": event.created_at.isoformat(),
            },
            "url": f"https://crush.lu{event_url}",
            "audience": {
                "@type": "PeopleAudience",
                "suggestedMinAge": event.min_age,
                "suggestedMaxAge": event.max_age,
            },
        }
        if event_languages:
            event_item["inLanguage"] = (
                event_languages if len(event_languages) > 1 else event_languages[0]
            )

        if performers:
            event_item["performer"] = performers

        item_list_elements.append(
            {
                "@type": "ListItem",
                "position": position,
                "item": event_item,
            }
        )

    event_list_jsonld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": str(_("Upcoming Dating Events in Luxembourg")),
            "description": str(
                _(
                    "Speed dating, social mixers, and singles meetups organized by Crush.lu"
                )
            ),
            "itemListElement": item_list_elements,
        },
        ensure_ascii=False,
    )

    # Active polls for the feedback banner
    active_polls = [
        p for p in EventPoll.objects.filter(is_published=True) if p.is_active
    ]

    context = {
        "upcoming_event_list": visible_upcoming,
        "past_events_with_attendance": past_events_with_attendance,
        "event_list_jsonld": event_list_jsonld,
        "active_polls": active_polls,
    }
    return render(request, "crush_lu/event_list.html", context)


@crush_login_required
def my_events(request):
    """
    Personal calendar of events the current user has registered for.

    Shows upcoming registrations (confirmed / waitlist / pending payment)
    and past attendance with mutual-match counts back into engagement.
    """
    from .models import EventConnection

    now = timezone.now()

    registrations = list(
        EventRegistration.objects.filter(user=request.user)
        .exclude(status="cancelled")
        .select_related("event")
        .order_by("event__date_time")
    )

    upcoming, past = [], []
    for reg in registrations:
        event = reg.event
        if not event or not event.is_published or event.is_cancelled:
            continue
        if event.end_time >= now:
            upcoming.append(reg)
        else:
            past.append(reg)

    past.sort(key=lambda r: r.event.date_time, reverse=True)

    # Count mutual matches per past attended event (single query, annotated).
    # Mutual-derived metrics must not be flow-blind: pre-`shared` crush rows
    # never count as a mutual match — the "1 mutual match" line would reveal
    # a private reciprocal declaration the moment it lands.
    attended_event_ids = [r.event_id for r in past if r.status == "attended"]
    mutual_counts = {}
    if attended_event_ids:
        # The FORWARD row must be filtered too, not just the reverse subquery:
        # `annotate_is_visible_mutual` only screens candidate reverse rows, so
        # the user's own unshared outgoing crush would still be iterated, and a
        # visible legacy reverse from the counterpart would flip
        # `is_mutual_annotated` — reporting a mutual match before the crush is
        # shared. Mirrors the recap-email calculation.
        connections = (
            EventConnection.objects.annotate_is_visible_mutual()
            .excluding_unshared_crushes()
            .filter(
                event_id__in=attended_event_ids,
                requester=request.user,
            )
        )
        for conn in connections:
            if conn.is_mutual_annotated:
                mutual_counts[conn.event_id] = mutual_counts.get(conn.event_id, 0) + 1

    # Event Lobby CTA per card. participant_gate/may_learn cost queries, so
    # only evaluate for attended registrations still in a live/recap phase
    # (at most one or two per user) — everything else renders no lobby CTA.
    from .services.event_lobby import (
        PHASE_CLOSED,
        event_lobby_phase,
        lobby_cta,
    )

    def _card_lobby_cta(reg):
        if reg.status != "attended":
            return None
        if event_lobby_phase(reg.event, now) == PHASE_CLOSED:
            return None
        return lobby_cta(request.user, reg.event, registration=reg, now=now)

    credit_balance_cents = available_credit_cents(request.user)
    upcoming_with_meta = [
        {
            "registration": reg,
            "event": reg.event,
            "is_waitlist": reg.status == "waitlist",
            "is_pending_payment": reg.status == "pending",
            # "applied" included: withdrawing an application is exactly the
            # thing an applicant may still want to do, and event_cancel accepts
            # it. Deliberately NOT SEAT_HOLDING_STATUSES — that set includes
            # "attended", which event_cancel rejects outright.
            "can_cancel": reg.event.date_time > now
            and reg.status in ("applied", "pending", "confirmed", "waitlist"),
            "lobby_cta": _card_lobby_cta(reg),
            "has_sufficient_crush_credit": credit_balance_cents
            >= int(reg.event.registration_fee * 100),
        }
        for reg in upcoming
    ]

    past_with_meta = [
        {
            "registration": reg,
            "event": reg.event,
            "attended": reg.status == "attended",
            "no_show": reg.status == "no_show",
            "mutual_matches": mutual_counts.get(reg.event_id, 0),
            "lobby_cta": _card_lobby_cta(reg),
        }
        for reg in past
    ]

    context = {
        "upcoming_registrations": upcoming_with_meta,
        "past_registrations": past_with_meta,
    }
    return render(request, "crush_lu/my_events.html", context)


def _registration_outlook(event, profile, gender=None):
    """What registration will actually do for this viewer.

    ``gender`` overrides the profile's own, for a *bound* form: the gender a
    member just picked lives in ``form.cleaned_data`` but is only written to the
    profile on the valid branch, under the lock. After a validation error the
    profile is therefore still genderless while the resubmit will pool-check
    exactly this value -- and without it the fallback below asks "is *every*
    pool full?", which answers no and promises a seat to someone whose chosen
    pool is full.

    Returns ``(pools, user_pool, will_waitlist, waitlist_reason)``:

    ``pools``
        Per-gender availability for display, every pool capped by the seats
        this viewer could claim overall -- so a total cap or a reserved-premium
        block that has already spoken for the seats is never re-advertised as
        pool availability. Empty for an event without gender caps.
    ``user_pool``
        The viewer's own row, or ``None`` when their gender does not resolve to
        a pool (anonymous visitors, or a profile with no gender set).
    ``will_waitlist``
        True when ``event_register`` would put them on the waitlist rather than
        give them a seat.
    ``waitlist_reason``
        ``"total"``, ``"pool"``, or ``None`` -- so a surface can say *why*
        rather than fall back on "Event is Full", which is plainly untrue when
        the event has seats left and only this member's pool does not. Mirrors
        the two branches of ``event_register``'s own flash message.

    One definition, because two surfaces consume it -- the event page's CTA and
    the registration page's own warning and submit label -- and #866 was
    precisely the failure of a second surface quietly disagreeing with the
    first. Anything that changes who gets waitlisted must change here too, or
    both pages go back to guessing.
    """
    # A curated event never waitlists: `event_register` skips the capacity test
    # entirely and every sign-up becomes an application. Returning the
    # capacity-based answer here would have both surfaces offering "Join
    # Waitlist" and a full-event warning to someone whose submit actually
    # creates an `applied` row with no queue position — exactly the
    # second-surface disagreement (#866) this helper exists to prevent. Pools
    # are still returned: the gender mix is useful information to an applicant
    # even though it does not gate them.
    if event.uses_curated_registration:
        _, _, pools = event.registration_capacity(
            is_premium=bool(profile and profile.has_active_premium)
        )
        user_gender = gender or getattr(profile, "gender", None)
        user_pool = None
        if pools and user_gender:
            pool_key = event.get_gender_pool(user_gender)
            user_pool = next((p for p in pools if p["key"] == pool_key), None)
        return pools, user_pool, False, None

    is_premium = bool(profile and profile.has_active_premium)
    # Total *and* pools off one read -- see MeetupEvent.registration_capacity().
    # Postgres runs READ COMMITTED, so every statement gets its own snapshot: a
    # registration committing between two queries here would let the chips
    # describe one moment and the CTA another, which is the shape of #866 rather
    # than a fix for it. It also memoises the count on `event`, so the premium
    # reserved-seat banner further down reads this same number for free instead
    # of counting again and disagreeing with the CTA beside it.
    total_full, capacity_remaining, pools = event.registration_capacity(
        is_premium=is_premium
    )

    user_gender = gender or getattr(profile, "gender", None)
    user_pool = None
    if pools and user_gender:
        pool_key = event.get_gender_pool(user_gender)
        user_pool = next((p for p in pools if p["key"] == pool_key), None)

    if not pools:
        pool_blocks = False
    elif user_pool is not None:
        # `pool_full`, not `is_full`: this is the pool's own cap, the exact
        # predicate event_register re-checks under lock. `is_full` also folds in
        # total capacity, which `total_full` already covers.
        pool_blocks = user_pool["pool_full"]
    else:
        # event_register makes a member with no stored gender choose one and
        # persists it *before* the pool check, so "no gender" does not mean "no
        # pool". Which pool they land in is unknowable here -- but when every
        # pool is full, every choice is waitlisted, and the CTA must say so.
        pool_blocks = all(pool["pool_full"] for pool in pools)

    # `total` wins the tie, matching event_register's own message branch: when
    # the whole event is full, that is the plainer thing to tell someone.
    if total_full:
        reason = "total"
    elif pool_blocks:
        reason = "pool"
    else:
        reason = None
    return pools, user_pool, total_full or pool_blocks, reason


def event_detail(request, event_id):
    """Event detail page"""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    # Check if user is registered
    registration = None
    if request.user.is_authenticated:
        registration = (
            EventRegistration.objects.filter(event=event, user=request.user)
            .exclude(status="cancelled")
            .first()
        )

    # For private events, verify access
    if event.is_private_invitation and not registration:
        is_invited = event.invited_users.filter(id=request.user.id).exists()
        has_approved_invitation = EventInvitation.objects.filter(
            event=event, created_user=request.user, approval_status="approved"
        ).exists()

        if not is_invited and not has_approved_invitation:
            messages.error(request, _("This event is by invitation only."))
            return redirect("crush_lu:event_list")

    # Fetch user profile for template display logic
    user_profile = None
    if request.user.is_authenticated:
        user_profile = CrushProfile.objects.filter(user=request.user).first()

    # Language requirement check
    language_requirement_met = True
    if event.languages and request.user.is_authenticated:
        language_requirement_met, _msg = event.user_meets_language_requirement(
            request.user
        )

    event_coaches = event.coaches.filter(is_active=True).select_related("user")

    # Build JSON-LD structured data in Python to guarantee valid JSON
    # (Django's escapejs produces \x27 for apostrophes, which is invalid JSON)
    if request.user.is_authenticated:
        location_data = {
            "@type": "Place",
            "name": event.location,
            "address": _postal_address(event),
        }
    else:
        canton = event.canton or "Luxembourg"
        location_data = {
            "@type": "Place",
            "name": canton,
            "address": {
                "@type": "PostalAddress",
                "addressLocality": canton,
                "addressRegion": canton,
                "addressCountry": "LU",
            },
        }

    # Build performer list from event coaches (first name only for privacy)
    performers = [
        {"@type": "Person", "name": coach.user.first_name}
        for coach in event_coaches
        if coach.user.first_name
    ]

    event_url = reverse("crush_lu:event_detail", args=[event.id])

    # Map event_type to schema.org Event sub-types for richer snippets
    event_type_schema = {
        "speed_dating": "SocialEvent",
        "mixer": "SocialEvent",
        "activity": "SocialEvent",
        "themed": "SocialEvent",
        "quiz_night": "SocialEvent",
    }
    schema_type = event_type_schema.get(event.event_type, "SocialEvent")

    # Map event languages to ISO codes for inLanguage
    lang_map = {"en": "en", "de": "de", "fr": "fr"}
    event_languages = [
        lang_map[lang] for lang in (event.languages or []) if lang in lang_map
    ]

    event_jsonld_data = {
        "@context": "https://schema.org",
        "@type": schema_type,
        "name": event.title,
        "description": event.description,
        "startDate": event.date_time.isoformat(),
        "endDate": event.end_time.isoformat(),
        "eventStatus": (
            "https://schema.org/EventCancelled"
            if event.is_cancelled
            else "https://schema.org/EventScheduled"
        ),
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": location_data,
        "organizer": {
            "@type": "Organization",
            "name": "Crush.lu",
            "url": "https://crush.lu",
        },
        "offers": {
            "@type": "Offer",
            "url": f"https://crush.lu{event_url}",
            "price": format(event.registration_fee, ".2f"),
            "priceCurrency": "EUR",
            "availability": (
                "https://schema.org/SoldOut"
                if event.is_full
                else (
                    "https://schema.org/InStock"
                    if event.is_registration_open
                    else "https://schema.org/OutOfStock"
                )
            ),
            "validFrom": event.created_at.isoformat(),
        },
        "maximumAttendeeCapacity": event.max_participants,
        "remainingAttendeeCapacity": event.spots_remaining,
        "typicalAgeRange": f"{event.min_age}-{event.max_age}",
        "image": (
            event.image.url
            if event.image
            else "https://crush.lu/static/crush_lu/crush_social_preview.jpg"
        ),
        "audience": {
            "@type": "PeopleAudience",
            "suggestedMinAge": event.min_age,
            "suggestedMaxAge": event.max_age,
        },
    }
    if event_languages:
        event_jsonld_data["inLanguage"] = (
            event_languages if len(event_languages) > 1 else event_languages[0]
        )
    if performers:
        event_jsonld_data["performer"] = performers
    event_jsonld = json.dumps(event_jsonld_data, ensure_ascii=False)

    event_list_url = reverse("crush_lu:event_list")
    breadcrumb_jsonld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": str(_("Home")),
                    "item": "https://crush.lu/",
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": str(_("Events")),
                    "item": f"https://crush.lu{event_list_url}",
                },
                {
                    "@type": "ListItem",
                    "position": 3,
                    "name": event.title,
                    "item": f"https://crush.lu{event_url}",
                },
            ],
        },
        ensure_ascii=False,
    )

    now = timezone.now()
    is_past = event.end_time < now
    can_cancel = bool(
        registration
        and registration.status not in ("attended", "cancelled", "no_show")
        and event.date_time > now
    )

    # Premium members can claim reserved seats, so fullness is evaluated against
    # the full capacity for them and public capacity otherwise. The entitlement is
    # an ACTIVE PremiumMembership, NOT `assigned_coach` -- a coach is also granted
    # free on first attendance, which handed the reserved block to every past
    # attendee. See CrushProfile.has_active_premium.
    user_is_premium = bool(user_profile and user_profile.has_active_premium)

    # Per-gender availability plus the one answer both the event page and the
    # registration page need: will registration actually seat this viewer?
    # The reason travels with it: the chips' personal line must name the same
    # cause the registration page names, and it cannot derive that from the pool
    # row alone -- `is_full` there folds a total or reserved-premium block in
    # with the pool's own cap, which read as "your gender group is full" to
    # someone whose gender group was not.
    (
        gender_pool_availability,
        user_gender_pool,
        event_full_for_user,
        registration_waitlist_reason,
    ) = _registration_outlook(event, user_profile)

    # A reserved seat is available to this premium member specifically when the
    # event is publicly full but not yet at total capacity. A full gender pool
    # closes this too: premium buys a seat past `reserved_premium_seats`, not
    # past a pool cap, so event_register waitlists them like everyone else.
    #
    # `is_full_for` here costs no query and cannot disagree with the CTA above:
    # registration_capacity() memoised the count it used, and this reads it.
    premium_reserved_seat_available = (
        user_is_premium
        and event.is_full_for(is_premium=False)
        and not event_full_for_user
    )

    # Event Lobby entry point: one per-user CTA state (or None while the
    # feature is disabled) — see services.event_lobby.lobby_cta for the
    # disclosure rules (§5.3 as amended 2026-07-18).
    from .services.event_lobby import lobby_cta

    event_lobby_cta = lobby_cta(request.user, event, registration=registration)

    context = {
        "event": event,
        "is_past": is_past,
        "can_cancel": can_cancel,
        "user_registration": registration,
        "user_profile": user_profile,
        "user_is_premium": user_is_premium,
        "event_full_for_user": event_full_for_user,
        "gender_pool_availability": gender_pool_availability,
        "user_gender_pool": user_gender_pool,
        "registration_waitlist_reason": registration_waitlist_reason,
        "premium_reserved_seat_available": premium_reserved_seat_available,
        "language_requirement_met": language_requirement_met,
        "event_languages_display": event.get_languages_display,
        "event_coaches": event_coaches,
        "event_jsonld": event_jsonld,
        "breadcrumb_jsonld": breadcrumb_jsonld,
        "event_lobby_cta": event_lobby_cta,
        "has_sufficient_crush_credit": bool(
            request.user.is_authenticated
            and available_credit_cents(request.user)
            >= int(event.registration_fee * 100)
        ),
    }
    return render(request, "crush_lu/event_detail.html", context)


def _ical_escape(text):
    """Escape text per RFC 5545 section 3.3.11."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _ical_fold(line):
    """Fold a content line to max 75 octets per RFC 5545 section 3.1."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts = []
    first = True
    while encoded:
        # First chunk: 75 octets max. Continuations: 74 (leading space = 1 octet)
        limit = 75 if first else 74
        if len(encoded) <= limit:
            parts.append(encoded.decode("utf-8"))
            break
        cut = limit
        # Don't split in the middle of a multi-byte character
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(encoded[:cut].decode("utf-8"))
        encoded = encoded[cut:]
        first = False
    return "\r\n ".join(parts)


def event_calendar_download(request, event_id):
    """Generate .ics calendar file for event (RFC 5545 compliant)."""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    from datetime import timezone as dt_timezone

    end_time = event.date_time + timedelta(minutes=event.duration_minutes)

    start_utc = event.date_time.astimezone(dt_timezone.utc)
    end_utc = end_time.astimezone(dt_timezone.utc)

    dtstart = start_utc.strftime("%Y%m%dT%H%M%SZ")
    dtend = end_utc.strftime("%Y%m%dT%H%M%SZ")
    dtstamp = timezone.now().astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if request.user.is_authenticated and hasattr(request.user, "crushprofile"):
        location = f"{event.location}, {event.full_address}"
    else:
        location = event.canton or "Luxembourg"

    event_url = request.build_absolute_uri(
        reverse("crush_lu:event_detail", kwargs={"event_id": event.id})
    )

    uid = f"event-{event.id}@crush.lu"
    description = _ical_escape(f"{event.description}\n\nRegister: {event_url}")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Crush.lu//Event Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        _ical_fold(f"SUMMARY:{_ical_escape(event.title)}"),
        _ical_fold(f"DESCRIPTION:{description}"),
        _ical_fold(f"LOCATION:{_ical_escape(location)}"),
        _ical_fold(f"URL:{event_url}"),
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        "END:VEVENT",
        "END:VCALENDAR",
    ]

    ics_content = "\r\n".join(lines) + "\r\n"

    response = HttpResponse(ics_content, content_type="text/calendar; charset=utf-8")
    filename = f"crush-event-{event.id}.ics"
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response["Cache-Control"] = "no-cache"

    return response


@crush_login_required
@ratelimit(key="user", rate="5/h", method="POST")
def event_register(request, event_id):
    """Register for an event - bypasses approval for invited guests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # FOR PRIVATE INVITATION EVENTS: Bypass normal profile approval flow
    if event.is_private_invitation:
        is_invited_existing_user = event.invited_users.filter(
            id=request.user.id
        ).exists()

        external_invitation = EventInvitation.objects.filter(
            event=event, created_user=request.user, approval_status="approved"
        ).first()

        if not is_invited_existing_user and not external_invitation:
            messages.error(
                request, _("You do not have an approved invitation for this event.")
            )
            return redirect("crush_lu:event_detail", event_id=event_id)

        if is_invited_existing_user:
            try:
                profile = CrushProfile.objects.get(user=request.user)
            except CrushProfile.DoesNotExist:
                messages.warning(
                    request,
                    _(
                        "Please complete your profile before registering for events. "
                        "This is required for all users, even with invitations."
                    ),
                )
                return redirect("crush_lu:create_profile")
        else:
            try:
                profile = CrushProfile.objects.get(user=request.user)
            except CrushProfile.DoesNotExist:
                logger.error(
                    f"Security issue: External guest {request.user.email} trying to register "
                    f"without profile. Invitation ID: {external_invitation.id if external_invitation else 'None'}"
                )
                messages.error(
                    request,
                    _(
                        "Your profile is missing. Please contact support for assistance."
                    ),
                )
                return redirect("crush_lu:event_detail", event_id=event_id)
    else:
        if event.profile_requirement == "completed":
            # Entry event: open to anyone with a COMPLETED profile (built +
            # phone verified), whether or not they are verified yet. This is
            # where unverified users get verified in person by a coach.
            # Allowlist on purpose — "!= incomplete" would wrongly admit
            # rejected profiles (statuses: incomplete/pending/verified/rejected).
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # Already-verified members always qualify. Unverified users need
                # a completed profile (submitted + phone verified) — they get
                # verified in person at the event.
                profile_ready = profile.verification_status == "verified" or (
                    profile.verification_status == "pending" and profile.phone_verified
                )
                if not profile_ready:
                    messages.warning(
                        request,
                        _(
                            "Please complete your profile before registering. "
                            "You'll get verified in person when you come to the event."
                        ),
                    )
                    return redirect("crush_lu:create_profile")
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        elif event.profile_requirement == "approved":
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # `verification_status`, not the legacy `is_approved`: the model
                # documents the latter as replaced, and `save()` only syncs
                # is_approved -> status, never the reverse.
                if profile.verification_status != "verified":
                    messages.error(
                        request,
                        _(
                            "This event is for verified members only. Get verified at an entry event or with LuxID first."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        elif event.profile_requirement == "coach_assigned":
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # A rejected profile keeps its `assigned_coach` — nothing in the
                # rejection path clears it — so gating on the FK alone let a
                # rejected member register here. Checked first so the rejection
                # is reported as such rather than as "no coach".
                if profile.verification_status == "rejected":
                    messages.error(
                        request,
                        _(
                            "Your profile was not approved, so you cannot register for this event. "
                            "Please contact support."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
                if not profile.assigned_coach_id:
                    messages.error(
                        request,
                        _(
                            "This event is for members with a personal coach. "
                            "Attend an event to get your coach assigned."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        elif event.profile_requirement == "unverified":
            try:
                profile = CrushProfile.objects.get(user=request.user)
                # Allowlist, not `not is_approved`. A rejected profile also has
                # is_approved=False, so the old test admitted it — the same trap
                # the `completed` branch already guards against.
                if profile.verification_status == "verified":
                    messages.error(
                        request,
                        _(
                            "This event is exclusively for members whose profile has not yet been verified by a coach."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
                if profile.verification_status == "rejected":
                    messages.error(
                        request,
                        _(
                            "Your profile was not approved, so you cannot register for this event. "
                            "Please contact support."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        elif event.profile_requirement == "profile_exists":
            # "Profile must exist" now means exactly that. It previously
            # rejected `is_approved` profiles, making it a byte-for-byte
            # duplicate of `unverified` under a label promising the opposite —
            # so an organiser choosing it to mean "anyone with a profile"
            # silently locked out every verified member, and no option in the
            # dropdown expressed "any profile" at all.
            try:
                profile = CrushProfile.objects.get(user=request.user)
                if profile.verification_status == "rejected":
                    messages.error(
                        request,
                        _(
                            "Your profile was not approved, so you cannot register for this event. "
                            "Please contact support."
                        ),
                    )
                    return redirect("crush_lu:event_detail", event_id=event_id)
            except CrushProfile.DoesNotExist:
                messages.error(
                    request,
                    _(
                        "This event requires a Crush profile. Please create one to register."
                    ),
                )
                return redirect("crush_lu:create_profile")
        else:
            try:
                profile = CrushProfile.objects.get(user=request.user)
            except CrushProfile.DoesNotExist:
                profile = None

    # Age verification — enforce event.min_age / event.max_age against the
    # user's date_of_birth on the profile. A checkbox self-attestation is NOT
    # sufficient for age-restricted events: we require a profile with a DOB.
    event_has_age_restriction = event.min_age > 18 or event.max_age < 99
    if event_has_age_restriction:
        if profile is None or profile.age is None:
            messages.error(
                request,
                _(
                    "This event has age restrictions. Please complete your "
                    "profile with your date of birth to verify your age."
                ),
            )
            return redirect("crush_lu:create_profile")
        if not (event.min_age <= profile.age <= event.max_age):
            messages.error(
                request,
                _(
                    "This event is restricted to ages %(min)d–%(max)d. "
                    "Your profile does not meet these age requirements."
                )
                % {"min": event.min_age, "max": event.max_age},
            )
            return redirect("crush_lu:event_detail", event_id=event_id)

    # Language requirement check
    if event.languages:
        meets_req, error_msg = event.user_meets_language_requirement(request.user)
        if not meets_req:
            messages.error(request, error_msg)
            return redirect("crush_lu:event_detail", event_id=event_id)

    if (
        EventRegistration.objects.filter(event=event, user=request.user)
        .exclude(status="cancelled")
        .exists()
    ):
        messages.warning(request, _("You are already registered for this event."))
        return redirect("crush_lu:event_detail", event_id=event_id)

    if not event.is_registration_accepting:
        # The event detail page already shows a "Registration is closed" banner,
        # so skip the redundant (and alarming, red) flash on top of it.
        return redirect("crush_lu:event_detail", event_id=event_id)

    # Self-attestation checkbox only appears for events with NO age restriction
    # AND no profile on file. Age-restricted events have already forced profile
    # creation above (see event_has_age_restriction), so the checkbox is never
    # the primary age signal for those. It remains as a legal safeguard for
    # open events where a profile isn't required.
    requires_age_confirmation = profile is None and not event_has_age_restriction

    # Gender selection needed when event uses per-gender caps and user has no gender
    requires_gender_selection = event.gender_limits_active and (
        profile is None or not profile.gender
    )

    # Which template the single render at the bottom uses. An invalid HTMX
    # submit swaps only #registration-form-container, so it gets the partial
    # instead of the whole page -- but it is chosen here rather than returned
    # early so that both go out through *one* render with *one* context.
    #
    # That is the actual fix, not just a tidier shape: the partial used to be
    # rendered from its own hand-built context, and a context the full page grew
    # and the partial did not is exactly how it ended up branching on
    # `event.is_full` long after every other surface had stopped -- flipping a
    # corrected "Join Waitlist" back to "Confirm Registration" the moment a
    # pool-full member tripped form validation. Sharing the context means a
    # future key cannot reach one and miss the other.
    template = "crush_lu/event_register.html"

    # Speed-dating registrations also collect per-application dating
    # preferences (age range / languages / gender preference). Scoped by event
    # type so every other event's registration form stays byte-identical.
    collect_preferences = event.event_type == "speed_dating"

    if request.method == "POST":
        form = EventRegistrationForm(
            request.POST,
            event=event,
            requires_age_confirmation=requires_age_confirmation,
            requires_gender_selection=requires_gender_selection,
        )
        pref_form = (
            EventPreferenceForm(request.POST, event=event)
            if collect_preferences
            else None
        )
        # Evaluate both unconditionally so an invalid re-render carries the
        # error messages of each form, not just the first.
        form_valid = form.is_valid()
        pref_form_valid = pref_form is None or pref_form.is_valid()
        if form_valid and pref_form_valid:
            # Use select_for_update + atomic to prevent race condition where
            # concurrent registrations could exceed max_participants
            with transaction.atomic():
                # Lock the event row to get accurate capacity count
                locked_event = MeetupEvent.objects.select_for_update().get(id=event_id)

                # Re-check registration deadline under lock to prevent race condition
                if not locked_event.is_registration_accepting:
                    # Detail page shows the "closed" banner; skip the redundant flash.
                    return redirect("crush_lu:event_detail", event_id=event_id)

                # Defense-in-depth: re-verify age under lock against the freshly
                # locked event, in case event.min_age / max_age or the user's
                # DOB changed concurrently. Derive the restriction flag from
                # the *locked* event — the pre-lock flag may be stale if an
                # admin tightened the age bounds after the initial read.
                locked_has_age_restriction = (
                    locked_event.min_age > 18 or locked_event.max_age < 99
                )
                if locked_has_age_restriction:
                    locked_profile = CrushProfile.objects.filter(
                        user=request.user
                    ).first()
                    if (
                        locked_profile is None
                        or locked_profile.age is None
                        or not (
                            locked_event.min_age
                            <= locked_profile.age
                            <= locked_event.max_age
                        )
                    ):
                        messages.error(
                            request,
                            _("This event is restricted to ages " "%(min)d–%(max)d.")
                            % {
                                "min": locked_event.min_age,
                                "max": locked_event.max_age,
                            },
                        )
                        return redirect("crush_lu:event_detail", event_id=event_id)

                # If the user submitted a gender, persist it to their profile
                submitted_gender = form.cleaned_data.get("gender")
                if requires_gender_selection and submitted_gender:
                    if profile is None:
                        profile = CrushProfile.objects.create(
                            user=request.user, gender=submitted_gender
                        )
                    else:
                        profile.gender = submitted_gender
                        profile.save(update_fields=["gender"])

                cancelled_registration = EventRegistration.objects.filter(
                    event=locked_event, user=request.user, status="cancelled"
                ).first()

                if cancelled_registration:
                    registration = cancelled_registration
                    # A re-registration is treated as brand new (its
                    # registered_at is reset below), so drop any stale hunt-team
                    # membership tied to this row. Otherwise reconfirming it
                    # would silently reactivate the old CacheTeamMember: the
                    # active-only member_count() freed the slot on cancellation,
                    # a replacement may have taken it, and the team would then
                    # exceed team_size_max. The user re-joins a team afresh.
                    registration.cache_memberships.all().delete()
                    registration.dietary_restrictions = form.cleaned_data.get(
                        "dietary_restrictions", ""
                    )
                    registration.bringing_guest = form.cleaned_data.get(
                        "bringing_guest", False
                    )
                    registration.guest_name = form.cleaned_data.get("guest_name", "")
                    # Policy: a user who cancelled and re-registers is treated
                    # like a new registration — their original `registered_at`
                    # is discarded and they go to the back of the waitlist (if
                    # the event is full). This prevents queue-jumping via
                    # cancel-then-re-register while the event is at capacity.
                    registration.registered_at = timezone.now()
                    registration.resale_source_registration = None
                    registration.resale_source_payment = None
                    registration.resale_beneficiary = None
                else:
                    registration = form.save(commit=False)
                    registration.event = locked_event
                    registration.user = request.user

                # A curated event admits nobody at signup: the sign-up is an
                # application and the organiser composes the group afterwards.
                # No capacity test runs, because applications are *meant* to
                # outnumber the places — that is the point of curating — and
                # "applied" holds no seat (see SEAT_HOLDING_STATUSES), so an
                # over-subscribed pool cannot overfill the event. No waitlist
                # either: there is no queue to be behind while nobody has been
                # admitted. Falling through to the shared tail below is
                # deliberate — it writes the preference row, skips the resale
                # claim and the confirmation email (both keyed off
                # SEAT_HOLDING_STATUSES and the status name), and returns the
                # same success response as every other path.
                if locked_event.uses_curated_registration:
                    registration.status = "applied"
                    messages.success(
                        request,
                        _(
                            "Your application has been received. The organiser "
                            "team composes the group before the event and will "
                            "let you know whether you have a place."
                        ),
                    )
                else:
                    # Determine confirmed vs waitlist using both total and gender caps.
                    # Premium members -- an ACTIVE PremiumMembership, not merely an
                    # `assigned_coach` -- can claim reserved seats, so their fullness
                    # is measured against the full capacity.
                    user_gender = getattr(profile, "gender", None)
                    is_premium = bool(profile and profile.has_active_premium)
                    total_full = locked_event.is_full_for(is_premium=is_premium)
                    gender_pool_full = (
                        locked_event.gender_limits_active
                        and user_gender
                        and locked_event.is_gender_pool_full(user_gender)
                    )

                    if total_full or gender_pool_full:
                        registration.status = "waitlist"
                        if gender_pool_full and not total_full:
                            messages.info(
                                request,
                                _(
                                    "All spots for your gender group are taken. "
                                    "You have been added to the waitlist."
                                ),
                            )
                        else:
                            messages.info(
                                request,
                                _("Event is full. You have been added to the waitlist."),
                            )
                    else:
                        # A paid event's seat is held, not confirmed, until the money
                        # arrives -- the SumUp return handler flips it to "confirmed".
                        # "pending" still counts toward capacity and still yields a
                        # door ticket (see SEAT_HOLDING_STATUSES); it only changes
                        # what the status *claims*. Free events are unaffected.
                        registration.status = _admitted_status(locked_event, registration)
                        if registration.status == "pending":
                            messages.success(
                                request,
                                _(
                                    "Your spot is reserved! Please complete payment "
                                    "to confirm your registration."
                                ),
                            )
                        else:
                            messages.success(
                                request, _("Successfully registered for the event!")
                            )

                registration.save()
                if pref_form is not None:
                    # update_or_create, not save(): the registration row is
                    # reused on re-registration, and a stale preference row
                    # from a cancelled application must be overwritten with
                    # this application's answers.
                    EventRegistrationPreference.objects.update_or_create(
                        registration=registration,
                        defaults=pref_form.preference_defaults(),
                    )
                if registration.status in SEAT_HOLDING_STATUSES:
                    _attach_unclaimed_resale_claim(registration, locked_event)

            try:
                if registration.status == "confirmed":
                    send_event_registration_confirmation(registration, request)
                elif registration.status == "waitlist":
                    send_event_waitlist_notification(registration, request)
                elif registration.status == "pending":
                    # Paid event: the seat is held but not yet confirmed. Without
                    # this branch a paying registrant would get no email at all.
                    send_event_payment_pending_notification(registration, request)
            except Exception as e:
                logger.error(f"Failed to send event registration email: {e}")

            if request.headers.get("HX-Request"):
                waitlist_position = None
                if registration.status == "waitlist":
                    waitlist_position = (
                        EventRegistration.objects.filter(
                            event=event,
                            status="waitlist",
                            registered_at__lt=registration.registered_at,
                        ).count()
                        + 1
                    )
                return render(
                    request,
                    "crush_lu/_event_registration_success.html",
                    {
                        "event": event,
                        "registration": registration,
                        "waitlist_position": waitlist_position,
                        "has_sufficient_crush_credit": (
                            available_credit_cents(request.user)
                            >= int(event.registration_fee * 100)
                        ),
                    },
                )
            return redirect("crush_lu:dashboard")
        elif request.headers.get("HX-Request"):
            template = "crush_lu/_event_registration_form.html"
    else:
        form = EventRegistrationForm(
            event=event,
            requires_age_confirmation=requires_age_confirmation,
            requires_gender_selection=requires_gender_selection,
        )
        pref_form = (
            EventPreferenceForm(
                event=event,
                initial=EventPreferenceForm.initial_for(request.user, profile, event),
            )
            if collect_preferences
            else None
        )

    # Same answer the event page's CTA used to get here, so the button that said
    # "Join Waitlist" does not land on a page headed "Confirm Registration"
    # (#866). `event.is_full` alone missed both a full gender pool and a
    # reserved-premium block.
    #
    # Below the branch, so the successful POST -- which returned above and needs
    # none of this -- does not pay for capacity counts it will not read.
    #
    # A bound form carries the gender the member just chose even though nothing
    # persisted it: that write lives on the valid branch, under the lock (see
    # `submitted_gender` there). Reading the profile alone would leave them
    # genderless here, fall back to "is every pool full?", and answer no --
    # promising a seat to someone whose chosen pool is full, which the corrected
    # resubmit then waitlists. #866, one unrelated field error away.
    submitted_gender = ""
    if form.is_bound:
        submitted_gender = (getattr(form, "cleaned_data", None) or {}).get("gender")

    _pools, _user_pool, registration_will_waitlist, waitlist_reason = (
        _registration_outlook(event, profile, gender=submitted_gender)
    )

    context = {
        "event": event,
        "form": form,
        "pref_form": pref_form,
        "requires_age_confirmation": requires_age_confirmation,
        "requires_gender_selection": requires_gender_selection,
        "registration_will_waitlist": registration_will_waitlist,
        # Both templates read this: the warning banner is a shared component
        # rendered inside the swapped container, so the partial needs the reason
        # as much as the full page does.
        "registration_waitlist_reason": waitlist_reason,
    }
    return render(request, template, context)


@crush_login_required
def event_cancel(request, event_id):
    """Cancel event registration"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    registration = get_object_or_404(EventRegistration, event=event, user=request.user)

    if request.method == "POST":
        promoted = None
        awaiting_resale = False

        with transaction.atomic():
            locked_event = MeetupEvent.objects.select_for_update().get(id=event_id)
            lock_ids = [registration.pk] + list(
                EventRegistration.objects.filter(event=locked_event, status="waitlist")
                .exclude(pk=registration.pk)
                .values_list("pk", flat=True)
            )
            locked_registrations = {
                row.pk: row
                for row in EventRegistration.objects.select_for_update()
                .filter(pk__in=lock_ids)
                .order_by("pk")
            }
            registration = locked_registrations[registration.pk]
            if registration.status in ("cancelled", "no_show"):
                messages.info(request, _("Your registration was already cancelled."))
                return redirect("crush_lu:dashboard")

            # Crush.lu has cancelled this event, so there is nothing for the
            # member to cancel and a great deal for them to lose by trying.
            #
            # The organiser-cancellation remedy is a PREMIUM credit plus cash
            # on request; the member-cancellation remedy is face value at best
            # and nothing at all inside 48h. Letting this path run turns the
            # first into the second. The window is real: the admin action
            # commits `is_cancelled` and then does the echo.lu withdrawal and
            # the Apple/Google wallet refreshes — network fan-out, seconds of
            # it — before the credit sweep reaches this member, and the sweep
            # skips rows that have gone `cancelled` in the meantime. A member
            # reading the cancellation email and clicking "cancel my place"
            # lands exactly there.
            if locked_event.is_cancelled:
                messages.info(
                    request,
                    _(
                        "This event has been cancelled — you don't need to do "
                        "anything. Your Crush Credit is on its way, and you can "
                        "reply to the cancellation email if you would rather "
                        "have your money back."
                    ),
                )
                return redirect("crush_lu:event_detail", event_id=event_id)

            now = timezone.now()
            if registration.status == "attended" or locked_event.end_time <= now:
                messages.error(
                    request,
                    _(
                        "This event has already taken place. If something is wrong, "
                        "contact your coach."
                    ),
                )
                return redirect("crush_lu:event_detail", event_id=event_id)
            if locked_event.date_time <= now:
                messages.error(
                    request,
                    _(
                        "This event has already started. If you can't make it, "
                        "contact your coach."
                    ),
                )
                return redirect("crush_lu:event_detail", event_id=event_id)

            registration.status = "cancelled"
            # This view promotes explicitly below, inside the same locked
            # transaction, and sends the confirmation email itself. Tell
            # `signals.promote_waitlist_on_cancellation` to stand down, or the
            # freed seat is handed out twice — once here and once on commit.
            registration._waitlist_promotion_handled = True
            registration.save()

            messages.success(request, _("Your registration has been cancelled."))

            # A paid cancellation used to notify nobody and give back nothing:
            # this view gates on already-cancelled, already-attended and
            # already-started, and never looked at `payment_confirmed` at all.
            # Under the credit policy the money now has somewhere to go on its
            # own. Inside the lock, on the row we just cancelled — the credit
            # and the `payment_confirmed` it releases have to land together
            # (see issue_credit's Trap 1 note).
            credits = issue_cancellation_credits(
                registration, moment=registration.cancelled_at
            )
            _paid_cents, captured_payment = paid_amount_cents(registration)
            awaiting_resale = bool(
                not credits
                and registration.payment_confirmed
                and captured_payment is not None
            )

            # Gender-aware waitlist promotion (DB only, inside transaction).
            # `accepts_waitlist_promotion` also covers is_cancelled, which this
            # branch previously did not: a member cancelling their place at a
            # cancelled event used to hand the seat to someone on the waitlist
            # and email them a confirmation for it.
            if locked_event.accepts_waitlist_promotion:
                promoted = _promote_from_waitlist(
                    locked_event,
                    request.user,
                    resale_source_registration=registration,
                )

            if credits:
                messages.success(
                    request,
                    _(
                        "We've added €%(amount).2f in Crush Credit to your "
                        "account — it's ready to use on any Crush.lu event."
                    )
                    % {"amount": sum(credit.amount_cents for credit in credits) / 100},
                )

        # Send emails OUTSIDE the transaction so they are only dispatched
        # after a successful commit and don't hold the DB lock during SMTP I/O.
        try:
            send_event_cancellation_confirmation(
                request.user,
                event,
                request,
                credits,
                awaiting_resale=awaiting_resale,
            )
        except Exception as e:
            logger.error(f"Failed to send event cancellation email: {e}")

        if promoted:
            try:
                # _promote_from_waitlist admits at _admitted_status(), so on a
                # paid event the promoted seat is "pending" and must ask for
                # payment rather than claim to be confirmed.
                if promoted.status == "pending":
                    send_event_payment_pending_notification(promoted, request)
                else:
                    send_event_registration_confirmation(promoted, request)
            except Exception as e:
                logger.error(f"Failed to send waitlist promotion email: {e}")

        return redirect("crush_lu:dashboard")

    context = {
        "event": event,
        "registration": registration,
    }
    return render(request, "crush_lu/event_cancel.html", context)


@crush_login_required
def event_feedback(request, event_id):
    """Capture a single feedback response from an attendee.

    Open only to users whose registration is in 'attended' status, and only
    after the event has ended. Idempotent: a returning user lands on a
    "thanks" view instead of being able to submit twice.
    """
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)
    now = timezone.now()

    if event.end_time > now:
        messages.info(request, _("Feedback opens once the event has ended."))
        return redirect("crush_lu:event_detail", event_id=event.id)

    registration = (
        EventRegistration.objects.filter(event=event, user=request.user)
        .exclude(status="cancelled")
        .first()
    )
    if not registration or registration.status != "attended":
        messages.error(
            request,
            _("Only attendees can leave feedback for this event."),
        )
        return redirect("crush_lu:event_detail", event_id=event.id)

    existing = EventFeedback.objects.filter(event=event, user=request.user).first()
    if existing and request.method != "POST":
        return render(
            request,
            "crush_lu/event_feedback.html",
            {"event": event, "submitted": True, "feedback": existing, "form": None},
        )

    if request.method == "POST":
        form = EventFeedbackForm(request.POST, instance=existing)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.event = event
            feedback.user = request.user
            feedback.save()
            messages.success(request, _("Thanks for the feedback!"))
            return redirect("crush_lu:event_feedback", event_id=event.id)
    else:
        form = EventFeedbackForm()

    return render(
        request,
        "crush_lu/event_feedback.html",
        {"event": event, "submitted": False, "feedback": None, "form": form},
    )
