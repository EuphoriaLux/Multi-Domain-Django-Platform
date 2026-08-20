"""Read-only event-cancellation reporting endpoints for hub.crush.lu.

There is no ``EventCancellation`` model. Cancellation state is reconstructed
live, at read time, straight off three crush_lu sources — the same live-read
pattern ``hub/views_social.py`` uses over ``crush_lu.models``, not the
standalone-ledger pattern in ``hub/models.py`` (``PaymentIn``/``Refund``):

1. ``MeetupEvent.is_cancelled`` / ``organiser_cancellation_started_at`` — the
   event-level flag and the start of its current cancellation cycle, set by
   the admin's ``cancel_events`` action (``crush_lu/admin/events.py``).
2. ``EventRegistration.status == "cancelled"`` / ``cancelled_at`` — a
   member's own seat cancellation.
3. ``CrushCredit`` rows with ``reason=CrushCredit.Reason.EVENT_CANCELLED``,
   linked back to a registration via ``source_registration`` (or, once that
   registration row has been deleted, via ``source_payment.event`` — the
   payment is ``PROTECT``ed and always carries the immutable event
   attribution ``PaymentTransaction.event``, unlike the ``SET_NULL``
   ``source_registration``).

⚠️ **Source #2 is *not* "every seat an organiser cancellation affected".**
``cancel_events`` uses ``queryset.update(is_cancelled=True)`` and never
touches individual ``EventRegistration.status`` — a seat stays
``confirmed``/``pending``/``waitlist``/``attended`` unless its own occupant
separately cancelled it. So the detail endpoint below surfaces *self*-
cancelled seats, which is exactly where all three sources can intersect: a
member who cancels inside the 48h late-cancellation window keeps
``payment_confirmed`` (no credit yet — only the resale share might follow),
so if the organiser then cancels the whole event,
``credit_paid_registrations_for_cancelled_event`` still finds and credits
that ``payment_confirmed=True`` row (see
``crush_lu/services/credits.py::credit_paid_registrations_for_cancelled_event``)
even though its ``status`` has stayed ``"cancelled"`` throughout.

Both endpoints are necessarily live-read/poll: ``.update()`` fires no
signals, so nothing here can be event-driven. Restoring then re-cancelling an
event resets ``organiser_cancellation_started_at``, and
``EventRegistration.save()`` clears ``cancelled_at`` the moment a row leaves
``"cancelled"`` — so every credit aggregate here, list totals and the detail
endpoint's per-registration credit alike, only ever reflects the event's
CURRENT cancellation cycle: credit issued under an earlier cycle, before a
restore, is scoped out everywhere, consistently (see ``_event_cancelled_credits``
below).

Read-only: there is no cancel-event write endpoint here.
"""

from __future__ import annotations

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from crush_lu.admin.credits import CashRefundQueueFilter
from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.models.credits import CrushCredit

from .serializers import (
    EventCancellationRegistrationSerializer,
    EventCancellationSummarySerializer,
)

# The one predicate for "this member may still ask for cash instead of
# credit" — see ``CashRefundQueueFilter._open``'s own docstring. Called
# unbound (it never reads ``self``) so both endpoints below stay provably the
# same query the staff cash-refund queue runs, rather than a hand-copied
# filter that can silently drift from it. A test asserts this identity by
# recomputing expectations from this same call, not from hardcoded booleans.
_open_cash_refund_credits = CashRefundQueueFilter._open


def _event_cancelled_credits(event_ids):
    """EVENT_CANCELLED credit rows attributable to ``event_ids``.

    Annotated with the owning event id (``_event_id``, via the
    ``source_registration``-then-``source_payment`` fallback described in the
    module docstring) and scoped to each event's CURRENT cancellation cycle:
    a credit issued before the most recent ``organiser_cancellation_started_at``
    is excluded, mirroring the cycle scoping ``cancel_events`` applies to its
    own email cursor (``crush_lu/admin/events.py``, ~line 1307).
    """
    return (
        CrushCredit.objects.filter(reason=CrushCredit.Reason.EVENT_CANCELLED)
        .filter(
            Q(source_registration__event_id__in=event_ids)
            | Q(
                source_registration__isnull=True,
                source_payment__event_id__in=event_ids,
            )
        )
        .annotate(
            _event_id=Coalesce(
                "source_registration__event_id", "source_payment__event_id"
            ),
            _cycle_started_at=Coalesce(
                "source_registration__event__organiser_cancellation_started_at",
                "source_payment__event__organiser_cancellation_started_at",
            ),
        )
        .filter(
            Q(_cycle_started_at__isnull=True) | Q(issued_at__gte=F("_cycle_started_at"))
        )
    )


class EventCancellationsView(APIView):
    """Every event currently flagged cancelled, most recent cycle first."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        events = list(
            MeetupEvent.objects.filter(is_cancelled=True).order_by(
                F("organiser_cancellation_started_at").desc(nulls_last=True), "-pk"
            )
        )
        event_ids = [event.pk for event in events]

        # Three bulk aggregate queries total, however many events are
        # cancelled — never one query per event.
        affected_counts = dict(
            EventRegistration.objects.filter(event_id__in=event_ids, status="cancelled")
            .values("event_id")
            .annotate(n=Count("id"))
            .values_list("event_id", "n")
        )

        base_credits = _event_cancelled_credits(event_ids)
        issued_totals = {
            row["_event_id"]: row
            for row in base_credits.values("_event_id").annotate(
                issued_count=Count("id"),
                issued_total_cents=Coalesce(Sum("amount_cents"), 0),
            )
        }

        # ``base_credits`` is already cycle-scoped (see
        # ``_event_cancelled_credits``), so this total is too.
        open_totals = {
            row["_event_id"]: row["open_total_cents"]
            for row in _open_cash_refund_credits(None, base_credits)
            .values("_event_id")
            .annotate(open_total_cents=Coalesce(Sum("amount_cents"), 0))
        }

        for event in events:
            issued = issued_totals.get(event.pk, {})
            event.affected_registrations = affected_counts.get(event.pk, 0)
            event.issued_credits_count = issued.get("issued_count", 0)
            event.issued_credits_total_cents = issued.get("issued_total_cents", 0)
            event.open_cash_refund_total_cents = open_totals.get(event.pk, 0)

        return Response(
            {"items": EventCancellationSummarySerializer(events, many=True).data}
        )


class EventCancellationDetailView(APIView):
    """Self-cancelled seats for one cancelled event, each with its credit."""

    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        try:
            event = MeetupEvent.objects.get(pk=pk, is_cancelled=True)
        except MeetupEvent.DoesNotExist:
            return Response({"error": "Event not found or not cancelled"}, status=404)

        registrations = list(
            EventRegistration.objects.filter(event=event, status="cancelled")
            .select_related("user")
            .order_by("-cancelled_at", "-pk")
        )
        registration_ids = [registration.pk for registration in registrations]

        # Current-cycle credit for exactly these registrations. Direct
        # ``source_registration_id__in`` rather than ``_event_cancelled_credits``:
        # every row here was reached FROM a live registration, so the
        # deleted-registration/``source_payment`` fallback that helper exists
        # for cannot apply.
        credits_qs = CrushCredit.objects.filter(
            reason=CrushCredit.Reason.EVENT_CANCELLED,
            source_registration_id__in=registration_ids,
        )
        if event.organiser_cancellation_started_at:
            credits_qs = credits_qs.filter(
                issued_at__gte=event.organiser_cancellation_started_at
            )
        credits = list(
            credits_qs.order_by("source_registration_id", "-issued_at", "-pk")
        )
        open_ids = set(
            _open_cash_refund_credits(None, credits_qs).values_list("id", flat=True)
        )

        # One representative credit per registration: the open one if there
        # is one (at most one in practice — a bonus/tranche-restore credit is
        # never itself cash-refund-eligible, see
        # crush_lu/services/credits.py::_issue_cancelled_event_remedy), else
        # the most recently issued (rows already arrive newest-first per
        # registration from the ``-issued_at`` ordering above).
        credit_by_registration = {}
        for credit in credits:
            reg_id = credit.source_registration_id
            current = credit_by_registration.get(reg_id)
            if current is None or (
                current.pk not in open_ids and credit.pk in open_ids
            ):
                credit_by_registration[reg_id] = credit

        issued_total_cents = sum(credit.amount_cents for credit in credits)
        open_total_cents = sum(
            credit.amount_cents for credit in credits if credit.pk in open_ids
        )

        for registration in registrations:
            credit = credit_by_registration.get(registration.pk)
            registration.linked_credit = credit
            registration.open_cash_refund = bool(credit and credit.pk in open_ids)

        event.affected_registrations = len(registrations)
        event.issued_credits_count = len(credits)
        event.issued_credits_total_cents = issued_total_cents
        event.open_cash_refund_total_cents = open_total_cents

        return Response(
            {
                "event": EventCancellationSummarySerializer(event).data,
                "items": EventCancellationRegistrationSerializer(
                    registrations, many=True
                ).data,
            }
        )
