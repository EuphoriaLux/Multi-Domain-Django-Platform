"""
Event-related admin classes for Crush.lu Coach Panel.

Includes:
- MeetupEventAdmin
- EventRegistrationAdmin
- EventInvitationAdmin
- Event inlines (registrations, invitations, voting, presentations, speed dating)
"""

import logging
import uuid
from functools import partial

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib import messages as django_messages
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin

from azureproject.admin_translation_mixin import AutoTranslateMixin

from crush_lu.models import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    EventRegistration,
    EventInvitation,
    EventVotingSession,
    MeetupEvent,
    PresentationQueue,
)
from crush_lu.models.events import SEAT_HOLDING_STATUSES, normalize_lu_postcode
from crush_lu.models.payments import PaymentTransaction

from .filters import EventCapacityFilter
from .quiz import QuizEventInline

logger = logging.getLogger(__name__)

# Size the address boxes to the data, so the shape of a Luxembourg address is
# legible at a glance. `address_postcode` is absent on purpose — its widget
# lives on the declared form field, which formfield_for_dbfield cannot reach.
ADDRESS_WIDGET_ATTRS = {
    "address_street": {"size": 50, "placeholder": "rue du Nord"},
    "address_number": {"size": 8, "placeholder": "7"},
    "address_town": {"size": 30, "placeholder": "Luxembourg"},
}


def _notify_google_indexing(event_ids, action=None):
    """Ping Google Search Indexing for events changed by a bulk action.

    Same reason as ``_enqueue_echo_sync``: ``queryset.update()`` fires no
    signals, so the indexing receivers never see these changes. Runs inside one
    wall-clock budget and never raises — a slow Google must not break an admin
    action whose database work already committed.
    """
    ids = list(dict.fromkeys(event_ids))
    if not ids:
        return

    try:
        from crush_lu.services.google_indexing import (
            INDEXABLE_EVENT_FILTERS,
            notify_events_indexing,
        )

        notify_events_indexing(
            MeetupEvent.objects.filter(pk__in=ids, **INDEXABLE_EVENT_FILTERS),
            action=action,
            max_budget_seconds=12.0,
        )
    except Exception:
        logger.exception(
            "Error sending Google indexing notifications for %d event(s)", len(ids)
        )


def _enqueue_echo_sync(event_ids):
    """Queue an echo.lu reconcile for events changed by a bulk action.

    The publish/unpublish/cancel actions all go through ``queryset.update()``,
    which does not fire post_save — so the receiver that normally mirrors an
    event onto echo.lu never runs for them. Publishing an event in bulk is the
    single most likely way for one to reach the public site, so without this
    the portal would only catch up on the next scheduled sweep.
    """
    from crush_lu.services import echo_lu

    if not echo_lu.is_sync_enabled() or not event_ids:
        return

    from crush_lu.tasks import sync_event_to_echo_task

    ids = list(event_ids)

    def _run():
        # on_commit runs inside the admin request, and production's
        # ImmediateBackend executes each enqueue synchronously — so this list
        # is a serial chain of HTTP calls, not a fan-out to a worker. Each is
        # individually short, but a bulk publish of two dozen events still
        # adds up past gunicorn's limit, and the database update has already
        # committed by then: the coach would see a failed request for work
        # that actually succeeded.
        #
        # So the batch gets a wall-clock budget of its own. Whatever it does
        # not reach is left to the hourly sweep, which selects by state and
        # will find those events unchanged and waiting.
        import time as _time

        budget = getattr(settings, "ECHO_LU_ADMIN_BUDGET_SECONDS", 30)
        deadline = _time.monotonic() + budget
        for index, pk in enumerate(ids):
            if _time.monotonic() >= deadline:
                logger.info(
                    "[ECHO] Bulk action budget spent after %s/%s events; "
                    "the rest are left to the hourly sweep",
                    index,
                    len(ids),
                )
                return
            sync_event_to_echo_task.enqueue(event_id=pk)

    transaction.on_commit(_run)


def _send_application_selection_notification_safely(registration_id):
    """Notify one selected applicant after the selection transaction commits."""

    try:
        registration = EventRegistration.objects.select_related("event", "user").get(
            pk=registration_id
        )
        from crush_lu.services.curated_group_workflow import (
            registration_has_certified_payable_group,
            requires_curated_group_certification,
        )

        if requires_curated_group_certification(
            registration.event
        ) and not registration_has_certified_payable_group(registration):
            logger.warning(
                "Skipped stale curated selection notification for registration %s",
                registration_id,
            )
            return False
        if registration.status == "pending":
            from crush_lu.email_helpers import (
                send_event_payment_pending_notification,
            )

            return bool(send_event_payment_pending_notification(registration))
        elif registration.status == "confirmed":
            from crush_lu.email_helpers import send_event_registration_confirmation

            return bool(send_event_registration_confirmation(registration))
        return False
    except Exception:
        # A mail outage must never roll back or disguise the auditable group
        # selection. The ordinary resend/support path can recover delivery.
        logger.exception(
            "Failed to notify selected event applicant registration %s",
            registration_id,
        )
        return False


class RegistrationAudienceWidget(forms.RadioSelect):
    """Card-style audience picker with explicit standard and advanced groups."""

    template_name = "admin/crush_lu/meetupevent/widgets/registration_audience.html"
    option_template_name = (
        "admin/crush_lu/meetupevent/widgets/registration_audience_option.html"
    )

    def __init__(self, *args, descriptions=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.descriptions = descriptions or {}

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        option["description"] = self.descriptions.get(str(value), "")
        return option


class MeetupEventAdminForm(forms.ModelForm):
    """Present the two stored access fields as one organizer-facing choice."""

    PRIVATE_INVITATION = "private_invitation"
    STANDARD_CHOICES = (
        ("none", _("All logged-in people")),
        ("completed", _("Participation-ready Crush profile")),
        ("approved", _("Verified members only")),
    )
    ADVANCED_CHOICES = (
        ("profile_exists", _("Any Crush profile (except rejected)")),
        ("unverified", _("Not-yet-verified profiles only (except rejected)")),
        (
            "coach_assigned",
            _("Members with an assigned coach (not Premium)"),
        ),
        (PRIVATE_INVITATION, _("Private invitation")),
    )
    AUDIENCE_CHOICES = (
        (_("Standard"), STANDARD_CHOICES),
        (_("Advanced"), ADVANCED_CHOICES),
    )
    AUDIENCE_DESCRIPTIONS = {
        "none": _(
            "No Crush profile is required. Event age and language restrictions "
            "still apply."
        ),
        "completed": _(
            "For verified members, plus pending members whose phone number is "
            "verified. Pending members can be verified in person at the event."
        ),
        "approved": _(
            "Only members whose Crush profile is already verified can register."
        ),
        "profile_exists": _(
            "Any member with a Crush profile can register, unless the profile "
            "was rejected."
        ),
        "unverified": _(
            "Only incomplete or pending Crush profiles can register. Verified "
            "and rejected profiles are excluded."
        ),
        "coach_assigned": _(
            "Only members with an assigned personal coach can register. This "
            "does not check for an active Premium membership."
        ),
        PRIVATE_INVITATION: _(
            "Only approved invitees can register. Configure the invitation "
            "details in the advanced section below."
        ),
    }

    registration_audience = forms.ChoiceField(
        label=_("Registration audience"),
        choices=AUDIENCE_CHOICES,
        initial="completed",
        widget=RegistrationAudienceWidget(descriptions=AUDIENCE_DESCRIPTIONS),
        help_text=_(
            "Choose who can register. Standard choices cover the usual event "
            "flows; use Advanced only for a specialized audience."
        ),
    )

    # Declared explicitly so the form accepts more characters than the column.
    # Django runs the field's own validators (including max_length) *before*
    # clean_<name>(), so with the model's max_length=4 propagated here, typing
    # the natural "L-2229" would be rejected six characters long before
    # clean_address_postcode could strip the prefix. The model's 4-char limit
    # and its RegexValidator still run, at _post_clean, against the normalized
    # value.
    address_postcode = forms.CharField(
        required=False,
        max_length=8,
        label=_("Postcode"),
        widget=forms.TextInput(attrs={"size": 8, "placeholder": "L-2229"}),
        help_text=_("Four digits. 'L-' optional — stored bare, displayed as L-NNNN."),
    )

    def clean_address_postcode(self):
        return normalize_lu_postcode(self.cleaned_data.get("address_postcode", ""))

    class Meta:
        model = MeetupEvent
        fields = "__all__"
        exclude = ("profile_requirement", "is_private_invitation")

    class Media:
        css = {"all": ("crush_lu/css/admin_registration_audience.css",)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            if self.instance and self.instance.pk:
                audience = (
                    self.PRIVATE_INVITATION
                    if self.instance.is_private_invitation
                    else self.instance.profile_requirement
                )
            else:
                audience = "completed"
            self.initial["registration_audience"] = audience

    @classmethod
    def apply_registration_audience(cls, instance, audience):
        """Map the organizer choice onto the existing stored fields."""
        if audience == cls.PRIVATE_INVITATION:
            instance.is_private_invitation = True
            # profile_requirement is deliberately preserved. Private invitation
            # takes priority at registration time, and the dormant value should
            # still be there if an organizer later disables private mode.
            return instance

        valid_profile_requirements = dict(MeetupEvent.PROFILE_REQUIREMENT_CHOICES)
        if audience not in valid_profile_requirements:
            raise forms.ValidationError(_("Select a valid registration audience."))

        instance.profile_requirement = audience
        instance.is_private_invitation = False
        return instance

    def _effective_curated(self, event_type, registration_mode):
        """Mirror of MeetupEvent.uses_curated_registration, on loose values.

        The behaviour is the product of BOTH fields, so a guard that watches
        only the mode has a hole in it: turning a curated speed-dating event
        into a mixer leaves registration_mode untouched while quietly making
        every later sign-up direct.
        """
        return (
            event_type == "speed_dating"
            and registration_mode == MeetupEvent.REGISTRATION_MODE_CURATED
        )

    def clean(self):
        cleaned_data = super().clean()

        # Refuse to change what a sign-up MEANS once people have signed up.
        #
        # The two modes are different contracts. Flipping curated -> direct
        # leaves the existing applications sitting at "applied" while everyone
        # who arrives afterwards is admitted or waitlisted on the spot; the
        # reverse leaves confirmed seats beside applications that hold none.
        # Either way one event has told two groups of members different things
        # about what their sign-up means, and no later selection reconciles it.
        #
        # Compared on effective behaviour rather than on registration_mode
        # alone, because event_type is the other half of the switch — see
        # _effective_curated.
        #
        # Blocking is the conservative half. Migrating the existing rows is the
        # other, and which direction to migrate them (do confirmed seats become
        # applications? do applications become seats?) has real consequences
        # for people who already signed up — not something to infer from a
        # dropdown change. Cancel the sign-ups, or make a new event.
        if self.instance.pk:
            was_curated = self._effective_curated(
                self.instance.event_type, self.instance.registration_mode
            )
            now_curated = self._effective_curated(
                cleaned_data.get("event_type", self.instance.event_type),
                cleaned_data.get("registration_mode", self.instance.registration_mode),
            )
            if was_curated != now_curated:
                live_signups = self.instance.eventregistration_set.exclude(
                    status="cancelled"
                ).count()
                if live_signups:
                    raise forms.ValidationError(
                        _(
                            "This event already has %(count)s active sign-up(s), "
                            "and this change switches what signing up means — "
                            "between an application the organiser selects from "
                            "and a seat granted on arrival. Cancel the existing "
                            "sign-ups first, or create a new event."
                        )
                        % {"count": live_signups}
                    )

        return cleaned_data

    def _post_clean(self):
        """Apply the audience choice BEFORE the model validates itself.

        `_post_clean` is where Django copies cleaned data onto the instance and
        calls `MeetupEvent.clean()`. `is_private_invitation` is not a form field
        -- it is derived from `registration_audience` -- and deriving it in
        `save()` alone meant model validation ran against the *stored* value.

        That matters now `clean()` skips its address rule for invitation-only
        events: switching a public event to invitation-only was still validated
        as public, and switching a private one to public was still skipped. Both
        judged the change that was being made by the state it was leaving.
        """
        audience = self.cleaned_data.get("registration_audience")
        if audience:
            try:
                self.apply_registration_audience(self.instance, audience)
            except forms.ValidationError as error:
                self.add_error("registration_audience", error)
        super()._post_clean()

    def save(self, commit=True):
        instance = super().save(commit=False)
        self.apply_registration_audience(
            instance, self.cleaned_data["registration_audience"]
        )
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class EventRegistrationAdminForm(forms.ModelForm):
    """Admin form for EventRegistration that enforces event age restrictions."""

    class Meta:
        model = EventRegistration
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()

        # Fall back to the bound instance for fields that aren't in the
        # submitted form. Matters for:
        #   - EventRegistrationInline (event is implicit from the parent admin)
        #   - changelist list_editable updates (only status / payment_confirmed
        #     are submitted; event and user come from the row being edited)
        event = cleaned_data.get("event")
        if not event and getattr(self.instance, "event_id", None):
            event = self.instance.event
        user = cleaned_data.get("user")
        if not user and getattr(self.instance, "user_id", None):
            user = self.instance.user
        status = cleaned_data.get("status") or getattr(self.instance, "status", None)
        payment_confirmed = cleaned_data.get(
            "payment_confirmed",
            getattr(self.instance, "payment_confirmed", False),
        )

        # Truly-missing data (new row with nothing posted): let Django's own
        # required-field errors surface rather than masking them here.
        if not event or not user:
            return cleaned_data

        # "Applied" holds no seat, so choosing it on an event that admits
        # members on arrival silently releases one. Checked HERE rather than
        # relying on EventRegistration.clean(), because this is the only place
        # that can see the event when the inline sits under a *new* event:
        # BaseInlineFormSet._construct_form sets only `event_id = parent.pk`,
        # which is None while the parent is unsaved, and the FK object is never
        # copied onto the instance because EventRegistrationInline.fields does
        # not list `event`. The parent reaches us as `cleaned_data["event"]`
        # instead -- BaseInlineFormSet.add_fields puts an InlineForeignKeyField
        # there whose value is the parent object, saved or not. That is the
        # same fallback the age gate above already depends on.
        if status == "applied":
            applied_message = EventRegistration.applied_status_message(event)
            if applied_message is not None:
                raise forms.ValidationError(
                    {"status": applied_message}
                    if "status" in self.fields
                    else applied_message
                )

        previous_status = None
        if self.instance.pk:
            previous_status = (
                EventRegistration.objects.filter(pk=self.instance.pk)
                .values_list("status", flat=True)
                .first()
            )
        transition_message = self.instance.group_transition_status_message(
            event=event,
            previous_status=previous_status,
            new_status=status,
        )
        if transition_message is not None:
            raise forms.ValidationError(
                {"status": transition_message}
                if "status" in self.fields
                else transition_message
            )
        event_change_message = self.instance.group_event_change_message(
            event=event,
            new_status=status,
        )
        if event_change_message is not None:
            raise forms.ValidationError(
                {"event": event_change_message}
                if "event" in self.fields
                else event_change_message
            )

        previous_payment_confirmed = False
        if self.instance.pk:
            previous_payment_confirmed = (
                EventRegistration.objects.filter(pk=self.instance.pk)
                .values_list("payment_confirmed", flat=True)
                .first()
                or False
            )
        if (
            payment_confirmed
            and not previous_payment_confirmed
            and event.uses_curated_registration
            and event.group_size
        ):
            active_generation = (
                CuratedEventGroup.objects.filter(
                    event=event,
                    status__in=(
                        CuratedEventGroup.STATUS_PROVISIONAL,
                        CuratedEventGroup.STATUS_LOCKED,
                    ),
                )
                .order_by("-generation")
                .values_list("generation", flat=True)
                .first()
            )
            membership = None
            if self.instance.pk and active_generation is not None:
                membership = (
                    CuratedEventGroupMembership.objects.select_related("group")
                    .filter(
                        event=event,
                        registration_id=self.instance.pk,
                        released_at__isnull=True,
                        group__generation=active_generation,
                        group__status__in=(
                            CuratedEventGroup.STATUS_PROVISIONAL,
                            CuratedEventGroup.STATUS_LOCKED,
                        ),
                    )
                    .first()
                )
            certified = False
            if membership is not None and status in SEAT_HOLDING_STATUSES:
                try:
                    current_summary = membership.group.schedule_viability(
                        evaluate_preferences=False
                    )
                except ValidationError:
                    current_summary = None
                certified = bool(
                    current_summary
                    and current_summary["schedule_digest"]
                    == membership.group.schedule_digest
                )
            if not certified:
                raise forms.ValidationError(
                    {
                        "payment_confirmed": _(
                            "Payment can be recorded only after the complete "
                            "current provisional group was explicitly selected "
                            "and its certified roster is still intact."
                        )
                    }
                    if "payment_confirmed" in self.fields
                    else _(
                        "Payment can be recorded only after the complete current "
                        "provisional group was explicitly selected."
                    )
                )

        # Cancelled registrations don't need to pass the age gate.
        if status == "cancelled":
            return cleaned_data

        event_has_age_restriction = event.min_age > 18 or event.max_age < 99
        if not event_has_age_restriction:
            return cleaned_data

        profile = getattr(user, "crushprofile", None)
        user_age = profile.age if profile else None

        if user_age is None:
            raise forms.ValidationError(
                _(
                    "Cannot register %(user)s: this event has age restrictions "
                    "(%(min)d–%(max)d) and the user has no date of birth on file."
                )
                % {
                    "user": user.get_full_name() or user.username,
                    "min": event.min_age,
                    "max": event.max_age,
                }
            )

        if not (event.min_age <= user_age <= event.max_age):
            raise forms.ValidationError(
                _(
                    "Cannot register %(user)s (age %(age)d): this event is "
                    "restricted to ages %(min)d–%(max)d."
                )
                % {
                    "user": user.get_full_name() or user.username,
                    "age": user_age,
                    "min": event.min_age,
                    "max": event.max_age,
                }
            )

        return cleaned_data


# Inline admin for Event Registrations
class EventRegistrationInline(admin.TabularInline):
    model = EventRegistration
    form = EventRegistrationAdminForm
    extra = 0
    autocomplete_fields = ["user"]
    fields = ("user", "status", "payment_confirmed", "registered_at")
    readonly_fields = ("registered_at",)
    can_delete = False
    show_change_link = True


# Inline admin for Event Invitations (Private Events)
class EventInvitationInline(admin.TabularInline):
    model = EventInvitation
    extra = 0
    fields = (
        "guest_email",
        "guest_first_name",
        "guest_last_name",
        "status",
        "approval_status",
        "invitation_sent_at",
    )
    readonly_fields = ("invitation_sent_at", "invitation_code")
    can_delete = True
    show_change_link = True
    verbose_name = "Private Invitation"
    verbose_name_plural = "Private Invitations"


# Inline admin for Voting Session
class EventVotingSessionInline(admin.StackedInline):
    model = EventVotingSession
    extra = 0
    max_num = 1
    fields = (
        ("is_active", "total_votes"),
        ("voting_start_time", "voting_end_time"),
        ("winning_presentation_style", "winning_speed_dating_twist"),
    )
    readonly_fields = ("total_votes",)
    can_delete = False


# Inline admin for Presentation Queue
class PresentationQueueInline(admin.TabularInline):
    model = PresentationQueue
    extra = 0
    autocomplete_fields = ["user"]
    fields = (
        "user",
        "presentation_order",
        "status",
        "started_at",
        "completed_at",
        "duration_seconds",
    )
    readonly_fields = ("duration_seconds", "started_at", "completed_at")
    can_delete = False
    ordering = ["presentation_order"]
    show_change_link = True


class CuratedEventGroupInline(admin.TabularInline):
    """Read-only visibility into the projection chosen for this evening.

    Group transitions rebuild rosters and schedules as one audited operation;
    exposing ordinary inline add/delete controls would let a coach bypass the
    viability and lock safeguards one row at a time.
    """

    model = CuratedEventGroup
    extra = 0
    can_delete = False
    fields = (
        "generation",
        "group_number",
        "status",
        "member_manifest",
        "schedule_manifest",
        "policy_version",
        "seed",
        "provisional_at",
        "locked_at",
        "degraded_at",
        "degradation_reason",
    )
    readonly_fields = fields
    verbose_name = _("Curated parallel group")
    verbose_name_plural = _("Curated parallel groups (read-only)")

    def get_queryset(self, request):
        latest_generation = (
            CuratedEventGroup.objects.filter(event_id=OuterRef("event_id"))
            .order_by("-generation")
            .values("generation")[:1]
        )
        return (
            super()
            .get_queryset(request)
            .annotate(_latest_generation=Subquery(latest_generation))
            .filter(generation=F("_latest_generation"))
            .prefetch_related(
                "memberships__registration__user",
            )
        )

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Members"))
    def member_manifest(self, obj):
        rows = []
        for membership in obj.memberships.all():
            user = membership.registration.user
            label = user.get_full_name() or user.email or user.username
            if membership.released_at:
                label = _("%(member)s (released)") % {"member": label}
            rows.append(str(label))
        return format_html("{}", ", ".join(rows) if rows else _("No members"))

    @admin.display(description=_("Stored schedule"))
    def schedule_manifest(self, obj):
        summary = obj.viability_summary or {}
        label = _("%(rounds)s rounds; minimum %(dates)s dates") % {
            "rounds": summary.get("rounds", "—"),
            "dates": summary.get("minimum_dates", "—"),
        }
        url = reverse("admin:crush_lu_curatedeventgroup_change", args=[obj.pk])
        return format_html('<a href="{}">{}</a>', url, label)


@admin.register(CuratedEventGroup)
class CuratedEventGroupAdmin(admin.ModelAdmin):
    """Read-only audit trail; detailed schedules load only on one group page."""

    list_display = (
        "event",
        "generation",
        "group_number",
        "status",
        "policy_version",
        "provisional_at",
        "locked_at",
        "degraded_at",
    )
    list_filter = ("status", "policy_version")
    search_fields = ("event__title", "seed")
    list_select_related = ("event",)
    readonly_fields = (
        "event",
        "generation",
        "group_number",
        "status",
        "policy_version",
        "seed",
        "member_manifest",
        "stored_schedule",
        "audit_data",
        "viability_summary",
        "schedule_digest",
        "created_by",
        "created_at",
        "provisional_by",
        "provisional_at",
        "locked_by",
        "locked_at",
        "degraded_by",
        "degraded_at",
        "degradation_reason",
        "cancelled_by",
        "cancelled_at",
        "cancellation_reason",
        "updated_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Members"))
    def member_manifest(self, obj):
        memberships = obj.memberships.select_related("registration__user").order_by(
            "position", "pk"
        )
        labels = []
        for membership in memberships:
            user = membership.registration.user
            label = user.get_full_name() or user.email or user.username
            if membership.released_at:
                label = _("%(member)s (released)") % {"member": label}
            labels.append(str(label))
        return format_html("{}", ", ".join(labels) if labels else _("No members"))

    @admin.display(description=_("Stored schedule"))
    def stored_schedule(self, obj):
        pairings = obj.pairings.prefetch_related(
            "participants__registration__user"
        ).order_by("round_number", "table_number")
        rows = []
        for pairing in pairings:
            names = []
            for participant in pairing.participants.all():
                user = participant.registration.user
                names.append(user.get_full_name() or user.email or user.username)
            rows.append(
                _("R%(round)d/T%(table)d: %(members)s")
                % {
                    "round": pairing.round_number,
                    "table": pairing.table_number,
                    "members": " ↔ ".join(names) if names else "—",
                }
            )
        return format_html("{}", " | ".join(rows) if rows else _("No schedule"))


class MeetupEventAdmin(AutoTranslateMixin, TranslationAdmin):
    form = MeetupEventAdminForm
    list_display = (
        "title",
        "event_type",
        "date_time",
        "canton",
        "location",
        "get_event_languages",
        "get_registration_count",
        "get_confirmed_count",
        "get_waitlist_count",
        "get_applied_count",
        "max_participants",
        "get_spots_remaining",
        "is_private_invitation",
        "get_invited_users_count",
        "get_voting_status",
        "is_published",
        "is_cancelled",
        "get_echo_lu_status",
    )

    def get_queryset(self, request):
        """Annotate the changelist with the registration counts it displays.

        get_confirmed_count / get_waitlist_count / get_applied_count all prefer
        an annotation when one is present and fall back to a per-row query when
        it is not — so without this the default 50-row page fires up to 150
        extra COUNTs, and the annotation added alongside get_applied_count
        would never actually be used.
        """
        return super().get_queryset(request).with_registration_counts()

    list_filter = (
        "event_type",
        "registration_mode",
        "is_published",
        "is_cancelled",
        "is_private_invitation",
        "enable_activity_voting",
        "profile_requirement",
        EventCapacityFilter,
        "date_time",
    )
    search_fields = (
        "title",
        "description",
        "location",
        "address",
        "address_street",
        "address_number",
        "address_town",
        "address_postcode",
        "canton",
    )
    readonly_fields = (
        # Legacy free text, read-only from here on. echo.lu now reads the
        # structured fields, so this box no longer feeds anything — leaving it
        # writable would just invite staff to keep typing addresses into it.
        "address",
        "created_at",
        "updated_at",
        "invitation_code",
        "get_registration_count",
        "get_confirmed_count",
        "get_waitlist_count",
        "get_spots_remaining",
        "get_revenue",
        "get_voting_status",
        "get_presentation_status",
        "get_echo_lu_detail",
        # Event cancellation has money and notification side effects. It must
        # go through the canonical action below, never a bare form save.
        "is_cancelled",
    )
    inlines = [
        QuizEventInline,
        CuratedEventGroupInline,
        EventRegistrationInline,
        EventInvitationInline,
        EventVotingSessionInline,
        PresentationQueueInline,
    ]
    actions = [
        "publish_events",
        "unpublish_events",
        "generate_curated_groups",
        "approve_curated_groups",
        "invite_approved_curated_groups",
        "lock_curated_groups",
        "start_curated_round_one",
        "repair_degraded_curated_groups",
        "cancel_events",
        "send_event_reminders",
        "export_attendees_csv",
        "sync_to_echo_lu",
        "withdraw_from_echo_lu",
        "ping_google_indexing",
    ]
    filter_horizontal = ("invited_users", "coaches")
    # get_echo_lu_status renders one column per row; without this the changelist
    # would issue a query per event to resolve the OneToOne.
    list_select_related = ["echo_sync"]

    fieldsets = (
        (
            "Event Information",
            {
                "fields": (
                    "title",
                    "description",
                    "image",
                    "event_type",
                    "enable_activity_voting",
                ),
                "description": "Title and description are translatable (English/French/German tabs will appear above fields)",
            },
        ),
        (
            "Location & Timing",
            {
                "fields": (
                    "canton",
                    "location",
                    ("address_number", "address_street"),
                    ("address_postcode", "address_town"),
                    ("latitude", "longitude"),
                    "date_time",
                    "duration_minutes",
                ),
                "description": (
                    "<strong>Location</strong> is the venue NAME (e.g. Café Konrad). "
                    "The street, number, postcode and town go in their own fields — "
                    "echo.lu publishes them separately on the national events portal "
                    "and prints them on wallet tickets, so don't repeat the venue "
                    "name in the street. <strong>Canton</strong> is the only part of "
                    "the location anonymous visitors can see."
                ),
            },
        ),
        (
            "Legacy address",
            {
                "fields": ("address",),
                "classes": ("collapse",),
                "description": (
                    "Read-only. What was typed before the address was split into "
                    "fields — kept so it can be transcribed into the fields "
                    "above, then ignored. echo.lu reads the structured fields now."
                ),
            },
        ),
        (
            "Capacity & Requirements",
            {
                "fields": (
                    "max_participants",
                    ("group_size", "planned_groups"),
                    "reserved_premium_seats",
                    ("max_participants_m", "max_participants_f", "max_participants_nb"),
                    "min_age",
                    "max_age",
                    "languages",
                    "has_food_component",
                    "allow_plus_ones",
                ),
                "description": (
                    "Set all three gender caps to activate gender-aware waitlisting. "
                    "Leave all blank for total-only cap."
                ),
            },
        ),
        (
            _("Registration Audience"),
            {
                "fields": ("registration_audience",),
                "description": _(
                    "Choose one audience. The three standard options cover the "
                    "usual event flows; specialized access rules are grouped "
                    "under Advanced."
                ),
            },
        ),
        (
            "Event Coaches",
            {
                "fields": ("coaches",),
                "description": "Assign coaches to facilitate this event. They will be shown on the attendees page.",
            },
        ),
        (
            "Registration",
            {
                "fields": (
                    "registration_deadline",
                    "registration_fee",
                    "registration_mode",
                ),
                "description": (
                    "Curated mode takes effect on speed-dating events only. "
                    "Sign-ups land as \u201cApplied\u201d and hold no seat, so "
                    "applications can outnumber the places; you compose the "
                    "group by moving the ones you want to Confirmed (free "
                    "event) or Pending Payment (paid event)."
                ),
            },
        ),
        (
            _("Advanced: Private Invitation Settings"),
            {
                "fields": (
                    "invited_users",
                    "invitation_code",
                    "max_invited_guests",
                    "invitation_expires_at",
                ),
                "classes": ("collapse",),
                "description": _(
                    "Configure this event as invitation-only. You can invite "
                    "existing users directly or send external guest invitations "
                    "(managed via EventInvitation inline below)."
                ),
            },
        ),
        (
            "📊 Event Statistics",
            {
                "fields": (
                    "get_registration_count",
                    "get_confirmed_count",
                    "get_waitlist_count",
                    "get_spots_remaining",
                    "get_revenue",
                ),
                "classes": ("collapse",),
                "description": "Real-time event statistics and capacity information",
            },
        ),
        (
            "🎯 Phase Status Overview",
            {
                "fields": (
                    "get_voting_status",
                    "get_presentation_status",
                ),
                "classes": ("collapse",),
                "description": "Track progress through the event voting/presentation phases",
            },
        ),
        ("Status", {"fields": ("is_published", "is_cancelled")}),
        (
            "🌍 echo.lu",
            {
                "fields": ("get_echo_lu_detail",),
                "description": (
                    "Published, public, upcoming events are mirrored to echo.lu, "
                    "Luxembourg's national events portal. Private invitation "
                    "events are never sent. See docs/integrations/echo-lu-sync.md."
                ),
            },
        ),
        ("Metadata", {"fields": ("created_at", "updated_at")}),
    )

    def get_registration_count(self, obj):
        """Total registrations (all statuses)"""
        return obj.eventregistration_set.count()

    get_registration_count.short_description = _("📝 Total Registrations")

    def get_invited_users_count(self, obj):
        """Count of directly invited existing users"""
        count = obj.invited_users.count()
        if count > 0:
            return f"👥 {count}"
        return "-"

    get_invited_users_count.short_description = _("Invited Users")

    def get_confirmed_count(self, obj):
        """Confirmed registrations only"""
        return obj.get_confirmed_count()

    get_confirmed_count.short_description = _("✅ Confirmed")

    def get_waitlist_count(self, obj):
        """Waitlisted registrations"""
        return obj.get_waitlist_count()

    get_waitlist_count.short_description = _("⏳ Waitlist")

    def get_spots_remaining(self, obj):
        """Calculate remaining spots with optional gender breakdown"""
        remaining = obj.spots_remaining
        if remaining == 0:
            label = f"🔴 FULL (0/{obj.max_participants})"
        elif remaining <= 5:
            label = f"🟡 {remaining}/{obj.max_participants}"
        else:
            label = f"🟢 {remaining}/{obj.max_participants}"

        if obj.gender_limits_active:
            m_count = obj.get_confirmed_count_for_gender("M")
            f_count = obj.get_confirmed_count_for_gender("F")
            nb_count = obj.get_confirmed_count_for_gender("NB")
            label += (
                f" | M:{m_count}/{obj.max_participants_m}"
                f" F:{f_count}/{obj.max_participants_f}"
                f" NB:{nb_count}/{obj.max_participants_nb}"
            )

        return label

    get_spots_remaining.short_description = _("Spots Available")

    def get_event_languages(self, obj):
        """Display event languages"""
        if not obj.languages:
            return _("Any")
        return ", ".join(
            lang["flag"] + " " + lang["name"] for lang in obj.get_languages_display
        )

    get_event_languages.short_description = _("Languages")

    def get_revenue(self, obj):
        """Actual captured cash revenue; credit is not new cash income."""
        payments = PaymentTransaction.objects.filter(
            event=obj,
            provider__in=(
                PaymentTransaction.Provider.SUMUP,
                PaymentTransaction.Provider.MANUAL,
            ),
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            status=PaymentTransaction.Status.PAID,
        )
        totals = payments.aggregate(total=Sum("amount"), count=Count("id"))
        return f"€{(totals['total'] or 0):.2f} ({totals['count']} paid)"

    get_revenue.short_description = _("💰 Revenue")

    def get_voting_status(self, obj):
        """Display Phase 1 voting status"""
        try:
            voting_session = obj.voting_session
            if not voting_session.is_active and voting_session.voting_end_time:
                return f"✅ Completed ({voting_session.total_votes} votes) | Winners: {voting_session.winning_presentation_style or 'N/A'} & {voting_session.winning_speed_dating_twist or 'N/A'}"
            elif voting_session.is_active:
                return f"🟢 ACTIVE ({voting_session.total_votes} votes so far)"
            else:
                return "⏸️ Not Started"
        except EventVotingSession.DoesNotExist:
            return "❌ No Voting Session"

    get_voting_status.short_description = _("🗳️ Phase 1: Voting")

    def get_presentation_status(self, obj):
        """Display Phase 2 presentation status"""
        presentations = obj.presentation_queue.all()
        if not presentations.exists():
            return "❌ Not Initialized"

        total = presentations.count()
        completed = presentations.filter(status="completed").count()
        in_progress = presentations.filter(status="in_progress").exists()

        if completed == total:
            return f"✅ All Complete ({total}/{total})"
        elif in_progress:
            return f"🟢 IN PROGRESS ({completed}/{total} done)"
        elif completed > 0:
            return f"⏸️ Paused ({completed}/{total} done)"
        else:
            return f"⏳ Ready to Start (0/{total})"

    get_presentation_status.short_description = _("🎤 Phase 2: Presentations")

    def _single_curated_event(self, request, queryset):
        events = list(queryset.order_by("pk")[:2])
        if len(events) != 1:
            django_messages.error(
                request,
                _(
                    "Select exactly one event for a curated-group lifecycle "
                    "action. Payment invitations and final locks are never bulked "
                    "across evenings."
                ),
            )
            return None
        return events[0]

    @admin.action(description=_("🧩 Generate fair curated groups"))
    def generate_curated_groups(self, request, queryset):
        """Store one complete post-application projection per selected event."""

        from crush_lu.services.curated_group_workflow import (
            generate_group_projection,
        )

        event = self._single_curated_event(request, queryset)
        if event is None:
            return
        try:
            result = generate_group_projection(event.pk, actor=request.user)
        except ValidationError as error:
            django_messages.error(
                request,
                _("“%(title)s”: groups were not generated (%(reason)s).")
                % {"title": event.title, "reason": "; ".join(error.messages)},
            )
            return
        django_messages.success(
            request,
            _(
                "“%(title)s”: stored generation %(generation)d with "
                "%(groups)d viable group(s). Review it before approval."
            )
            % {
                "title": event.title,
                "generation": result.generation,
                "groups": result.group_count,
            },
        )

    @admin.action(description=_("✅ Approve all current fair groups"))
    def approve_curated_groups(self, request, queryset):
        """Certify one complete current generation without contacting members."""

        from crush_lu.services.curated_group_workflow import (
            approve_current_generation,
        )

        event = self._single_curated_event(request, queryset)
        if event is None:
            return
        try:
            approved = approve_current_generation(event.pk, actor=request.user)
        except ValidationError as error:
            django_messages.error(
                request,
                _("“%(title)s”: groups were not approved (%(reason)s).")
                % {"title": event.title, "reason": "; ".join(error.messages)},
            )
            return
        django_messages.success(
            request,
            _(
                "“%(title)s”: approved generation %(generation)d with "
                "%(groups)d group(s). No member has been contacted yet."
            )
            % {
                "title": event.title,
                "generation": approved.generation,
                "groups": len(approved.group_ids),
            },
        )

    @admin.action(description=_("✉️ Invite the approved generation to pay"))
    def invite_approved_curated_groups(self, request, queryset):
        """Select exactly the complete approved roster and notify on commit."""

        from crush_lu.services.curated_group_workflow import (
            get_approved_current_generation,
        )

        event = self._single_curated_event(request, queryset)
        if event is None:
            return
        try:
            approved = get_approved_current_generation(event.pk)
        except ValidationError as error:
            django_messages.error(
                request,
                _("“%(title)s”: the approved groups were not ready (%(reason)s).")
                % {"title": event.title, "reason": "; ".join(error.messages)},
            )
            return
        if not approved.applied_registration_ids:
            django_messages.info(
                request,
                _(
                    "“%(title)s”: generation %(generation)d has no remaining "
                    "applicants to invite."
                )
                % {"title": event.title, "generation": approved.generation},
            )
            return
        # The registration action owns capacity, whole-roster, no-gap fairness
        # and stale-state checks. The IDs come from every group server-side;
        # no checkbox selection can substitute a preferred subset.
        EventRegistrationAdmin(
            EventRegistration, self.admin_site
        ).confirm_registrations(
            request,
            EventRegistration.objects.filter(
                pk__in=approved.applied_registration_ids,
                event_id=event.pk,
                status="applied",
            ),
        )

    @admin.action(description=_("🔒 Lock checked-in curated groups"))
    def lock_curated_groups(self, request, queryset):
        """Freeze complete checked-in rosters without claiming delivery."""

        from crush_lu.services.curated_group_workflow import lock_current_generation

        event = self._single_curated_event(request, queryset)
        if event is None:
            return
        try:
            group_ids = lock_current_generation(event.pk, actor=request.user)
        except ValidationError as error:
            django_messages.error(
                request,
                _("“%(title)s”: groups were not locked (%(reason)s).")
                % {"title": event.title, "reason": "; ".join(error.messages)},
            )
            return
        django_messages.success(
            request,
            _(
                "“%(title)s”: locked %(groups)d checked-in group(s). Round one "
                "has not been marked as started yet."
            )
            % {"title": event.title, "groups": len(group_ids)},
        )

    @admin.action(description=_("▶️ Mark curated round one as started"))
    def start_curated_round_one(self, request, queryset):
        """Record the explicit service-delivery boundary after final lock."""

        from crush_lu.services.curated_group_workflow import start_curated_rounds

        event = self._single_curated_event(request, queryset)
        if event is None:
            return
        try:
            group_ids = start_curated_rounds(event.pk, actor=request.user)
        except ValidationError as error:
            django_messages.error(
                request,
                _("“%(title)s”: round one was not started (%(reason)s).")
                % {"title": event.title, "reason": "; ".join(error.messages)},
            )
            return
        django_messages.success(
            request,
            _(
                "“%(title)s”: round one explicitly started for %(groups)d "
                "locked group(s)."
            )
            % {"title": event.title, "groups": len(group_ids)},
        )

    @admin.action(description=_("🛟 Reproject or compensate degraded groups"))
    def repair_degraded_curated_groups(self, request, queryset):
        """Run the same idempotent remedy normally scheduled by signals."""

        from crush_lu.services.curated_group_workflow import (
            repair_degraded_event_groups,
        )

        event = self._single_curated_event(request, queryset)
        if event is None:
            return
        try:
            remedy = repair_degraded_event_groups(event.pk, actor=request.user)
        except ValidationError as error:
            django_messages.error(
                request,
                _("“%(title)s”: degraded groups were not repaired (%(reason)s).")
                % {"title": event.title, "reason": "; ".join(error.messages)},
            )
            return
        django_messages.info(
            request,
            _("“%(title)s”: curated-group remedy result: %(action)s.")
            % {"title": event.title, "action": remedy.action},
        )

    @admin.action(description=_("✅ Publish selected events"))
    def publish_events(self, request, queryset):
        # `queryset.update()` emits no signals and runs no `clean()`, so the
        # address rule on the model cannot see this path. Without the check
        # below, selecting a batch of drafts publishes the incomplete ones and
        # sends them straight to a national portal with half an address —
        # the one publish route that bypasses every guard on the change form.
        # `MeetupEvent.unmet_publish_requirements()` is the one definition of
        # what a publishable event needs -- shared with `clean()` and with
        # `backfill_event_addresses --audit`. Re-deriving it here is what let
        # this action forget `canton`, which `clean()` has required all along.
        incomplete = [
            event
            for event in queryset
            if event.unmet_publish_requirements(as_published=True)
        ]
        if incomplete:
            queryset = queryset.exclude(pk__in=[event.pk for event in incomplete])
            django_messages.warning(
                request,
                _(
                    "Not published — {details}. Fill those in, or run "
                    "`manage.py backfill_event_addresses`."
                ).format(
                    # Name the actual gaps per event. A fixed "street, postcode
                    # and town" sends staff hunting through address boxes that
                    # are already filled when the only thing missing is canton.
                    details="; ".join(
                        "{title} needs {fields}".format(
                            title=event,
                            fields=", ".join(
                                name.removeprefix("address_")
                                for name in event.unmet_publish_requirements(
                                    as_published=True
                                )
                            ),
                        )
                        for event in incomplete
                    )
                ),
            )

        # Snapshot before .update(): the queryset is lazy and may well filter on
        # is_published=False, which matches nothing once the update lands.
        event_ids = list(queryset.values_list("pk", flat=True))
        updated = queryset.update(is_published=True)
        _enqueue_echo_sync(event_ids)

        # Real-time Google Indexing update (single-budget batch, dynamic privacy check)
        try:
            from crush_lu.services.google_indexing import notify_events_indexing

            notify_events_indexing(
                MeetupEvent.objects.filter(pk__in=event_ids),
                action=None,
                max_budget_seconds=12.0,
            )
        except Exception:
            logger.exception("Error sending Google indexing notifications on publish")

        django_messages.success(
            request, _("Published {count} event(s)").format(count=updated)
        )

    @admin.action(description=_("❌ Unpublish selected events"))
    def unpublish_events(self, request, queryset):
        event_ids = list(queryset.values_list("pk", flat=True))
        updated = queryset.update(is_published=False)
        _enqueue_echo_sync(event_ids)

        # Real-time Google Indexing removal (single-budget batch)
        try:
            from crush_lu.services.google_indexing import notify_events_indexing

            notify_events_indexing(
                MeetupEvent.objects.filter(pk__in=event_ids),
                action="URL_DELETED",
                max_budget_seconds=12.0,
            )
        except Exception:
            logger.exception("Error sending Google indexing removals on unpublish")

        django_messages.success(
            request, _("Unpublished {count} event(s)").format(count=updated)
        )

    # --- echo.lu ---------------------------------------------------------

    @admin.display(description=_("echo.lu"))
    def get_echo_lu_status(self, obj):
        """Compact listing state for the changelist."""
        sync = getattr(obj, "echo_sync", None)
        if sync is None:
            return "—"
        icons = {
            "synced": "✅",
            "pending": "⏳",
            "failed": "⚠️",
            "withdrawn": "🚫",
            "suppressed": "🔕",
            "cancelled": "📢",
            # The one state that needs a human. It should not read as just
            # another row while a coach scans the changelist.
            "orphaned": "🛑",
        }
        return f"{icons.get(sync.status, '')} {sync.get_status_display()}".strip()

    @admin.display(description=_("echo.lu listing"))
    def get_echo_lu_detail(self, obj):
        """Full sync state on the change form, including the last error.

        The error text is the whole point of surfacing this: echo.lu rejects an
        experience wholesale on one unknown category slug, and without the
        message here the only symptom is an event that quietly never appears on
        the portal.
        """
        from crush_lu.services import echo_lu

        if not obj.pk:
            return _("Save the event first.")

        sync = getattr(obj, "echo_sync", None)
        if sync is None:
            if not echo_lu.is_sync_enabled():
                return _("echo.lu sync is disabled for this environment.")
            if not echo_lu.should_publish(obj):
                return _("Not eligible: only published, public, upcoming events sync.")
            return _("Not synced yet — saves on the next sync run.")

        rows = [
            format_html("<strong>{}</strong>", sync.get_status_display()),
            format_html("Experience id: {}", sync.experience_id or "—"),
        ]
        if sync.last_synced_at:
            rows.append(
                format_html(
                    "Last synced: {}", sync.last_synced_at.strftime("%Y-%m-%d %H:%M")
                )
            )
        if sync.last_error:
            rows.append(
                format_html(
                    '<span style="color:#b91c1c">Last error: {}</span>', sync.last_error
                )
            )
        return mark_safe("<br>".join(rows))

    @admin.action(description=_("🌍 Sync selected events to echo.lu"))
    def sync_to_echo_lu(self, request, queryset):
        from crush_lu.services import echo_lu

        if not echo_lu.is_sync_enabled():
            django_messages.warning(
                request,
                _(
                    "echo.lu sync is disabled. Set ECHO_LU_SYNC_ENABLED=true and "
                    "ECHO_LU_API_KEY in the environment."
                ),
            )
            return

        # Run inline rather than enqueued so the coach sees the outcome — this
        # action exists precisely to retry after a rejection and read the
        # error, which an enqueued task cannot report back.
        #
        # The admin page size does not bound this: at the default timeout with
        # retries, two slow events are enough to pass gunicorn's 120s and lose
        # the whole response. So the client is the impatient one, and a shared
        # wall-clock budget stops the loop while there is still a page to
        # render — the rest is named, and the hourly sweep takes it.
        import time as _time

        timeout = getattr(settings, "ECHO_LU_SIGNAL_TIMEOUT_SECONDS", 5)
        budget = getattr(settings, "ECHO_LU_ADMIN_BUDGET_SECONDS", 30)
        deadline = _time.monotonic() + budget - timeout

        client = echo_lu.EchoLuClient(timeout=timeout, max_retries=0)
        succeeded, failed = 0, 0
        inert = []
        events = list(queryset.select_related("echo_sync"))
        deferred = []
        for index, event in enumerate(events):
            if _time.monotonic() >= deadline:
                deferred = events[index:]
                break
            try:
                outcome = echo_lu.sync_event(event, client=client, force=True)
            except echo_lu.EchoLuError as exc:
                failed += 1
                django_messages.error(request, f"[{event.pk}] {event.title}: {exc}")
                continue

            # Not every non-exception outcome reached echo.lu. "blocked" is an
            # orphaned listing that force cannot override, "skipped" an event
            # that does not qualify — counting either as a success tells a
            # coach the listing is live when nothing happened at all.
            if outcome in ("blocked", "skipped", "suppressed", "disabled"):
                inert.append((event, outcome))
            else:
                succeeded += 1

        if succeeded:
            django_messages.success(
                request, _("Synced {count} event(s) to echo.lu").format(count=succeeded)
            )
        for event, outcome in inert:
            django_messages.warning(
                request,
                _(
                    "[{pk}] {title}: not sent ({outcome}) — see the echo.lu "
                    "section on the event for why."
                ).format(pk=event.pk, title=event.title, outcome=outcome),
            )
        if failed:
            django_messages.warning(
                request,
                _(
                    "{count} event(s) were rejected — the reason is saved on each "
                    "event and shown on its change form."
                ).format(count=failed),
            )
        if deferred:
            django_messages.warning(
                request,
                _(
                    "{count} event(s) were not attempted — echo.lu was too slow "
                    "to get through the selection in one request. They stay "
                    "queued for the hourly sync, or select fewer and retry."
                ).format(count=len(deferred)),
            )

    @admin.action(description=_("🚫 Remove selected events from echo.lu"))
    def withdraw_from_echo_lu(self, request, queryset):
        """Take listings down without unpublishing the event on crush.lu."""
        from crush_lu.services import echo_lu

        if not echo_lu.is_sync_enabled():
            django_messages.warning(
                request, _("echo.lu sync is disabled for this environment.")
            )
            return

        # Bounded exactly like the sync action next door — same request, same
        # gunicorn limit, and the default retrying client would let two
        # unreachable listings hold it for most of three minutes.
        import time as _time

        timeout = getattr(settings, "ECHO_LU_SIGNAL_TIMEOUT_SECONDS", 5)
        budget = getattr(settings, "ECHO_LU_ADMIN_BUDGET_SECONDS", 30)
        deadline = _time.monotonic() + budget - timeout

        client = echo_lu.EchoLuClient(timeout=timeout, max_retries=0)
        withdrawn = 0
        events = list(queryset.select_related("echo_sync"))
        deferred = []
        for index, event in enumerate(events):
            if _time.monotonic() >= deadline:
                deferred = events[index:]
                break
            try:
                # explicit: the coach is removing a listing whose event may
                # still qualify, so the row has to hold the removal against
                # the next sweep rather than be re-published within the hour.
                if (
                    echo_lu.withdraw_event(event, client=client, explicit=True)
                    == "withdrawn"
                ):
                    withdrawn += 1
            except echo_lu.EchoLuError as exc:
                django_messages.error(request, f"[{event.pk}] {event.title}: {exc}")

        django_messages.success(
            request,
            _("Removed {count} listing(s) from echo.lu").format(count=withdrawn),
        )
        if deferred:
            # Record the intent for the ones we ran out of time for, so the
            # message below is true. The coach asked for all of these to come
            # down; without this the sweep would find their events still
            # eligible and republish instead.
            #
            # Per event with get_or_create, matching what withdraw_event does:
            # an event whose first sync is still pending has no sync row at
            # all, and one whose create was refused has a row with a blank id.
            # A bulk update over existing rows with an id would skip both —
            # and those are exactly the events with nothing on echo.lu yet, so
            # "removed" for them means "never create it", which is the only
            # instruction that can be lost here.
            from crush_lu.models.echo_lu import EchoExperienceSync

            for event in deferred:
                sync, _created = EchoExperienceSync.objects.get_or_create(event=event)
                if not sync.removal_requested:
                    sync.removal_requested = True
                    sync.save(update_fields=["removal_requested", "updated_at"])
            django_messages.warning(
                request,
                _(
                    "{count} listing(s) were not attempted here — echo.lu was "
                    "too slow to get through the selection in one request. The "
                    "removal is recorded and the hourly sync keeps retrying it."
                ).format(count=len(deferred)),
            )

    @admin.action(description=_("🔍 Ping Google Search Indexing for selected events"))
    def ping_google_indexing(self, request, queryset):
        """Send immediate Googlebot indexing notification for selected events across all languages."""
        from crush_lu.services.google_indexing import notify_events_indexing

        events = list(queryset)
        res = notify_events_indexing(events, action=None, max_budget_seconds=15.0)

        successful_urls = res["success_count"]
        total_urls = res["total_expected"]
        deferred = res["deferred_count"]

        if successful_urls > 0:
            if successful_urls == total_urls:
                django_messages.success(
                    request,
                    _(
                        "Google Indexing: all {count} URL(s) successfully notified across {events} event(s)."
                    ).format(count=successful_urls, events=len(events)),
                )
            else:
                django_messages.warning(
                    request,
                    _(
                        "Google Indexing: {success}/{total} URL(s) notified across {events} event(s)"
                        "{deferred_msg}."
                    ).format(
                        success=successful_urls,
                        total=total_urls,
                        events=len(events),
                        deferred_msg=(
                            f" ({deferred} deferred due to time limit)"
                            if deferred
                            else ""
                        ),
                    ),
                )
        else:
            django_messages.warning(
                request,
                _(
                    "No Google Indexing pings were sent. Check that GOOGLE_INDEXING_ENABLED=True "
                    "and service account credentials are configured."
                ),
            )

    @staticmethod
    def _google_holders_for_cancellation(events):
        """Google holders whose card this cancellation actually changes.

        Two ways in, and the second exists because the first goes blind on a
        finished event:

          * the holder's card is currently SHOWING one of the selected events.
            A holder with an earlier eligible registration is looking at that
            one instead, so cancelling this event would rebuild a
            byte-identical object — and the fan-out is capped with no
            Google-side poll behind it, so each no-op can push somebody whose
            card IS wrong out of the batch and strand it.
          * the holder has a live seat on a selected event that has already
            ENDED. get_next_event_for_pass stopped selecting it when it
            finished, so it is nobody's displayed event and the first test says
            "nobody" — but the Google object was last written while the event
            was still upcoming and can still be advertising it, with nothing
            else in the estate recomputing that.

        The ended branch is scoped to that event's OWN attendees rather than
        switching the filter off selection-wide: one ended event, even with no
        registrations at all, must not disarm the narrowing for holders of the
        other selected events.

        ALREADY-cancelled events are excluded from both branches. This action
        changes nothing for them, and letting them into the ended branch would
        queue every one of their attendees — re-entering, through the fallback,
        the same cap starvation the narrowing exists to prevent.
        """
        from crush_lu.models import CrushProfile
        from crush_lu.wallet_pass import (
            PASS_NEXT_EVENT_STATUSES,
            get_next_event_registrations,
        )

        now = timezone.now()
        transitioning = [event for event in events if not event.is_cancelled]
        if not transitioning:
            return []

        ended_ids = {e.pk for e in transitioning if e.end_time < now}
        live_ids = {e.pk for e in transitioning} - ended_ids

        candidates = list(
            CrushProfile.objects.filter(
                # Both conditions in ONE filter() so they constrain the SAME
                # registration row — split across two calls they would match
                # any registration for the event plus any live registration
                # anywhere, which is every attendee.
                user__eventregistration__event__in=transitioning,
                user__eventregistration__status__in=PASS_NEXT_EVENT_STATUSES,
            )
            .exclude(google_wallet_object_id="")
            .distinct()
        )
        if not candidates:
            return []

        # user -> which of the selected events they actually hold a live seat
        # for. One query, so the per-holder branch below stays free.
        held = {}
        if ended_ids:
            for user_id, event_id in EventRegistration.objects.filter(
                event_id__in=ended_ids, status__in=PASS_NEXT_EVENT_STATUSES
            ).values_list("user_id", "event_id"):
                held.setdefault(user_id, set()).add(event_id)

        displayed = (
            get_next_event_registrations(
                [profile.user_id for profile in candidates], now=now
            )
            if live_ids
            else {}
        )
        return [
            profile
            for profile in candidates
            if held.get(profile.user_id)
            or getattr(displayed.get(profile.user_id), "event_id", None) in live_ids
        ]

    @admin.action(description=_("🚫 Cancel selected events"))
    def cancel_events(self, request, queryset):
        # Snapshot before .update() — the queryset is lazy and its filter may
        # well be is_cancelled=False, which would match nothing afterwards.
        events = list(queryset)
        event_ids = [event.pk for event in events]

        # The holder selection (see _google_holders_for_cancellation) has to be
        # resolved BEFORE the update: get_next_event_for_pass excludes
        # cancelled events, so afterwards none of these is anybody's displayed
        # next event and the answer would be "nobody".
        #
        # Snapshot and update under ONE lock on the selected events, taken on
        # the same rows the registration path locks (views_events, which does
        # MeetupEvent.objects.select_for_update() before admitting a seat).
        # Without it a signup can commit between the two: its own receiver
        # PATCHes the member object while the event is still live, so the
        # object names the event — but that holder is not in the snapshot, and
        # the cancellation that follows has no path left to take it back off
        # their card. Google never polls, so that would be permanent.
        google_profiles = []
        cancellation_cycle_started_at = timezone.now()
        with transaction.atomic():
            # Use the rows the lock returned, not the ones read before it.
            # Between `list(queryset)` and this line another admin can change
            # is_cancelled, and the selection below turns on exactly that
            # field: an event read as cancelled but restored since would be
            # dropped from `transitioning`, re-cancelled here anyway, and its
            # holders — whose cards the restore may just have PATCHed to show
            # it — would get no refresh at all.
            locked = list(
                MeetupEvent.objects.select_for_update().filter(pk__in=event_ids)
            )
            transitioning_ids = [event.pk for event in locked if not event.is_cancelled]
            try:
                google_profiles = self._google_holders_for_cancellation(locked)
            except Exception:
                logger.exception(
                    "Failed selecting Google wallet holders for %s "
                    "cancelled event(s)",
                    len(events),
                )
            # By pk rather than the caller's queryset: `events` is already
            # materialised, and the incoming filter is typically
            # is_cancelled=False, which matches nothing once this lands.
            updated = MeetupEvent.objects.filter(pk__in=event_ids).update(
                is_cancelled=True
            )
            if transitioning_ids:
                MeetupEvent.objects.filter(pk__in=transitioning_ids).update(
                    organiser_cancellation_started_at=cancellation_cycle_started_at
                )

        # Same reason as the wallet refreshes below: .update() emits no signals,
        # so nothing tells echo.lu either. A cancelled event left on the
        # national portal keeps advertising itself to the whole country, which
        # is the most visible way this action can go wrong.
        _enqueue_echo_sync(event_ids)

        # Real-time Google Indexing refresh for cancelled events. Deliberately a
        # refresh and not a removal: a published cancelled event still serves 200
        # with EventCancelled structured data (views_events.event_detail), so it
        # has to be re-crawled, not declared gone. _notify_google_indexing skips
        # the ones that really are unreachable (unpublished / invitation-only).
        _notify_google_indexing(event_ids)

        # .update() emits no signals, so nothing else tells Apple Wallet these
        # tickets are dead. Without this, a cancelled event's installed passes
        # keep rendering as valid at the door (the rebuild sets `voided`, but
        # only once Wallet is told to fetch it), and member cards keep showing
        # the cancelled event as the holder's next one.
        #
        # ONE call for the whole action, deliberately. Each refresh_* helper
        # starts its own push budget, so refreshing per event — twice per event,
        # tickets then member passes — restarted the deadline 2N times and let
        # total request time grow with the size of the selection. Batching
        # shares a single budget across everything the action touches.
        try:
            from crush_lu.models import CrushProfile
            from crush_lu.wallet.passkit_service import refresh_ticket_serials

            serials = list(
                EventRegistration.objects.filter(event__in=events)
                .exclude(apple_wallet_ticket_serial="")
                .values_list("apple_wallet_ticket_serial", flat=True)
            )
            serials += list(
                CrushProfile.objects.filter(user__eventregistration__event__in=events)
                .exclude(apple_pass_serial="")
                .values_list("apple_pass_serial", flat=True)
                .distinct()
            )
            refresh_ticket_serials(
                serials, context=f"Admin cancel_events ({len(events)} event(s))"
            )
        except Exception:
            logger.exception(
                "Failed scheduling Apple wallet refresh for %s cancelled event(s)",
                len(events),
            )

        # The GOOGLE member object prints the same "Next Event" block, and
        # get_next_event_for_pass drops cancelled events outright — so these
        # holders' cards are now advertising an event that will not happen.
        # There is no Google ticket to carry the change instead; the member
        # object is the only Google surface this event appears on.
        #
        # Its own try/except, and ONE call for the whole action, for the same
        # two reasons as the Apple half above: independent APIs must not skip
        # each other, and each helper opens its own budget.
        if google_profiles:
            try:
                from crush_lu.wallet.google_api import refresh_google_wallet_objects

                refresh_google_wallet_objects(
                    google_profiles,
                    context=f"Admin cancel_events ({len(events)} event(s))",
                )
            except Exception:
                logger.exception(
                    "Failed scheduling Google wallet refresh for %s cancelled event(s)",
                    len(events),
                )

        django_messages.success(
            request, _("Cancelled {count} event(s)").format(count=updated)
        )

        # Cancellation is one operation: stop the event, compensate every paid
        # registration, then tell each affected member what was issued and how
        # to request cash when a SumUp capture exists. Free events naturally
        # produce an empty issue list.
        from crush_lu.email_helpers import send_event_cancelled_by_organiser
        from crush_lu.services.credits import (
            credit_paid_registrations_for_cancelled_event,
        )

        credited = 0
        failed_event_ids = set()
        for event in events:
            try:
                issued = credit_paid_registrations_for_cancelled_event(event)
                credited += len(
                    {
                        credit.source_registration_id
                        for credit in issued
                        if credit.source_registration_id
                    }
                )
            except Exception:
                failed_event_ids.add(event.pk)
                logger.exception(
                    "Failed issuing cancellation credit for event %s", event.pk
                )
                django_messages.error(
                    request,
                    _(
                        "“{title}” was cancelled but its credit could not be "
                        "issued. Issue it by hand — the members have not been "
                        "compensated."
                    ).format(title=event.title),
                )

        # Production executes Django tasks inline, so email fan-out cannot be
        # moved to `.enqueue()` and still leave this request. Send a bounded
        # batch and use the per-registration marker as a durable cursor. The
        # action is already idempotent, so a coach can rerun it until the queue
        # is empty without issuing a second credit.
        from crush_lu.models.credits import CrushCredit

        cancellation_credits = CrushCredit.objects.filter(
            reason=CrushCredit.Reason.EVENT_CANCELLED,
        ).order_by("issued_at", "pk")
        email_limit = max(
            1, getattr(settings, "CRUSH_CREDIT_CANCELLATION_EMAIL_LIMIT", 50)
        )
        # "applied" belongs here even though it holds no seat and is owed no
        # credit: a curated applicant is waiting on a selection decision that
        # a cancelled event will never deliver. Leaving them out means the one
        # group still expecting to hear from us hears nothing at all.
        affected_registration = Q(
            status__in=("applied", "pending", "confirmed", "waitlist", "attended")
        ) | Q(issued_credits__reason=CrushCredit.Reason.EVENT_CANCELLED)
        candidates = list(
            EventRegistration.objects.filter(
                event_id__in=event_ids,
                organiser_cancellation_notified_at__isnull=True,
            )
            .exclude(event_id__in=failed_event_ids)
            .filter(affected_registration)
            .select_related("event", "user")
            .prefetch_related(
                Prefetch(
                    "issued_credits",
                    queryset=cancellation_credits,
                    to_attr="organiser_cancellation_credits",
                )
            )
            .distinct()
            .order_by("pk")[:email_limit]
        )
        emailed = 0
        for registration in candidates:
            # A fully redeemed cancellation award must still receive its
            # delayed notice. Select the newest payment cycle rather than
            # filtering on mutable credit status; registration rows can be
            # reused and therefore carry older cancellation awards too.
            all_credits = registration.organiser_cancellation_credits
            cycle_started_at = registration.event.organiser_cancellation_started_at
            if cycle_started_at:
                all_credits = [
                    credit
                    for credit in all_credits
                    if credit.issued_at >= cycle_started_at
                ]
            if all_credits:
                latest_credit = all_credits[-1]
                if latest_credit.source_payment_id:
                    current_credits = [
                        credit
                        for credit in all_credits
                        if credit.source_payment_id == latest_credit.source_payment_id
                    ]
                else:
                    current_credits = [latest_credit]
            else:
                current_credits = []
            try:
                emailed += bool(
                    send_event_cancelled_by_organiser(
                        registration,
                        current_credits,
                        request,
                    )
                )
            except Exception:
                logger.exception(
                    "Failed sending organiser cancellation email for registration %s",
                    registration.pk,
                )
                django_messages.error(
                    request,
                    _(
                        "Credit was issued to {email}, but the cancellation "
                        "email failed. Rerun this action to retry."
                    ).format(email=registration.user.email),
                )

        remaining_emails = (
            EventRegistration.objects.filter(
                event_id__in=event_ids,
                organiser_cancellation_notified_at__isnull=True,
            )
            .exclude(event_id__in=failed_event_ids)
            .filter(affected_registration)
            .distinct()
            .count()
        )
        if remaining_emails:
            django_messages.warning(
                request,
                _(
                    "Sent {sent} cancellation email(s); {remaining} remain. "
                    "Rerun this action to continue."
                ).format(sent=emailed, remaining=remaining_emails),
            )

        django_messages.success(
            request,
            _(
                "Issued Crush Credit to {count} paid registration(s). Card "
                "payments remain eligible for a cash refund on request."
            ).format(count=credited),
        )

    @admin.action(description=_("💳 Cancel selected events AND credit paid seats"))
    def cancel_events_and_credit(self, request, queryset):
        """Backward-compatible alias; the canonical action now always credits."""
        return self.cancel_events(request, queryset)

    @admin.action(description=_("🔔 Send reminders to confirmed attendees"))
    def send_event_reminders(self, request, queryset):
        """Send push/email reminders to all confirmed registrations for selected events."""
        from crush_lu.notification_service import notify_event_reminder
        from django.utils import timezone

        total_sent = 0
        total_failed = 0

        for event in queryset:
            if event.is_cancelled:
                continue

            if event.date_time:
                days_until = (event.date_time.date() - timezone.now().date()).days
            else:
                days_until = 1

            # Seat-holding: an unpaid cash-at-the-door attendee still needs the
            # reminder (see send_event_reminders for the same reasoning).
            registrations = event.eventregistration_set.filter(
                status__in=SEAT_HOLDING_STATUSES
            )

            for registration in registrations:
                try:
                    result = notify_event_reminder(
                        user=registration.user,
                        registration=registration,
                        event=event,
                        days_until=days_until,
                        request=request,
                    )
                    if result.any_delivered:
                        total_sent += 1
                except Exception:
                    total_failed += 1

        if total_sent > 0:
            django_messages.success(
                request,
                _("Sent {count} reminder(s) successfully").format(count=total_sent),
            )
        if total_failed > 0:
            django_messages.warning(
                request,
                _("Failed to send {count} reminder(s)").format(count=total_failed),
            )
        if total_sent == 0 and total_failed == 0:
            django_messages.info(request, _("No confirmed registrations to notify"))

    @admin.action(description=_("📋 Export attendees to CSV"))
    def export_attendees_csv(self, request, queryset):
        """Export confirmed attendees for selected events to CSV"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="event_attendees_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow(
            [
                "Event",
                "Event Date",
                "Name",
                "Email",
                "Phone",
                "Status",
                "Payment Confirmed",
                "Dietary Restrictions",
                "Bringing Guest",
                "Guest Name",
                "Registered At",
            ]
        )

        for event in queryset:
            # The venue list must match who will actually walk in. A pending
            # seat holder is expected at the door, so omitting them dropped
            # their dietary, accessibility, guest and contact details from the
            # operational export.
            registrations = event.eventregistration_set.filter(
                status__in=SEAT_HOLDING_STATUSES
            ).select_related("user__crushprofile")

            for reg in registrations:
                user = reg.user
                profile = getattr(user, "crushprofile", None)
                phone = profile.phone_number if profile else ""

                writer.writerow(
                    [
                        event.title,
                        (
                            event.date_time.strftime("%Y-%m-%d %H:%M")
                            if event.date_time
                            else ""
                        ),
                        user.get_full_name() or user.username,
                        user.email,
                        phone,
                        reg.get_status_display(),
                        "Yes" if reg.payment_confirmed else "No",
                        reg.dietary_restrictions or "",
                        "Yes" if reg.bringing_guest else "No",
                        reg.guest_name or "",
                        (
                            reg.registered_at.strftime("%Y-%m-%d %H:%M")
                            if reg.registered_at
                            else ""
                        ),
                    ]
                )

        total_events = queryset.count()
        django_messages.success(
            request,
            _("Exported attendees from %(count)d event(s) to CSV.")
            % {"count": total_events},
        )
        return response

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Customize form fields for better UX"""
        if db_field.name == "languages":
            # `forms` is imported at module level; a local re-import here would
            # make the name local to this whole method and break every other
            # branch that uses it.
            from crush_lu.models import CrushProfile

            kwargs["widget"] = forms.CheckboxSelectMultiple(
                choices=CrushProfile.EVENT_LANGUAGE_CHOICES
            )
            kwargs["help_text"] = _(
                "Select the languages this event will be conducted in. "
                "Leave all unchecked for no language restriction (any user can register)."
            )
            # Return a MultipleChoiceField that stores as JSON list
            return forms.TypedMultipleChoiceField(
                choices=CrushProfile.EVENT_LANGUAGE_CHOICES,
                widget=forms.CheckboxSelectMultiple,
                required=False,
                help_text=kwargs["help_text"],
                coerce=str,
            )
        elif db_field.name == "image":
            kwargs["help_text"] = _(
                "📸 Event banner image (optional)\n"
                "• Recommended size: 1200×630 pixels (1.9:1 aspect ratio)\n"
                "• Format: JPG or PNG\n"
                "• File size: Under 500KB for best performance\n"
                "• This size works great for social media sharing (Open Graph) and displays perfectly on all devices\n"
                "• The image will be cropped to fit: centered on mobile, full width on desktop"
            )
        elif db_field.name in ADDRESS_WIDGET_ATTRS:
            kwargs["widget"] = forms.TextInput(
                attrs=ADDRESS_WIDGET_ATTRS[db_field.name]
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def save_formset(self, request, form, formset, change):
        """
        Don't let a re-submitted registration row take the whole save down.

        `EventRegistration` is unique on (event, user) and the formset does
        check that before saving — but only against the table as it stood when
        the form was validated. Two overlapping submissions of the same change
        form therefore both pass validation, both INSERT, and the loser gets

            IntegrityError: duplicate key value violates unique constraint
            "crush_lu_eventregistration_event_id_user_id_52c32bb9_uniq"

        rendered as a 500. `changeform_view` wraps the POST in a transaction,
        so that rollback also discards the parent event's edits and every other
        inline in the same submission — for a row the admin's own twin request
        had already written (staging 2026-08-11, event 29 / user 86). The
        registration exists either way, so the duplicate is dropped and named
        in a warning instead of costing the rest of the save.

        Only that exact collision is absorbed. Each row goes in under its own
        savepoint and anything that is not an already-registered (event, user)
        pair is re-raised untouched. A collision whose winner does NOT match
        what was submitted is still absorbed — a 500 helps nobody — but it is
        reported field by field rather than as a plain duplicate, because
        there the admin's values really were dropped.
        """
        if formset.model is not EventRegistration:
            super().save_formset(request, form, formset, change)
            return

        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()

        # The form each row came from, so a collision is reported against
        # exactly the fields the admin submitted and no others.
        forms_by_instance = {
            id(saved.instance): saved for saved in getattr(formset, "saved_forms", [])
        }

        skipped = []
        for obj in instances:
            try:
                # A savepoint, not a nested transaction: the admin's atomic
                # block is already open, and an IntegrityError that escapes
                # into it poisons it — Postgres rejects every later statement
                # in an aborted transaction, including the lookup below.
                with transaction.atomic():
                    obj.save()
            except IntegrityError:
                already_registered = EventRegistration.objects.filter(
                    event_id=obj.event_id, user_id=obj.user_id
                )
                if obj.pk is not None:
                    already_registered = already_registered.exclude(pk=obj.pk)
                winner = already_registered.first()
                if winner is None:
                    raise
                skipped.append(
                    (
                        obj,
                        self._registration_conflicts(
                            forms_by_instance.get(id(obj)), obj, winner
                        ),
                    )
                )

        formset.save_m2m()

        if not skipped:
            return

        # `construct_change_message()` runs right after this and reads these
        # two collections straight into the admin LogEntry. A row the savepoint
        # rolled back must not show up in the event's history as added or
        # changed — the history is the record of what was persisted.
        dropped = {id(obj) for obj, _ in skipped}
        formset.new_objects = [
            obj for obj in formset.new_objects if id(obj) not in dropped
        ]
        formset.changed_objects = [
            (obj, fields)
            for obj, fields in formset.changed_objects
            if id(obj) not in dropped
        ]

        self._report_skipped_registrations(request, form, skipped)

    @staticmethod
    def _registration_conflicts(saved_form, submitted, winner):
        """Submitted values the row that won the race does not agree with.

        A twin submission of the same form agrees on every field, so this comes
        back empty and dropping the row costs nothing. A row written by someone
        else inside the same window need not agree — a member self-registering
        as `waitlist` while an admin adds them as `confirmed` with payment
        recorded leaves those two values unapplied — and reporting that as a
        plain duplicate would claim a save that did not happen.
        """
        if saved_form is None:
            return []

        conflicts = []
        for name in saved_form.fields:
            # `id` and `event` identify the row rather than describe it, and
            # `user` is equal by construction: it is half the unique key.
            if name in {"id", "event", "user"}:
                continue
            try:
                field = EventRegistration._meta.get_field(name)
            except FieldDoesNotExist:
                continue  # the formset's own DELETE checkbox, etc.
            if not field.concrete:
                continue
            mine = getattr(submitted, field.attname, None)
            theirs = getattr(winner, field.attname, None)
            if mine != theirs:
                conflicts.append((field.verbose_name, mine, theirs))
        return conflicts

    def _report_skipped_registrations(self, request, form, skipped):
        """Tell the admin which rows were dropped, and whether anything was lost."""

        def label(obj):
            return obj.user.get_full_name() or obj.user.username

        duplicates = [obj for obj, conflicts in skipped if not conflicts]
        conflicted = [(obj, conflicts) for obj, conflicts in skipped if conflicts]

        if duplicates:
            names = ", ".join(label(obj) for obj in duplicates)
            logger.warning(
                "Dropped %d duplicate registration row(s) on event %s (users: %s) "
                "— the change form was submitted more than once.",
                len(duplicates),
                form.instance.pk,
                names,
            )
            self.message_user(
                request,
                _(
                    "Already registered for this event, so no duplicate was "
                    "created: %(users)s."
                )
                % {"users": names},
                level=django_messages.WARNING,
            )

        for obj, conflicts in conflicted:
            details = "; ".join(
                _("%(field)s — you sent %(mine)s, the saved one has %(theirs)s")
                % {"field": verbose_name, "mine": mine, "theirs": theirs}
                for verbose_name, mine, theirs in conflicts
            )
            logger.warning(
                "Kept an existing registration for user %s on event %s over a "
                "concurrent admin submission; unapplied: %s",
                obj.user_id,
                form.instance.pk,
                details,
            )
            self.message_user(
                request,
                _(
                    "%(user)s was registered for this event by another request "
                    "while you were editing, so your row was not applied "
                    "(%(details)s). Open that registration to apply your values."
                )
                % {"user": label(obj), "details": details},
                level=django_messages.WARNING,
            )


class EventRegistrationAdmin(admin.ModelAdmin):
    form = EventRegistrationAdminForm
    list_display = (
        "get_user_display",
        "event",
        "status",
        "payment_confirmed",
        "registered_at",
    )
    list_per_page = 50
    list_select_related = ["user", "event"]
    list_filter = ("status", "payment_confirmed", "registered_at")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__email",
        "event__title",
    )
    autocomplete_fields = ["user", "event"]
    readonly_fields = ("registered_at", "updated_at", "cancelled_at")
    # Quick inline editing for registration management
    list_editable = ("status", "payment_confirmed")
    actions = [
        "export_registrations_csv",
        "confirm_registrations",
        "resend_curated_selection_notifications",
        "move_to_waitlist",
    ]
    fieldsets = (
        ("Registration Details", {"fields": ("event", "user", "status")}),
        (
            "Additional Information",
            {
                "fields": (
                    "dietary_restrictions",
                    "bringing_guest",
                    "guest_name",
                )
            },
        ),
        ("Payment", {"fields": ("payment_confirmed", "payment_date")}),
        (
            "Timestamps",
            {"fields": ("registered_at", "cancelled_at", "updated_at")},
        ),
    )

    def save_model(self, request, obj, form, change):
        # Stamp payment_date when staff flips payment_confirmed — in the change
        # form OR via the changelist's list_editable checkbox, which also routes
        # through save_model. Without this the weekly "paid event registrations"
        # KPI, which windows on payment_date, silently drops confirmed-but-undated
        # rows (mirrors the CrushConnect / PremiumMembership admins).
        promoted = False
        locked_event = None
        form_initial = getattr(form, "initial", {})
        persisted_initial = {}
        if obj.pk:
            persisted_initial = (
                EventRegistration.objects.filter(pk=obj.pk)
                .values("status", "payment_confirmed")
                .first()
                or {}
            )
        initial_status = form_initial.get(
            "status", persisted_initial.get("status", obj.status)
        )
        initial_payment_confirmed = form_initial.get(
            "payment_confirmed",
            persisted_initial.get("payment_confirmed", obj.payment_confirmed),
        )
        payment_changed = "payment_confirmed" in form.changed_data
        if payment_changed and "status" in form.changed_data:
            self.message_user(
                request,
                _(
                    "NOT saved: record payment separately from an attendance "
                    "status change. Reload this registration first so a stale "
                    "admin page cannot undo check-in or cancellation."
                ),
                level=django_messages.ERROR,
            )
            return
        # ``applied`` and ``waitlist`` have never held a seat, so recording a
        # new payment there would bypass selection. ``cancelled``/``no_show``
        # are different: staff may be entering cash that was genuinely taken
        # before the later exit, and that money must reach the immutable ledger
        # so the existing cancellation/no-show policy can reconcile it.
        if (
            payment_changed
            and obj.payment_confirmed
            and obj.status
            in {
                "applied",
                "waitlist",
            }
        ):
            self.message_user(
                request,
                _(
                    "NOT saved: payment cannot be recorded for a registration "
                    "that has not been selected for a seat. Select the "
                    "registration through its ordinary workflow first."
                ),
                level=django_messages.ERROR,
            )
            return
        confirmed_payment_date = obj.payment_date
        if payment_changed:
            if obj.payment_confirmed:
                obj.payment_date = obj.payment_date or timezone.now()
                # Money recorded out of band (bank transfer, cash) has to make
                # the same pending -> confirmed transition the SumUp return
                # handler makes. Without it the attendee stays labelled "Pending
                # Payment" forever: no confirmation email, and excluded from
                # every confirmed-only reminder and report even though they paid.
                # Only "pending" is promoted -- never a cancelled or waitlisted
                # row, where staff ticking a box must not conjure a seat.
                # Promote only "pending" -- staff ticking the box on a
                # cancelled or waitlisted row must not conjure a seat.
                if obj.status == "pending" and not obj.event.is_cancelled:
                    obj.status = "confirmed"
                # ...but notify for any seat holder. In the cash-at-the-door
                # flow the attendee is scanned to "attended" BEFORE staff record
                # the cash, so keying the email off the promotion alone left
                # exactly that case without the confirmation it was promised.
                if obj.status in SEAT_HOLDING_STATUSES and not obj.event.is_cancelled:
                    promoted = True
            else:
                obj.payment_date = None
        with transaction.atomic():
            # An unchecked manual payment is a correction to the cash ledger,
            # not merely to the mutable seat flag. Move those rows out of PAID
            # before touching registrations, preserving the global
            # PaymentTransaction -> EventRegistration lock order.
            if payment_changed and not obj.payment_confirmed and obj.pk:
                current_payment = (
                    PaymentTransaction.objects.select_for_update()
                    .filter(
                        event_registration=obj,
                        purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                        status=PaymentTransaction.Status.PAID,
                    )
                    .order_by("-paid_at", "-created_at", "-pk")
                    .first()
                )
                if current_payment and (
                    current_payment.provider != PaymentTransaction.Provider.MANUAL
                    or current_payment.paid_at != confirmed_payment_date
                ):
                    self.message_user(
                        request,
                        _(
                            "NOT saved: this confirmation is backed by a card "
                            "or Crush Credit payment (or by a different payment "
                            "cycle). Use the explicit refund/credit-void workflow "
                            "before clearing the paid marker."
                        ),
                        level=django_messages.ERROR,
                    )
                    return
                if (
                    current_payment
                    and current_payment.provider == PaymentTransaction.Provider.MANUAL
                    and current_payment.paid_at == confirmed_payment_date
                ):
                    # Older MANUAL captures belong to completed registration
                    # cycles and remain historical revenue. Reverse only the
                    # manual receipt whose immutable paid_at matches the
                    # mutable confirmation being unchecked; a newer SumUp or
                    # Credit capture means the old manual row is not this cycle.
                    current_payment.status = PaymentTransaction.Status.CANCELLED
                    current_payment.failure_reason = (
                        "Manual payment confirmation removed by staff."
                    )
                    current_payment.save(
                        update_fields=["status", "failure_reason", "updated_at"]
                    )

            # Confirming by hand does not close the member's still-open SumUp
            # widget, and refunds on this platform are manual-only: staff
            # records the cash, the open checkout then captures, and
            # _apply_paid_checkout's idempotency guard skips an
            # already-confirmed seat silently — the member has paid twice with
            # no log and no alert. So every PENDING checkout is deactivated at
            # SumUp BEFORE the MANUAL capture exists, in the same
            # PaymentTransaction-before-EventRegistration lock order as the
            # supersede loop in create_sumup_event_checkout.
            if payment_changed and obj.payment_confirmed and obj.pk:
                open_checkouts = list(
                    PaymentTransaction.objects.select_for_update()
                    .filter(
                        event_registration_id=obj.pk,
                        purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                        status=PaymentTransaction.Status.PENDING,
                    )
                    .exclude(sumup_checkout_id="")
                )
                all_closed = True
                if open_checkouts:
                    from crush_lu.services.sumup import SumUpClient

                    client = SumUpClient()
                    for open_tx in open_checkouts:
                        if client.deactivate_checkout(open_tx.sumup_checkout_id):
                            open_tx.status = PaymentTransaction.Status.CANCELLED
                            # Deliberately not translated: this field is read
                            # in the Coach Panel, which is forced to English.
                            open_tx.failure_reason = (
                                "Superseded by a manual payment confirmation — "
                                "staff recorded the money out of band (cash or "
                                "bank transfer), so this checkout was "
                                "deactivated at SumUp before any card was "
                                "charged."
                            )
                            open_tx.save(
                                update_fields=[
                                    "status",
                                    "failure_reason",
                                    "updated_at",
                                ]
                            )
                            logger.info(
                                "Deactivated SumUp checkout %s before manual "
                                "payment confirmation of registration %s",
                                open_tx.sumup_checkout_id,
                                obj.pk,
                            )
                        else:
                            # SumUp unreachable, or the checkout was just PAID
                            # and can no longer be cancelled. Leave the row
                            # PENDING — marking it terminal would hide a
                            # captured card payment from the webhook and the
                            # reconciliation sweep.
                            all_closed = False
                            logger.warning(
                                "Could not deactivate SumUp checkout %s for "
                                "registration %s — manual payment confirmation "
                                "refused",
                                open_tx.sumup_checkout_id,
                                obj.pk,
                            )
                if not all_closed:
                    # Refuse the whole save: a MANUAL capture beside a still
                    # payable card checkout IS the double charge. Returning
                    # before super().save_model() leaves the registration row
                    # untouched; checkouts deactivated above stay CANCELLED,
                    # which mirrors what is now true at SumUp.
                    self.message_user(
                        request,
                        _(
                            "NOT saved: this member still has an open SumUp "
                            "card checkout that could not be closed (SumUp "
                            "unreachable, or the card payment just went "
                            "through). Confirming by hand now could charge "
                            "them twice. Check the payment transactions for "
                            "this registration and try again."
                        ),
                        level=django_messages.ERROR,
                    )
                    return

                # The form's certification check is advisory: a selected
                # member can cancel while the admin page is open or while the
                # provider deactivations above are in flight. Re-lock and
                # revalidate the whole group after those calls, immediately
                # before recording cash. Payment rows are already locked, so
                # this keeps the callback's global order: payment -> event ->
                # registrations -> group state.
                locked_event = MeetupEvent.objects.select_for_update().get(
                    pk=obj.event_id
                )
                from crush_lu.services.curated_group_workflow import (
                    lock_event_group_payment_state,
                    registration_has_certified_payable_group,
                    requires_curated_group_certification,
                )

                if requires_curated_group_certification(locked_event):
                    locked_event, _groups = lock_event_group_payment_state(
                        locked_event.pk
                    )
                    locked_registration = EventRegistration.objects.get(pk=obj.pk)
                    if (
                        locked_registration.status != initial_status
                        or locked_registration.payment_confirmed
                        != initial_payment_confirmed
                    ):
                        self.message_user(
                            request,
                            _(
                                "NOT saved: this registration changed while the "
                                "payment was being recorded. Reload it and apply "
                                "the payment to the current attendance state."
                            ),
                            level=django_messages.ERROR,
                        )
                        return
                    if not registration_has_certified_payable_group(
                        locked_registration,
                        event=locked_event,
                    ):
                        self.message_user(
                            request,
                            _(
                                "NOT saved: this curated registration no longer "
                                "belongs to a complete certified payable group. "
                                "Reproject or compensate the degraded group first."
                            ),
                            level=django_messages.ERROR,
                        )
                        return

            lock_ids = [obj.pk] if obj.pk else []
            if obj.resale_source_registration_id:
                lock_ids.append(obj.resale_source_registration_id)
            if obj.pk:
                lock_ids.extend(
                    EventRegistration.objects.filter(
                        resale_source_registration_id=obj.pk
                    ).values_list("pk", flat=True)
                )
            locked_registrations = {
                registration.pk: registration
                for registration in EventRegistration.objects.select_for_update()
                .filter(pk__in=lock_ids)
                .order_by("pk")
            }

            locked_obj_snapshot = locked_registrations.get(obj.pk)
            if (
                payment_changed
                and locked_obj_snapshot is not None
                and (
                    locked_obj_snapshot.status != initial_status
                    or locked_obj_snapshot.payment_confirmed
                    != initial_payment_confirmed
                )
            ):
                self.message_user(
                    request,
                    _(
                        "NOT saved: this registration changed while the payment "
                        "was being recorded. Reload it and apply the payment to "
                        "the current attendance state."
                    ),
                    level=django_messages.ERROR,
                )
                return

            super().save_model(request, obj, form, change)

            if payment_changed and obj.payment_confirmed:
                # The registration row is reused after cancellation. Any old
                # PAID transaction therefore belongs to an earlier booking
                # cycle and cannot stand in for the cash/bank receipt staff is
                # recording now. Every false -> true transition gets its own
                # immutable MANUAL capture.
                payment = None
                payment_event = locked_event or obj.event
                if payment_event.registration_fee > 0:
                    payment = PaymentTransaction.objects.create(
                        transaction_reference=(
                            f"CRUSH-MANUAL-{obj.pk}-{uuid.uuid4().hex[:8]}"
                        ),
                        provider=PaymentTransaction.Provider.MANUAL,
                        amount=payment_event.registration_fee,
                        currency="EUR",
                        status=PaymentTransaction.Status.PAID,
                        purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                        user=obj.user,
                        event_registration=obj,
                        paid_at=obj.payment_date,
                        raw_response={
                            "recorded_by_admin_user_id": getattr(
                                getattr(request, "user", None), "pk", None
                            ),
                            "method": "cash_or_bank_transfer",
                        },
                    )

                locked_obj = EventRegistration.objects.select_related(
                    "event", "user", "resale_beneficiary"
                ).get(pk=obj.pk)
                previous_locked_obj = locked_registrations.get(locked_obj.pk)
                cancellation_signal_owns = bool(
                    previous_locked_obj is not None
                    and previous_locked_obj.status != "cancelled"
                    and locked_obj.status == "cancelled"
                )
                source = locked_registrations.get(
                    locked_obj.resale_source_registration_id
                )
                resale_replacements = [
                    registration
                    for registration in locked_registrations.values()
                    if registration.pk != locked_obj.pk
                    and registration.resale_source_registration_id == locked_obj.pk
                ]
                from crush_lu.services.credits import (
                    issue_cancellation_credits,
                    settle_pending_resale_credit,
                )

                resale_credits = settle_pending_resale_credit(
                    locked_obj, source_registration=source
                )
                if resale_credits and not locked_obj.event.is_cancelled:
                    from crush_lu.views_payments import (
                        _send_member_cancellation_safely,
                    )

                    transaction.on_commit(
                        partial(
                            _send_member_cancellation_safely,
                            source or locked_obj,
                            resale_credits,
                            recipient_user=resale_credits[0].user,
                        )
                    )
                if (
                    locked_obj.status == "cancelled"
                    and not locked_obj.event.is_cancelled
                    and payment is not None
                    # A status -> cancelled transition schedules the canonical
                    # post-save cancellation callback in signals.py. That
                    # callback sees this MANUAL capture after commit and owns
                    # promotion, compensation and the one member email. This
                    # admin branch is only for recording money on a row that
                    # was already cancelled, where no status signal fires.
                    and not cancellation_signal_owns
                ):
                    from crush_lu.views_payments import (
                        _send_member_cancellation_safely,
                    )

                    cancellation_credits = issue_cancellation_credits(
                        locked_obj, moment=locked_obj.cancelled_at
                    )
                    if cancellation_credits:
                        transaction.on_commit(
                            partial(
                                _send_member_cancellation_safely,
                                locked_obj,
                                cancellation_credits,
                            )
                        )
                    else:
                        settled_credits = []
                        for replacement in resale_replacements:
                            if replacement.resale_source_payment_id != payment.pk:
                                replacement.resale_source_payment = payment
                                replacement.save(
                                    update_fields=["resale_source_payment"]
                                )
                            settled_credits.extend(
                                settle_pending_resale_credit(
                                    replacement,
                                    source_registration=locked_obj,
                                )
                            )
                        transaction.on_commit(
                            partial(
                                _send_member_cancellation_safely,
                                locked_obj,
                                settled_credits,
                                awaiting_resale=not bool(settled_credits),
                            )
                        )
                if locked_obj.event.is_cancelled and payment is not None:
                    from crush_lu.services.credits import (
                        credit_registration_for_cancelled_event,
                    )
                    from crush_lu.views_payments import (
                        _send_organiser_cancellation_safely,
                    )

                    if resale_credits:
                        resale_email_registration = source or locked_obj
                        resale_recipient = (
                            None if source is not None else resale_credits[0].user
                        )
                        transaction.on_commit(
                            partial(
                                _send_organiser_cancellation_safely,
                                resale_email_registration,
                                resale_credits,
                                recipient_user=resale_recipient,
                                mark_notified=source is not None,
                            )
                        )
                    credits = credit_registration_for_cancelled_event(
                        locked_obj, payment=payment
                    )
                    if credits:
                        transaction.on_commit(
                            partial(
                                _send_organiser_cancellation_safely,
                                locked_obj,
                                credits,
                            )
                        )

        # The payment-pending email promises a confirmation "once payment is
        # received" — that promise has to hold for money recorded by hand, not
        # just for SumUp. Deferred to on_commit so a failed admin save sends
        # nothing, and wrapped so a mail error cannot break the admin page.
        if promoted:
            from django.db import transaction as _transaction

            from ..views_payments import _send_registration_confirmation_safely

            _transaction.on_commit(
                lambda r=obj: _send_registration_confirmation_safely(r)
            )

    def get_user_display(self, obj):
        full_name = obj.user.get_full_name()
        if full_name:
            return format_html(
                '{} <span style="color: #888; font-size: 11px;">({})</span>',
                full_name,
                obj.user.username,
            )
        return obj.user.username

    get_user_display.short_description = _("User")
    get_user_display.admin_order_field = "user__first_name"

    @admin.action(description=_("📋 Export selected registrations to CSV"))
    def export_registrations_csv(self, request, queryset):
        """Export selected registrations to CSV"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="registrations_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow(
            [
                "Event",
                "Event Date",
                "Name",
                "Email",
                "Phone",
                "Status",
                "Payment Confirmed",
                "Payment Date",
                "Dietary Restrictions",
                "Bringing Guest",
                "Guest Name",
                "Registered At",
            ]
        )

        for reg in queryset.select_related("user__crushprofile", "event"):
            user = reg.user
            profile = getattr(user, "crushprofile", None)
            phone = profile.phone_number if profile else ""

            writer.writerow(
                [
                    reg.event.title,
                    (
                        reg.event.date_time.strftime("%Y-%m-%d %H:%M")
                        if reg.event.date_time
                        else ""
                    ),
                    user.get_full_name() or user.username,
                    user.email,
                    phone,
                    reg.get_status_display(),
                    "Yes" if reg.payment_confirmed else "No",
                    reg.payment_date.strftime("%Y-%m-%d") if reg.payment_date else "",
                    reg.dietary_restrictions or "",
                    "Yes" if reg.bringing_guest else "No",
                    reg.guest_name or "",
                    (
                        reg.registered_at.strftime("%Y-%m-%d %H:%M")
                        if reg.registered_at
                        else ""
                    ),
                ]
            )

        django_messages.success(
            request, f"Exported {queryset.count()} registration(s) to CSV."
        )
        return response

    @admin.action(description=_("✅ Confirm selected registrations"))
    def confirm_registrations(self, request, queryset):
        """Confirm selected registrations, skipping anyone who doesn't meet age limits."""
        eligible_ids = []
        skipped = 0
        invalid_parallel_entries = []
        for reg in queryset.select_related("event", "user__crushprofile"):
            event = reg.event
            if (
                event.uses_curated_registration
                and event.group_size
                and reg.status != "applied"
            ):
                invalid_parallel_entries.append(reg.pk)
                continue
            if event.min_age > 18 or event.max_age < 99:
                profile = getattr(reg.user, "crushprofile", None)
                age = profile.age if profile else None
                if age is None or not (event.min_age <= age <= event.max_age):
                    skipped += 1
                    continue
            eligible_ids.append(reg.pk)
        if invalid_parallel_entries:
            django_messages.error(
                request,
                _(
                    "Configured parallel groups can only grant seats from "
                    "Applied through a complete provisional-group selection. "
                    "Nothing was changed."
                ),
            )
            return
        # Serials of seats being restored from ANY invalid status — their Apple
        # ticket is currently `voided` and has to be told it is live again.
        # Keyed off SEAT_HOLDING_STATUSES rather than "cancelled" alone, to
        # match what the payload actually voids: `waitlist` and `no_show` are
        # equally invalid, and confirming one of those would otherwise leave a
        # visibly voided ticket on a now-valid seat. Collected before the
        # update, while the old status is still readable.
        restoring = EventRegistration.objects.filter(pk__in=eligible_ids).exclude(
            status__in=SEAT_HOLDING_STATUSES
        )
        restored_serials = list(
            restoring.exclude(apple_wallet_ticket_serial="").values_list(
                "apple_wallet_ticket_serial", flat=True
            )
        )
        # ...and their MEMBER passes. Restoring a seat makes the event eligible
        # for get_next_event_for_pass again, so the card has to stop showing
        # the previous next event (or "No upcoming events"). A holder who never
        # downloaded the ticket has only this serial.
        from crush_lu.models import CrushProfile

        restored_serials += list(
            CrushProfile.objects.filter(user__eventregistration__in=restoring)
            .exclude(apple_pass_serial="")
            .values_list("apple_pass_serial", flat=True)
            .distinct()
        )
        # ...and the GOOGLE member objects, for exactly the reason above: a
        # restored seat is eligible for get_next_event_for_pass again, so the
        # card has to stop showing whatever it fell back to. Materialised HERE,
        # before the update, for the same reason as the serials — `restoring`
        # is a lazy queryset keyed on the old status, and the flip to
        # "confirmed" is about to make it match nothing.
        #
        # NARROWER than `restoring`, which also carries waitlist rows. Their
        # Apple TICKET is voided and does need un-voiding, but a waitlisted
        # registration is already inside PASS_NEXT_EVENT_STATUSES, so the
        # member card already names this event and the rebuild would be
        # byte-identical. Only a seat coming back from OUTSIDE that set
        # (cancelled, no_show) actually changes what the card says.
        # A second narrowing on top of that, the mirror of the one in
        # cancel_events: re-entering the candidate set only changes the card if
        # the restored event would then BE the displayed one. A holder with an
        # earlier eligible registration keeps looking at that, so restoring a
        # later seat rebuilds a byte-identical object and spends a capped,
        # poll-less budget slot that a genuinely-changed holder needed.
        #
        # `<=` on the comparison, not `<`: two events sharing a start time are
        # ordered by whatever the database returns, so a tie is "might change"
        # rather than "cannot".
        from crush_lu.wallet_pass import (
            PASS_NEXT_EVENT_STATUSES,
            get_next_event_registrations,
        )

        now = timezone.now()
        restored_rows = list(
            restoring.exclude(status__in=PASS_NEXT_EVENT_STATUSES).select_related(
                "event"
            )
        )
        restored_google_profiles = []
        if restored_rows:
            # Keyed on the FULL ordering key, not the start time alone. Once
            # _next_event_candidates gained its (date_time, event_id, id)
            # tie-break, "same start time" stopped meaning "might win": on a
            # tie the restored event takes the card only if it also sorts
            # first. Comparing dates alone queued every tied holder — no-ops
            # spending a cap whose overflow is permanent.
            def _key(row):
                return (row.event.date_time, row.event_id, row.pk)

            by_user = {}
            for row in restored_rows:
                event = row.event
                if event.is_cancelled or event.end_time < now:
                    continue  # restoring the seat still leaves it off the card
                best = by_user.get(row.user_id)
                if best is None or _key(row) < best:
                    by_user[row.user_id] = _key(row)
            displayed = get_next_event_registrations(list(by_user), now=now)
            candidates = CrushProfile.objects.filter(user_id__in=list(by_user)).exclude(
                google_wallet_object_id=""
            )
            for profile in candidates:
                showing = displayed.get(profile.user_id)
                if showing is None or by_user[profile.user_id] < _key(showing):
                    restored_google_profiles.append(profile)

        # Only rows arriving from *outside* the seat-holding set change what the
        # page publishes. pending/confirmed/attended -> confirmed leaves
        # remainingAttendeeCapacity and offer availability untouched, and the
        # pending -> confirmed workflow is the common one.
        confirmed_event_ids = list(
            EventRegistration.objects.filter(pk__in=eligible_ids)
            .exclude(status__in=SEAT_HOLDING_STATUSES)
            .values_list("event_id", flat=True)
            .distinct()
        )
        # Selecting an applicant on a PAID curated event owes them a payment
        # request, not a seat. Confirming outright would hand out a seat, a
        # door ticket, check-in eligibility and reminders before any money
        # arrived — `_admitted_status` draws exactly this line at signup, and
        # this is that same rule applied to the selection step.
        #
        # Scoped to "applied" on a paid event so every pre-existing path
        # through this action keeps its current behaviour byte-for-byte: a
        # cancelled or waitlisted row still confirms as it always has, paid or
        # not. Widening that is a separate decision, not this change's to make.
        # Selecting more applicants than there are places overbooks the event
        # outright — and on a curated event that is an ordinary slip, not an
        # edge case, because the applicant pool is *designed* to outnumber the
        # seats. Refuse the whole action rather than promote an arbitrary
        # subset: which applicants get in is the organiser's decision, and
        # silently letting the database's row order make it would be worse
        # than doing nothing.
        #
        # Scoped to applications, like the paid-event rule below. Confirming
        # cancelled or waitlisted rows over capacity has always been possible
        # here and organisers may rely on it to deliberately overbook; taking
        # that away is a separate decision.

        # Local import, as everywhere else in this module: views_events pulls
        # in the payment and wallet stacks at module scope, and admin.events is
        # imported during app registry population.
        from crush_lu.views_events import _admitted_status

        prelock_application_ids = set(
            EventRegistration.objects.filter(
                pk__in=eligible_ids, status="applied"
            ).values_list("pk", flat=True)
        )
        prelock_event_ids = sorted(
            set(
                EventRegistration.objects.filter(
                    pk__in=prelock_application_ids,
                    status="applied",
                ).values_list("event_id", flat=True)
            )
        )
        curated_notice_generations = {}
        selection_delivery = None

        # The capacity check and the status updates have to be one atomic,
        # locked unit. Reading spots_remaining and then updating in a separate
        # statement lets two admins selecting at the same time both see the
        # last seat free and both spend it — and the seat they overspend comes
        # with a door ticket and check-in eligibility.
        #
        # Gender pools are checked alongside the total: with caps enabled a
        # male applicant can pass a total-capacity test while the male pool is
        # already full, which is exactly the case event_register re-checks
        # under its own lock. Half a guard here would be worse than none,
        # because it reads as protection.
        with transaction.atomic():
            # The member-cancellation path takes the event lock before any
            # registration locks. Keep that global order here too or an admin
            # selection racing a cancellation can deadlock on PostgreSQL.
            locked_events = {
                event.pk: event
                for event in MeetupEvent.objects.select_for_update()
                .filter(pk__in=prelock_event_ids)
                .order_by("pk")
            }
            # Re-read the selected applications under the lock, not just the
            # event. Locking the event alone left the rows themselves stale:
            # two admins selecting the same paid application both materialise
            # it as "applied" before either writes, and the second — finding it
            # is no longer "applied" and so no longer needs payment —
            # would sweep it into confirm_ids and grant the seat WITHOUT
            # payment. Anything another admin (or the member, withdrawing) has
            # already moved out of "applied" is dropped from this run entirely,
            # so a concurrent decision is never silently overwritten.
            #
            # NO select_related() on this query. "user__crushprofile" is a
            # REVERSE one-to-one, so it joins as a LEFT OUTER JOIN, and
            # PostgreSQL refuses FOR UPDATE on the nullable side of an outer
            # join — the whole action would raise NotSupportedError before
            # updating anyone. _promote_from_waitlist carries the same warning
            # for the same reason. SQLite ignores select_for_update() entirely,
            # so the test suite cannot see this; profiles and events are read
            # separately below. "event" is dropped too: joining it here would
            # take event locks implicitly in registration order, after the
            # explicit sorted event-first lock above established the global
            # order shared with member cancellation.
            applications = list(
                EventRegistration.objects.select_for_update()
                .filter(
                    pk__in=prelock_application_ids,
                    event_id__in=locked_events,
                    status="applied",
                )
                .order_by("pk")
            )
            live_application_ids = {reg.pk for reg in applications}
            stale_application_ids = prelock_application_ids - live_application_ids

            profiles_by_user = {}
            if applications:
                # Separate query, for the outer-join reason above.
                profiles_by_user = {
                    profile.user_id: profile
                    for profile in CrushProfile.objects.filter(
                        user_id__in={reg.user_id for reg in applications}
                    )
                }

                by_event = {}
                for reg in applications:
                    by_event.setdefault(reg.event_id, []).append(reg)

                for event_id, regs in by_event.items():
                    event = locked_events[event_id]
                    # A configured parallel night is selected in complete
                    # groups, not against the raw venue ceiling. This matters
                    # both when the coach committed fewer than the maximum and
                    # when max_participants has a remainder smaller than one
                    # group. Direct events intentionally keep the same value.
                    remaining = event.selection_spots_remaining
                    if len(regs) > remaining:
                        django_messages.error(
                            request,
                            _(
                                "“%(title)s” has %(remaining)s place(s) left but "
                                "%(selected)s application(s) were selected. Nothing "
                                "was changed — deselect %(excess)s and try again."
                            )
                            % {
                                "title": event.title,
                                "remaining": remaining,
                                "selected": len(regs),
                                "excess": len(regs) - remaining,
                            },
                        )
                        return

                    # On a parallel curated night, a checkbox alone must not
                    # create a payment request. The applicant must belong to a
                    # schedule that passed the five-date guarantee and was
                    # explicitly promoted to PROVISIONAL. Legacy curated
                    # events without group_size retain the existing manual
                    # selection workflow.
                    if event.group_size:
                        registration_ids = {reg.pk for reg in regs}
                        active_groups = list(
                            CuratedEventGroup.objects.select_for_update()
                            .filter(event_id=event_id)
                            .filter(
                                status__in=(
                                    CuratedEventGroup.STATUS_PROVISIONAL,
                                    CuratedEventGroup.STATUS_LOCKED,
                                )
                            )
                            .order_by("pk")
                        )
                        current_generation = max(
                            (group.generation for group in active_groups),
                            default=None,
                        )
                        memberships = list(
                            CuratedEventGroupMembership.objects.select_for_update()
                            .select_related("group")
                            .filter(
                                event_id=event_id,
                                registration_id__in=registration_ids,
                                released_at__isnull=True,
                            )
                            .order_by("pk")
                        )
                        payable_ids = {
                            membership.registration_id
                            for membership in memberships
                            if membership.group.status
                            == CuratedEventGroup.STATUS_PROVISIONAL
                            and membership.group.generation == current_generation
                        }
                        unready = registration_ids - payable_ids
                        if unready:
                            django_messages.error(
                                request,
                                _(
                                    "“%(title)s”: %(count)d selected applicant(s) "
                                    "are not in a viable provisional group. Nothing "
                                    "was changed — approve the stored group schedule "
                                    "before inviting payment."
                                )
                                % {"title": event.title, "count": len(unready)},
                            )
                            return

                        touched_group_ids = {
                            membership.group_id for membership in memberships
                        }
                        generation_groups = list(
                            CuratedEventGroup.objects.select_for_update()
                            .filter(
                                event_id=event_id,
                                generation=current_generation,
                            )
                            .order_by("group_number", "pk")
                        )
                        touched_group_numbers = {
                            group.group_number
                            for group in generation_groups
                            if group.pk in touched_group_ids
                        }
                        highest_touched_number = max(touched_group_numbers)
                        prefix_groups = [
                            group
                            for group in generation_groups
                            if group.group_number <= highest_touched_number
                        ]
                        prefix_numbers = {group.group_number for group in prefix_groups}
                        unsatisfied_prefix = prefix_numbers != set(
                            range(1, highest_touched_number + 1)
                        )
                        for prefix_group in prefix_groups:
                            if prefix_group.status not in {
                                CuratedEventGroup.STATUS_PROVISIONAL,
                                CuratedEventGroup.STATUS_LOCKED,
                            }:
                                unsatisfied_prefix = True
                                break
                            if (
                                prefix_group.pk not in touched_group_ids
                                and prefix_group.memberships.filter(
                                    released_at__isnull=True,
                                    registration__status="applied",
                                ).exists()
                            ):
                                unsatisfied_prefix = True
                                break
                        if unsatisfied_prefix:
                            django_messages.error(
                                request,
                                _(
                                    "“%(title)s”: groups must be selected in "
                                    "fairness order with no skipped group number. "
                                    "Invite or resolve every earlier group first."
                                )
                                % {"title": event.title},
                            )
                            return
                        roster_memberships = list(
                            CuratedEventGroupMembership.objects.select_for_update()
                            .filter(
                                group_id__in=touched_group_ids,
                                released_at__isnull=True,
                            )
                            .order_by("pk")
                        )
                        still_applied_ids = set(
                            EventRegistration.objects.filter(
                                curated_group_memberships__in=roster_memberships,
                                status="applied",
                            ).values_list("pk", flat=True)
                        )
                        if registration_ids != still_applied_ids:
                            django_messages.error(
                                request,
                                _(
                                    "“%(title)s”: select every still-applied "
                                    "member of each touched provisional group in "
                                    "one action. Nothing was changed."
                                )
                                % {"title": event.title},
                            )
                            return
                        try:
                            for group in active_groups:
                                if group.pk in touched_group_ids:
                                    summary = group.schedule_viability(
                                        evaluate_preferences=False
                                    )
                                    if (
                                        summary["schedule_digest"]
                                        != group.schedule_digest
                                    ):
                                        raise ValidationError(
                                            _(
                                                "The roster or schedule changed "
                                                "after provisional approval."
                                            )
                                        )
                        except ValidationError as error:
                            django_messages.error(
                                request,
                                _(
                                    "“%(title)s”: the stored provisional schedule "
                                    "is no longer viable (%(reason)s). Nothing was "
                                    "changed."
                                )
                                % {"title": event.title, "reason": error},
                            )
                            return

                        for reg in regs:
                            curated_notice_generations[reg.pk] = current_generation

                    if not event.gender_limits_active:
                        continue
                    selected_per_pool = {}
                    for reg in regs:
                        profile = profiles_by_user.get(reg.user_id)
                        gender = getattr(profile, "gender", None)
                        pool = event.get_gender_pool(gender) if gender else None
                        if pool is None:
                            continue
                        selected_per_pool.setdefault(pool, []).append(gender)
                    for pool, genders in selected_per_pool.items():
                        gender = genders[0]
                        limit = event.get_gender_pool_limit(gender)
                        if limit is None:
                            continue
                        taken = event.get_confirmed_count_for_gender(gender)
                        if taken + len(genders) > limit:
                            django_messages.error(
                                request,
                                _(
                                    "“%(title)s”: the %(pool)s places are capped at "
                                    "%(limit)s with %(taken)s already taken, but "
                                    "%(selected)s application(s) in that group were "
                                    "selected. Nothing was changed."
                                )
                                % {
                                    "title": event.title,
                                    "pool": pool,
                                    "limit": limit,
                                    "taken": taken,
                                    "selected": len(genders),
                                },
                            )
                            return

            # Inside the same locked transaction as the checks above: releasing
            # the lock before writing would leave exactly the race the lock was
            # taken to close.
            #
            # Everything is derived from the LOCKED read. Deriving confirm_ids
            # from the pre-lock eligible_ids is what let a row another admin
            # had just moved to "pending" fall through to "confirmed".
            #
            # _admitted_status, not the fee alone. A member who paid, cancelled
            # late and then reapplied keeps payment_confirmed on the REUSED
            # row, and that helper exists precisely so such a row is admitted
            # as "confirmed" instead of being asked to pay a second time for
            # money we already hold — the UI would show payment due while
            # checkout rejected them as already paid.
            pending_application_ids = {
                reg.pk
                for reg in applications
                if _admitted_status(locked_events[reg.event_id], reg) == "pending"
            }
            confirm_ids = [
                pk
                for pk in eligible_ids
                if pk not in pending_application_ids and pk not in stale_application_ids
            ]

            updated = EventRegistration.objects.filter(pk__in=confirm_ids).update(
                status="confirmed",
                # QuerySet.update bypasses EventRegistration.save(). A restored
                # registration is a new cancellation-policy cycle and must not
                # retain the previous cancellation's timing classification.
                cancelled_at=None,
            )
            awaiting_payment = 0
            if pending_application_ids:
                # status="applied" in the filter as well as the lock: belt and
                # braces against acting on a row that moved under us.
                awaiting_payment = EventRegistration.objects.filter(
                    pk__in=pending_application_ids, status="applied"
                ).update(status="pending", cancelled_at=None)

            # Carry a released paid seat's resale claim onto the applicant now
            # taking it.
            #
            # When a paid attendee cancels late, their 50% share is owed once
            # somebody else takes and pays for the seat. On a direct event the
            # replacement picks the claim up automatically: waitlist promotion
            # and event_register both call _attach_unclaimed_resale_claim. A
            # curated event has neither -- no waitlist to promote from, and the
            # sign-up made an application that took no seat -- so the claim sat
            # on the cancelled row and the replacement arrived without it.
            # Settlement then found no resale_source_* links and issued
            # nothing, so the original member LOST THEIR SHARE PERMANENTLY even
            # after the replacement paid in full. Selection is the moment the
            # seat actually changes hands, so it is where the claim moves.
            #
            # AFTER the status writes above, which is what makes this safe.
            # _attach_unclaimed_resale_claim decides a claim is already taken by
            # looking for another row in SEAT_HOLDING_STATUSES carrying it.
            # Called before the updates, every applicant in the run would still
            # be "applied" -- seat-holding to nobody -- and each would be handed
            # the SAME claim. Afterwards they all hold seats, so the existing
            # check separates them with no bookkeeping of our own. Ordering the
            # calls by pk gives the oldest claim to the lowest-numbered
            # applicant, deterministically rather than by database whim.
            resale_claims_attached = 0
            if applications:
                from crush_lu.services.credits import settle_pending_resale_credit
                from crush_lu.views_events import _attach_unclaimed_resale_claim

                for reg in applications:
                    # Scoped to applications, like the paid-event rule above:
                    # a cancelled or waitlisted row confirmed through this
                    # action behaves exactly as it does today.
                    #
                    # No registration_fee test here. _resale_claim_from already
                    # weighs the fee against captured payments and returned
                    # credits, and a fee lowered to zero after someone paid
                    # still owes them; second-guessing it would drop that case.
                    if (
                        reg.resale_source_registration_id
                        or reg.resale_source_payment_id
                    ):
                        continue  # already carries one
                    if (
                        _attach_unclaimed_resale_claim(reg, locked_events[reg.event_id])
                        is None
                    ):
                        continue
                    resale_claims_attached += 1
                    # A returning applicant whose earlier payment still stands
                    # is admitted straight to "confirmed", so no later checkout
                    # will settle this for them. _promote_from_waitlist settles
                    # the same case at the same point.
                    if reg.payment_confirmed:
                        settle_pending_resale_credit(reg)

            # Curated invitations use a durable per-generation outbox. A
            # request timeout therefore leaves the exact unsent remainder
            # retryable instead of an unknowable partial email fan-out.
            curated_notice_ids = set(curated_notice_generations).intersection(
                live_application_ids
            )
            if curated_notice_ids:
                from crush_lu.services.curated_group_notifications import (
                    enqueue_selection_notifications,
                )

                enqueue_selection_notifications(
                    {
                        registration_id: curated_notice_generations[registration_id]
                        for registration_id in curated_notice_ids
                    }
                )

            # Legacy/direct event selection keeps its existing single-message
            # callback. The projector's potentially 500-person path never
            # enters this loop.
            for registration_id in sorted(live_application_ids - curated_notice_ids):
                transaction.on_commit(
                    partial(
                        _send_application_selection_notification_safely,
                        registration_id,
                    )
                )

        if curated_notice_ids:
            from crush_lu.services.curated_group_notifications import (
                deliver_curated_group_notifications,
            )

            selection_delivery = deliver_curated_group_notifications(
                registration_ids=curated_notice_ids,
                kinds=["selection"],
            )

        # .update() emits no signals, so the per-registration receiver never
        # runs here and the restored tickets would stay voided forever.
        if restored_serials:
            try:
                from crush_lu.wallet.passkit_service import refresh_ticket_serials

                refresh_ticket_serials(
                    restored_serials, context="Admin confirm_registrations"
                )
            except Exception:
                logger.exception(
                    "Failed scheduling Apple ticket refresh for %s restored "
                    "registration(s)",
                    len(restored_serials),
                )

        if restored_google_profiles:
            try:
                from crush_lu.wallet.google_api import refresh_google_wallet_objects

                refresh_google_wallet_objects(
                    restored_google_profiles,
                    context="Admin confirm_registrations",
                )
            except Exception:
                logger.exception(
                    "Failed scheduling Google wallet refresh for %s restored "
                    "registration(s)",
                    len(restored_google_profiles),
                )

        # .update() emits no signals here either, so the EventRegistration
        # receiver that keeps remainingAttendeeCapacity and offer availability
        # fresh in the event JSON-LD never runs for waitlist -> confirmed.
        _notify_google_indexing(confirmed_event_ids)

        django_messages.success(
            request, _("Confirmed %(count)s registration(s).") % {"count": updated}
        )
        if stale_application_ids:
            django_messages.warning(
                request,
                _(
                    "%(count)s selected application(s) had already been acted on "
                    "by someone else and were left untouched."
                )
                % {"count": len(stale_application_ids)},
            )
        if awaiting_payment:
            django_messages.info(
                request,
                _(
                    "%(count)s selected application(s) on paid events were set "
                    "to Pending Payment rather than Confirmed — their seat is "
                    "held until payment arrives."
                )
                % {"count": awaiting_payment},
            )
        if selection_delivery is not None:
            django_messages.info(
                request,
                _(
                    "Curated invitation delivery: %(sent)d sent, %(failed)d "
                    "failed and remain retryable, %(remaining)d still queued."
                )
                % {
                    "sent": selection_delivery.sent,
                    "failed": selection_delivery.failed,
                    "remaining": selection_delivery.remaining,
                },
            )
        if resale_claims_attached:
            # Worth saying out loud: it commits the organiser to paying someone
            # a 50% share out of this seat, and it is otherwise invisible.
            django_messages.info(
                request,
                _(
                    "%(count)s selected applicant(s) took a seat released by a "
                    "late cancellation. The member who cancelled earns their "
                    "50%% resale share once the new attendee has paid."
                )
                % {"count": resale_claims_attached},
            )
        if skipped:
            django_messages.warning(
                request,
                _(
                    "Skipped %(count)s registration(s) that do not meet the event's age requirements."
                )
                % {"count": skipped},
            )

    @admin.action(description=_("📨 Deliver pending curated emails"))
    def resend_curated_selection_notifications(self, request, queryset):
        """Resume exact unsent invitations/remedies in one bounded batch."""

        registration_ids = list(queryset.order_by("pk").values_list("pk", flat=True))
        selection_rows = (
            EventRegistration.objects.filter(
                pk__in=registration_ids,
                status__in=("pending", "confirmed"),
                curated_group_memberships__released_at__isnull=True,
                curated_group_memberships__group__status__in=(
                    CuratedEventGroup.STATUS_PROVISIONAL,
                    CuratedEventGroup.STATUS_LOCKED,
                ),
            )
            .order_by("pk", "-curated_group_memberships__group__generation")
            .values_list("pk", "curated_group_memberships__group__generation")
        )
        generation_by_registration = {}
        for registration_id, generation in selection_rows:
            generation_by_registration.setdefault(registration_id, generation)
        from crush_lu.services.curated_group_notifications import (
            deliver_curated_group_notifications,
            enqueue_selection_notifications,
        )

        enqueue_selection_notifications(generation_by_registration)
        delivery = deliver_curated_group_notifications(
            registration_ids=registration_ids,
        )
        django_messages.info(
            request,
            _(
                "Curated email batch: %(sent)d sent, %(failed)d failed and "
                "remain retryable, %(cancelled)d stale notice(s) closed, and "
                "%(remaining)d remain queued."
            )
            % {
                "sent": delivery.sent,
                "failed": delivery.failed,
                "cancelled": delivery.cancelled,
                "remaining": delivery.remaining,
            },
        )

    @admin.action(description=_("⏳ Move to waitlist"))
    def move_to_waitlist(self, request, queryset):
        """Move selected registrations to waitlist"""
        selected_ids = list(queryset.values_list("pk", flat=True))
        if not selected_ids:
            return
        with transaction.atomic():
            event_ids = sorted(
                set(
                    EventRegistration.objects.filter(pk__in=selected_ids)
                    .order_by()
                    .values_list("event_id", flat=True)
                )
            )
            list(
                MeetupEvent.objects.select_for_update()
                .filter(pk__in=event_ids)
                .order_by("pk")
            )
            locked_registrations = list(
                EventRegistration.objects.select_for_update()
                .filter(event_id__in=event_ids)
                .order_by("pk")
            )
            selected = [
                registration
                for registration in locked_registrations
                if registration.pk in selected_ids
            ]
            group_ids = list(
                CuratedEventGroupMembership.objects.filter(
                    registration_id__in=selected_ids,
                    released_at__isnull=True,
                ).values_list("group_id", flat=True)
            )
            groups = list(
                CuratedEventGroup.objects.select_for_update()
                .filter(pk__in=group_ids)
                .order_by("pk")
            )
            if any(group.status == CuratedEventGroup.STATUS_LOCKED for group in groups):
                self.message_user(
                    request,
                    _(
                        "No registrations were moved: at least one selected "
                        "attendee belongs to a locked final group."
                    ),
                    level=django_messages.ERROR,
                )
                return
            degraded_events = set()
            for group in groups:
                if group._mark_degraded_locked(
                    reason=CuratedEventGroup.DEGRADATION_REASON_STATUS_EXIT
                ):
                    degraded_events.add(group.event_id)

            # Serials losing their seat, captured while the locked rows still
            # carry their previous status. Waitlist is not seat-holding.
            voiding_serials = [
                registration.apple_wallet_ticket_serial
                for registration in selected
                if registration.status in SEAT_HOLDING_STATUSES
                and registration.apple_wallet_ticket_serial
            ]
            waitlisted_event_ids = sorted(
                {
                    registration.event_id
                    for registration in selected
                    if registration.status in SEAT_HOLDING_STATUSES
                }
            )
            updated = EventRegistration.objects.filter(pk__in=selected_ids).update(
                status="waitlist"
            )

        # Seat-holding -> waitlist frees a seat, and .update() emits no signals,
        # so the indexing receiver has to be driven by hand here too.
        _notify_google_indexing(waitlisted_event_ids)

        if voiding_serials:
            try:
                from crush_lu.wallet.passkit_service import refresh_ticket_serials

                refresh_ticket_serials(
                    voiding_serials, context="Admin move_to_waitlist"
                )
            except Exception:
                logger.exception(
                    "Failed scheduling Apple ticket refresh for %s waitlisted "
                    "registration(s)",
                    len(voiding_serials),
                )

        # No Google refresh here, unlike the other three bulk actions, and NOT
        # an oversight: this action moves a registration from one member of
        # PASS_NEXT_EVENT_STATUSES to another ("waitlist" is in the set), so it
        # cannot change which event the member card names — and the card prints
        # only that event's title and date, never the registration status. The
        # rebuild would be byte-identical, which is also why the Apple half
        # above refreshes the TICKET serials only and leaves member passes
        # alone. PATCHing anyway would spend a capped budget on no-ops and log a
        # false "left STALE" warning about passes that were never wrong.
        # Revisit if the payload ever starts printing next_event["status"] —
        # TestAdminBulkActionsRefreshWallets pins this.

        django_messages.success(
            request, f"Moved {updated} registration(s) to waitlist."
        )
        if degraded_events:
            from crush_lu.services.curated_group_workflow import (
                repair_degraded_event_groups,
            )

            for event_id in sorted(degraded_events):
                try:
                    remedy = repair_degraded_event_groups(event_id, actor=request.user)
                except Exception as exc:
                    logger.exception(
                        "Curated-group remedy failed after waitlist move for "
                        "event %s",
                        event_id,
                    )
                    django_messages.error(
                        request,
                        _(
                            "Event %(event)d remains non-payable because its "
                            "curated-group remedy failed: %(error)s"
                        )
                        % {"event": event_id, "error": type(exc).__name__},
                    )
                else:
                    django_messages.info(
                        request,
                        _("Event %(event)d curated-group remedy: %(action)s.")
                        % {"event": event_id, "action": remedy.action},
                    )


class EventInvitationAdmin(admin.ModelAdmin):
    """
    ✨ PRIVATE EVENT INVITATIONS - Manage VIP Guest Invitations

    Send and manage private invitations for exclusive events.
    Track invitation status, approvals, and guest account creation.
    """

    list_display = (
        "get_guest_name",
        "guest_email",
        "event",
        "status",
        "approval_status",
        "invitation_sent_at",
        "invited_by",
        "has_special_user",
        "get_invitation_link",
    )
    list_select_related = ["event", "invited_by", "special_user", "created_user"]
    list_filter = (
        "status",
        "approval_status",
        "invitation_sent_at",
        "event",
        "special_user",
    )
    search_fields = (
        "guest_email",
        "guest_first_name",
        "guest_last_name",
        "event__title",
        "invited_by__username",
        "special_user__first_name",
        "special_user__last_name",
    )
    readonly_fields = (
        "invitation_code",
        "invitation_sent_at",
        "accepted_at",
        "approved_at",
        "get_invitation_link",
        "get_status_display",
    )
    actions = ["approve_guests", "reject_guests", "resend_invitations"]

    fieldsets = (
        (
            "👤 Guest Information",
            {"fields": ("guest_first_name", "guest_last_name", "guest_email")},
        ),
        ("🎉 Event Details", {"fields": ("event", "invited_by")}),
        (
            "✨ Special User VIP Treatment",
            {
                "fields": ("special_user",),
                "classes": ("collapse",),
                "description": "Link this invitation to a Special User Experience for VIP treatment (auto-approval, custom journey, etc.)",
            },
        ),
        (
            "📧 Invitation Status",
            {
                "fields": (
                    "status",
                    "invitation_code",
                    "get_invitation_link",
                    "invitation_sent_at",
                    "accepted_at",
                ),
                "description": "Track invitation delivery and guest response",
            },
        ),
        (
            "✅ Approval Workflow",
            {
                "fields": ("approval_status", "approval_notes", "approved_at"),
                "description": "Coach approval for guests to attend the event",
            },
        ),
        (
            "👥 User Account",
            {
                "fields": ("created_user",),
                "description": "Linked user account (created when guest accepts invitation)",
            },
        ),
        (
            "📊 Status Overview",
            {
                "fields": ("get_status_display",),
                "classes": ("collapse",),
                "description": "Complete invitation lifecycle status",
            },
        ),
    )

    def get_guest_name(self, obj):
        """Display guest's full name"""
        return f"{obj.guest_first_name} {obj.guest_last_name}"

    get_guest_name.short_description = _("Guest Name")
    get_guest_name.admin_order_field = "guest_first_name"

    def has_special_user(self, obj):
        """Display if linked to Special User Experience"""
        return obj.special_user is not None

    has_special_user.boolean = True
    has_special_user.short_description = _("✨ VIP")

    def get_invitation_link(self, obj):
        """Display clickable invitation link"""
        if obj.invitation_code:
            url = f"https://crush.lu{reverse('crush_lu:invitation_landing', kwargs={'code': obj.invitation_code})}"
            return format_html(
                '<a href="{}" target="_blank" style="color: #9B59B6; font-weight: bold;">'
                "📧 View Invitation Page</a><br>"
                '<small style="color: #666; font-family: monospace;">{}</small>',
                url,
                url,
            )
        return "N/A"

    get_invitation_link.short_description = _("Invitation Link")

    def get_status_display(self, obj):
        """Display comprehensive status with visual indicators"""
        status_html = (
            '<div style="padding: 15px; background: #f8f9fa; border-radius: 8px;">'
        )

        status_colors = {
            "pending": "#ffc107",
            "accepted": "#0dcaf0",
            "declined": "#6c757d",
            "attended": "#28a745",
            "expired": "#dc3545",
        }
        status_color = status_colors.get(obj.status, "#6c757d")
        status_html += f'<p><strong>Invitation:</strong> <span style="color: {status_color}; font-weight: bold;">● {obj.get_status_display()}</span></p>'

        approval_colors = {
            "pending_approval": "#ffc107",
            "approved": "#28a745",
            "rejected": "#dc3545",
        }
        approval_color = approval_colors.get(obj.approval_status, "#6c757d")
        status_html += f'<p><strong>Approval:</strong> <span style="color: {approval_color}; font-weight: bold;">● {obj.get_approval_status_display()}</span></p>'

        if obj.is_expired:
            status_html += '<p style="color: #dc3545;"><strong>⚠️ EXPIRED</strong></p>'

        if obj.created_user:
            status_html += f"<p><strong>Account Created:</strong> ✅ {obj.created_user.username}</p>"
        else:
            status_html += "<p><strong>Account:</strong> ❌ Not yet created</p>"

        status_html += "</div>"
        return mark_safe(status_html)

    get_status_display.short_description = _("Complete Status")

    @admin.action(description=_("✅ Approve selected guests"))
    def approve_guests(self, request, queryset):
        """Approve guests to attend the event and send notification emails"""
        from django.utils import timezone
        from crush_lu.email_notifications import send_invitation_approval_email

        accepted_invitations = queryset.filter(
            status="accepted", approval_status="pending_approval"
        )

        updated = 0
        emails_sent = 0

        for invitation in accepted_invitations:
            invitation.approval_status = "approved"
            invitation.approved_at = timezone.now()
            invitation.save()
            updated += 1

            if send_invitation_approval_email(invitation, request=request):
                emails_sent += 1

        if updated > 0:
            django_messages.success(
                request,
                _(
                    "Approved {updated} guest(s) to attend the event. "
                    "Sent {emails_sent} email notification(s)."
                ).format(updated=updated, emails_sent=emails_sent),
            )
        else:
            django_messages.warning(
                request,
                _(
                    "No pending invitations to approve. Only accepted invitations can be approved."
                ),
            )

    @admin.action(description=_("❌ Reject selected guests"))
    def reject_guests(self, request, queryset):
        """Reject guests from attending the event"""
        from django.utils import timezone

        accepted_invitations = queryset.filter(
            status="accepted", approval_status="pending_approval"
        )

        updated = accepted_invitations.update(
            approval_status="rejected", approved_at=timezone.now()
        )

        if updated > 0:
            django_messages.success(
                request,
                _("Rejected {count} guest(s). They will be notified.").format(
                    count=updated
                ),
            )
        else:
            django_messages.warning(
                request,
                _(
                    "No pending invitations to reject. Only accepted invitations can be rejected."
                ),
            )

    @admin.action(description=_("📧 Resend invitation emails"))
    def resend_invitations(self, request, queryset):
        """Resend invitation emails to guests who haven't accepted"""
        pending_invitations = queryset.filter(status="pending")
        count = pending_invitations.count()

        if count > 0:
            django_messages.info(
                request,
                _(
                    "Would resend {count} invitation(s). Email sending not yet implemented."
                ).format(count=count),
            )
        else:
            django_messages.warning(
                request,
                _(
                    "No pending invitations to resend. Only unaccepted invitations can be resent."
                ),
            )


class EventFeedbackAdmin(admin.ModelAdmin):
    """
    📝 POST-EVENT FEEDBACK REVIEW

    Read-only view of attendee survey responses (NPS + free text).
    Feedback is submitted by users after events; coaches review it here
    for quality assurance. Records are never created from the admin.
    """

    list_display = (
        "user",
        "event",
        "nps_score",
        "get_nps_segment",
        "would_recommend",
        "created_at",
    )
    list_select_related = ["user", "event"]
    list_filter = ("would_recommend", "nps_score", "created_at")
    search_fields = ("user__username", "user__email", "event__title")
    readonly_fields = (
        "event",
        "user",
        "nps_score",
        "would_recommend",
        "what_worked",
        "what_to_improve",
        "created_at",
    )
    date_hierarchy = "created_at"
    list_per_page = 50

    fieldsets = (
        (_("Response"), {"fields": ("event", "user", "nps_score", "would_recommend")}),
        (
            _("Free Text (visible to coaches only)"),
            {"fields": ("what_worked", "what_to_improve")},
        ),
        (_("Metadata"), {"fields": ("created_at",)}),
    )

    @admin.display(description=_("NPS Segment"))
    def get_nps_segment(self, obj):
        if obj.is_promoter:
            return format_html('<span style="color: #28a745;">😍 Promoter</span>')
        if obj.is_detractor:
            return format_html('<span style="color: #dc3545;">😞 Detractor</span>')
        return format_html('<span style="color: #6c757d;">😐 Passive</span>')

    def has_add_permission(self, request):
        # Feedback comes from attendees via the post-event survey.
        return False

    def has_delete_permission(self, request, obj=None):
        # Preserve the survey audit trail; rows are only removed by
        # app-level cascades (e.g. account deletion).
        return False
