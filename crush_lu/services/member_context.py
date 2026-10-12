"""Current-member reads shared only within one dashboard/template request."""

from datetime import timedelta

from django.db.models import (
    Count,
    DateTimeField,
    DurationField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    Value,
)
from django.db.models.functions import Cast
from django.utils import timezone
from django.utils.functional import cached_property

from crush_lu.models import (
    CrushProfile,
    EventConnection,
    EventRegistration,
    MeetupEvent,
    ProfileSubmission,
)
from crush_lu.models.events import SEAT_HOLDING_STATUSES

_REQUEST_DATA = "_crush_member_context_data"


class MemberContextData:
    def __init__(self, user):
        self.user = user
        self.user_id = user.pk

    @cached_property
    def profile(self):
        try:
            return CrushProfile.objects.select_related("assigned_coach__user").get(
                user=self.user
            )
        except CrushProfile.DoesNotExist:
            return None

    @cached_property
    def submission(self):
        if self.profile is None:
            return None
        return ProfileSubmission.latest_for_profile(
            self.profile, select_related=("coach__user",)
        )

    @cached_property
    def hidden_counterpart_ids(self):
        # Resolve the existing policy helpers lazily, including their failures:
        # a failed lookup is not cached and the navbar retains its safe fallback.
        from .blocking import blocked_user_ids
        from .event_lobby import hidden_encounter_user_ids

        return frozenset(
            blocked_user_ids(self.user) | hidden_encounter_user_ids(self.user)
        )

    @cached_property
    def connection_count(self):
        hidden_ids = self.hidden_counterpart_ids
        return (
            EventConnection.objects.active_for_user(self.user)
            .excluding_unshared_crushes()
            .exclude(Q(requester_id__in=hidden_ids) | Q(recipient_id__in=hidden_ids))
            .count()
        )

    @cached_property
    def event_now(self):
        return timezone.now()

    def _registrations_with_windows(self):
        # SQLite rejects PositiveIntegerField * DurationField. Cast the scalar
        # explicitly, then type both the duration and datetime expressions;
        # PostgreSQL compiles this as integer * interval and timestamp + interval.
        duration = ExpressionWrapper(
            Cast(F("event__duration_minutes"), IntegerField())
            * Value(timedelta(minutes=1)),
            output_field=DurationField(),
        )
        connection_window = ExpressionWrapper(
            Cast(F("event__connection_window_hours"), IntegerField())
            * Value(timedelta(hours=1)),
            output_field=DurationField(),
        )
        from .event_lobby import RECAP_WINDOW_HOURS

        return EventRegistration.objects.filter(
            user=self.user, event__is_published=True, event__is_cancelled=False
        ).alias(
            _event_end=ExpressionWrapper(
                F("event__date_time") + duration, output_field=DateTimeField()
            ),
            _connection_deadline=ExpressionWrapper(
                F("_event_end") + connection_window, output_field=DateTimeField()
            ),
            _recap_deadline=ExpressionWrapper(
                F("_event_end") + Value(timedelta(hours=RECAP_WINDOW_HOURS)),
                output_field=DateTimeField(),
            ),
            _quiz_deadline=ExpressionWrapper(
                F("_event_end") + Value(timedelta(days=2)),
                output_field=DateTimeField(),
            ),
        )

    def _upcoming_filter(self):
        return Q(
            status__in=(*SEAT_HOLDING_STATUSES, "waitlist", "applied"),
            event__date_time__gte=MeetupEvent.live_lookback_cutoff(self.event_now),
            _event_end__gte=self.event_now,
        )

    def _dashboard_registration_queryset(self):
        # The 48-hour lobby/quiz window and configurable My Crush window are
        # independent. A fixed recent-date cutoff would hide valid long windows.
        attended_actions = Q(status="attended") & (
            Q(_recap_deadline__gt=self.event_now)
            | Q(event__event_type="quiz_night", _quiz_deadline__gte=self.event_now)
            | Q(_connection_deadline__gte=self.event_now)
        )
        return (
            self._registrations_with_windows()
            .filter(self._upcoming_filter() | attended_actions)
            .select_related("event")
            .order_by("event__date_time")
        )

    @cached_property
    def dashboard_registrations(self):
        return list(self._dashboard_registration_queryset())

    @cached_property
    def upcoming_registrations(self):
        registrations = self.__dict__.get("dashboard_registrations")
        if registrations is not None:
            # The dashboard already fetched these same current candidates.
            return [
                registration
                for registration in registrations
                if registration.status
                in (*SEAT_HOLDING_STATUSES, "waitlist", "applied")
                and registration.event.end_time >= self.event_now
            ]
        return list(self._upcoming_registration_queryset())

    def _upcoming_registration_queryset(self):
        return (
            self._registrations_with_windows()
            .filter(self._upcoming_filter())
            .select_related("event")
            .order_by("event__date_time")
        )

    @cached_property
    def navigation_registrations(self):
        # Never store a truncated list in upcoming_registrations: a later
        # dashboard evaluation still needs every current seat and its count.
        upcoming = self.__dict__.get("upcoming_registrations")
        if upcoming is not None:
            return upcoming[:5]
        if "dashboard_registrations" in self.__dict__:
            return self.upcoming_registrations[:5]
        # End/visibility filtering is already in SQL, so the navbar can fetch
        # its five rows directly without hydrating the remaining future seats.
        return list(self._upcoming_registration_queryset()[:5])

    @cached_property
    def registration_counts(self):
        return EventRegistration.objects.filter(user=self.user).aggregate(
            attended=Count(
                "pk",
                filter=Q(
                    status="attended",
                    event__is_published=True,
                    event__is_cancelled=False,
                ),
            ),
            booked=Count("pk", filter=Q(status__in=("confirmed", "attended"))),
        )

    @cached_property
    def has_booked(self):
        counts = self.__dict__.get("registration_counts")
        if counts is not None:
            return counts["booked"] > 0
        # Keep the lightweight existence check on non-dashboard pages, which
        # do not need the historical attendance aggregate.
        return EventRegistration.objects.filter(
            user=self.user, status__in=("confirmed", "attended")
        ).exists()


def member_context_data(request):
    """Bind lazy reads to this request's current account, never a global cache."""
    user = request.user
    data = getattr(request, _REQUEST_DATA, None)
    if data is None or data.user_id != user.pk:
        data = MemberContextData(user)
        setattr(request, _REQUEST_DATA, data)
    return data


def invalidate_member_context_data(request):
    """Discard a snapshot after a write, including a partially failed transition."""
    if hasattr(request, _REQUEST_DATA):
        delattr(request, _REQUEST_DATA)
