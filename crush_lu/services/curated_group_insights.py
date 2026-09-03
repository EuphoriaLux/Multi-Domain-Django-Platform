"""Read-only insight presenters for curated speed-dating groups.

The fairness of a curated evening is real but, until now, invisible: the
projector's decisions live in ``CuratedEventGroup.audit_data``, the roster in
Django admin, and the reason somebody was left out nowhere at all. This module
turns those stored facts into plain-language context for the coach event page.

Everything here reads. Group lifecycle changes (generate, approve, invite,
lock, start, repair) stay admin actions in ``crush_lu/admin/events.py``; a
"why this group" sentence is built from the audit trail the lifecycle already
stored, never from a fresh policy decision.

Coach-only. The dictionaries returned carry other members' names, genders and
ages, so they may only ever reach a ``@coach_required`` template.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedEventPairingParticipant,
    EventRegistration,
)

# ``_applicant`` and ``_projection_incomplete_reasons`` are the projector's own
# adapter and fail-closed eligibility rule. ``project_groups`` builds its
# ``ineligibility_reasons`` from exactly this pair before any optimisation, so
# reading them here cannot disagree with a generation the organiser stores
# (``test_left_out_reasons_match_the_projection`` pins that). The models module
# already imports ``_applicant`` the same way for ``schedule_viability``.
from crush_lu.services.event_grouping import (
    GROUPING_CANDIDATE_STATUSES,
    GroupingGroupSizeTooLarge,
    GroupingPoolTooLarge,
    _applicant,
    _projection_incomplete_reasons,
    project_event_groups,
)

# The workflow's ``_current_active_groups`` deliberately excludes ``degraded``
# because a degraded group is never approved, locked or started. For display
# it is the most important state of all: a repair that could not retire a
# checkout leaves the group degraded in the latest generation with no
# successor, and the coach has to see that.
CURRENT_GENERATION_STATUSES = (
    CuratedEventGroup.STATUS_DRAFT,
    CuratedEventGroup.STATUS_PROVISIONAL,
    CuratedEventGroup.STATUS_LOCKED,
    CuratedEventGroup.STATUS_DEGRADED,
)

STAGE_NONE = "none"
STAGE_DRAFT = "draft"
STAGE_PROVISIONAL = "provisional"
STAGE_LOCKED = "locked"
STAGE_STARTED = "started"
STAGE_DEGRADED = "degraded"

NEXT_WAIT_FOR_DEADLINE = "wait_for_deadline"
NEXT_GENERATE = "generate"
NEXT_REGENERATE = "regenerate"
NEXT_APPROVE = "approve"
NEXT_INVITE = "invite"
NEXT_CHECK_IN_AND_LOCK = "check_in_and_lock"
NEXT_START = "start"
NEXT_DELIVERED = "delivered"
NEXT_REPAIR = "repair"
NEXT_CANCELLED = "cancelled"

# Admin action names quoted verbatim (minus the emoji) so a coach can find the
# entry in the actions dropdown.
NEXT_ACTION_LABELS = {
    NEXT_WAIT_FOR_DEADLINE: _(
        "Wait for the application deadline, then run “Generate fair curated groups”."
    ),
    NEXT_GENERATE: _("Run “Generate fair curated groups”."),
    NEXT_REGENERATE: _(
        "Run “Generate fair curated groups” again: the pool or the deadline "
        "changed since this draft, so approval would be refused."
    ),
    NEXT_APPROVE: _("Review the draft, then run “Approve all current fair groups”."),
    NEXT_INVITE: _("Run “Invite the approved generation to pay”."),
    NEXT_CHECK_IN_AND_LOCK: _(
        "Check every member in at the door, then run “Lock checked-in curated groups”."
    ),
    NEXT_START: _("Run “Mark curated round one as started” as the first round begins."),
    NEXT_DELIVERED: _("Round one has been marked as started. Nothing left to do here."),
    NEXT_REPAIR: _("Run “Reproject or compensate degraded groups”."),
    NEXT_CANCELLED: _("The event is cancelled; no group action applies."),
}

INELIGIBILITY_LABELS = {
    "missing_event_preferences": _("no event preferences on the application"),
    "missing_gender": _("no gender on the profile"),
    "missing_age": _("no date of birth on the profile"),
    "outside_event_age_range": _("outside the event's age range"),
    "incomplete_application": _("incomplete application"),
}


def current_generation_groups(event):
    """The groups a coach is looking at: latest generation still in play."""

    groups = list(
        CuratedEventGroup.objects.filter(
            event=event, status__in=CURRENT_GENERATION_STATUSES
        ).order_by("group_number", "pk")
    )
    if not groups:
        return []
    generation = max(group.generation for group in groups)
    return [group for group in groups if group.generation == generation]


def coach_group_panel(event, registrations=None):
    """Everything the coach "Groups" tab shows, or ``None`` when it must not.

    ``registrations`` is the coach page's already-loaded, non-cancelled list
    (``select_related`` on profile and preference); pass it to avoid a second
    load. Direct-registration events and legacy curated events without a
    ``group_size`` get ``None`` so their pages stay byte-identical.

    The projector runs only while its answer is the interesting one: before a
    generation exists and while the current one is a draft. Once a generation
    is approved the stored groups are the truth, and a 42-person pool costs
    the projector about two seconds -- not a price to pay on every page load
    of a delivered evening.
    """

    if not (event.uses_curated_registration and event.group_size):
        return None

    if registrations is None:
        registrations = (
            EventRegistration.objects.filter(event=event)
            .exclude(status="cancelled")
            .select_related("user__crushprofile", "preference")
            .order_by("registered_at")
        )
    candidates = [
        registration
        for registration in registrations
        if registration.status in GROUPING_CANDIDATE_STATUSES
    ]

    groups = current_generation_groups(event)
    stage = _stage(event, groups)
    deadline_passed = timezone.now() >= event.registration_deadline

    projection = None
    projection_error = None
    if stage in (STAGE_NONE, STAGE_DRAFT):
        # Same seed the Approve action re-projects with, so a draft preflight
        # shows the organiser exactly what approval will re-check.
        seed = groups[0].seed if groups else None
        try:
            projection = project_event_groups(event, deterministic_seed=seed)
        except (GroupingPoolTooLarge, GroupingGroupSizeTooLarge) as error:
            projection_error = str(error)

    reasons_by_id = {}
    for registration in candidates:
        reasons = _projection_incomplete_reasons(event, _applicant(registration))
        if reasons:
            reasons_by_id[registration.pk] = reasons

    memberships = []
    participants = []
    if groups:
        memberships = list(
            CuratedEventGroupMembership.objects.filter(group__in=groups)
            .select_related("registration__user__crushprofile")
            .order_by("group_id", "position", "pk")
        )
        participants = list(
            CuratedEventPairingParticipant.objects.filter(group__in=groups)
            .select_related("pairing", "registration__user__crushprofile")
            .order_by("group_id", "round_number", "pairing__table_number", "seat")
        )

    active_membership_ids = {
        membership.registration_id
        for membership in memberships
        if membership.released_at is None
    }
    stale = _draft_is_stale(event, stage, groups, projection)
    next_action = _next_action(
        event,
        stage,
        memberships,
        deadline_passed=deadline_passed,
        stale=stale,
    )

    preflight = None
    if stage in (STAGE_NONE, STAGE_DRAFT):
        preflight = _preflight(candidates, projection, projection_error, stale)

    if groups:
        placed_ids = active_membership_ids
    elif projection is not None:
        placed_ids = set(projection.selected_registration_ids)
    else:
        placed_ids = set()

    left_out_blocked = []
    left_out_eligible = []
    for registration in candidates:
        if registration.pk in placed_ids:
            continue
        person = _person(registration, reasons=reasons_by_id.get(registration.pk, ()))
        (left_out_blocked if person["reasons"] else left_out_eligible).append(person)

    admin_url = reverse("crush_admin:crush_lu_meetupevent_changelist")
    if event.title:
        admin_url = f"{admin_url}?{urlencode({'q': event.title})}"

    return {
        "stage": stage,
        "generation": groups[0].generation if groups else None,
        "rounds_started": event.curated_rounds_started_at is not None,
        "event_cancelled": event.is_cancelled,
        "deadline_passed": deadline_passed,
        "next_action": next_action,
        "next_action_label": NEXT_ACTION_LABELS[next_action],
        "admin_url": admin_url,
        "preflight": preflight,
        "groups": [_group_card(group, memberships, participants) for group in groups],
        "left_out": {
            "preview": not groups,
            "blocked": left_out_blocked,
            "eligible": left_out_eligible,
        },
    }


def _stage(event, groups):
    """Degraded beats everything: it is the one state that needs a hand."""

    statuses = {group.status for group in groups}
    if CuratedEventGroup.STATUS_DEGRADED in statuses:
        return STAGE_DEGRADED
    if event.curated_rounds_started_at is not None:
        return STAGE_STARTED
    if not groups:
        return STAGE_NONE
    if statuses == {CuratedEventGroup.STATUS_LOCKED}:
        return STAGE_LOCKED
    if CuratedEventGroup.STATUS_PROVISIONAL in statuses:
        return STAGE_PROVISIONAL
    return STAGE_DRAFT


def _draft_is_stale(event, stage, groups, projection):
    """Mirror the two checks that make ``approve_current_generation`` refuse.

    A draft made before the deadline, or one whose input digest no longer
    matches the pool, is refused at approval time with "regenerate". Saying so
    on the page saves the organiser a round trip through an error message.
    """

    if stage != STAGE_DRAFT:
        return False
    if any(group.created_at < event.registration_deadline for group in groups):
        return True
    if projection is None:
        return False
    return any(
        group.viability_summary.get("projection_input_digest")
        != projection.input_digest
        for group in groups
    )


def _next_action(event, stage, memberships, *, deadline_passed, stale):
    if event.is_cancelled:
        return NEXT_CANCELLED
    if stage == STAGE_DEGRADED:
        return NEXT_REPAIR
    if stage == STAGE_STARTED:
        return NEXT_DELIVERED
    if stage == STAGE_LOCKED:
        return NEXT_START
    if stage == STAGE_PROVISIONAL:
        still_applied = any(
            membership.released_at is None
            and membership.registration.status == "applied"
            for membership in memberships
        )
        return NEXT_INVITE if still_applied else NEXT_CHECK_IN_AND_LOCK
    if stage == STAGE_DRAFT:
        return NEXT_REGENERATE if stale else NEXT_APPROVE
    return NEXT_GENERATE if deadline_passed else NEXT_WAIT_FOR_DEADLINE


def _preflight(candidates, projection, projection_error, stale):
    if projection is None:
        return {
            "applications": len(candidates),
            "eligible": None,
            "viable_groups": None,
            "left_out": None,
            "error": projection_error,
            "stale": stale,
        }
    ineligible_ids = {
        registration_id
        for registration_id, _reasons in projection.ineligibility_reasons
    }
    return {
        "applications": len(candidates),
        "eligible": len(candidates) - len(ineligible_ids),
        "viable_groups": len(projection.viable_groups),
        "left_out": len(projection.unassigned_registration_ids),
        "error": None,
        "stale": stale,
    }


def _display_name(user):
    profile = getattr(user, "crushprofile", None)
    if profile is not None:
        return profile.display_name
    return user.first_name or user.username


def _person(registration, *, dates=None, released=False, reasons=()):
    user = registration.user
    profile = getattr(user, "crushprofile", None)
    return {
        "registration_id": registration.pk,
        "user_id": user.pk,
        "name": _display_name(user),
        "gender": getattr(profile, "gender", "") or "",
        "gender_display": (
            profile.get_gender_display()
            if profile is not None and profile.gender
            else ""
        ),
        "age": getattr(profile, "age", None),
        "verified": bool(profile is not None and profile.is_approved),
        "status": registration.status,
        "status_display": registration.get_status_display(),
        "dates": dates,
        "released": released,
        "reasons": [INELIGIBILITY_LABELS.get(code, code) for code in reasons],
    }


def _group_card(group, memberships, participants):
    own_memberships = [m for m in memberships if m.group_id == group.pk]
    own_participants = [p for p in participants if p.group_id == group.pk]

    dates_by_registration = {}
    tables_by_round = {}
    for participant in own_participants:
        dates_by_registration[participant.registration_id] = (
            dates_by_registration.get(participant.registration_id, 0) + 1
        )
        tables = tables_by_round.setdefault(participant.round_number, {})
        table = tables.setdefault(
            participant.pairing.table_number,
            {"table": participant.pairing.table_number, "a": "", "b": ""},
        )
        table[participant.seat] = _display_name(participant.registration.user)

    # Active members first in seating position, released ones after them,
    # struck through by the template.
    ordered = sorted(
        own_memberships,
        key=lambda m: (m.released_at is not None, m.position, m.pk),
    )
    members = [
        _person(
            membership.registration,
            dates=dates_by_registration.get(membership.registration_id, 0),
            released=membership.released_at is not None,
        )
        for membership in ordered
    ]
    active_names = {member["name"] for member in members if not member["released"]}

    schedule = []
    for round_number in sorted(tables_by_round):
        tables = [
            tables_by_round[round_number][t]
            for t in sorted(tables_by_round[round_number])
        ]
        seated = {table["a"] for table in tables} | {table["b"] for table in tables}
        schedule.append(
            {
                "round": round_number,
                "tables": tables,
                "sitting_out": sorted(active_names - seated),
            }
        )

    summary = group.viability_summary or {}
    fairness = (group.audit_data or {}).get("fairness_decision") or {}
    degradation = (group.audit_data or {}).get("degradation") or {}
    projected_size = summary.get("members") or len(own_memberships)
    rounds = summary.get("rounds") or (max(tables_by_round) if tables_by_round else 0)
    minimum_dates = summary.get("minimum_dates")
    if minimum_dates is None:
        minimum_dates = fairness.get("min_achieved")
    target_dates = (
        summary.get("target_dates")
        or fairness.get("target_requested")
        or CuratedEventGroup.TARGET_DATES
    )
    members_meeting_target = summary.get("members_meeting_target")
    if members_meeting_target is None:
        members_meeting_target = fairness.get("members_meeting_target")
    target_achieved = fairness.get("target_achieved")
    if target_achieved is None and members_meeting_target is not None:
        target_achieved = members_meeting_target >= projected_size

    return {
        "id": group.pk,
        "number": group.group_number,
        "generation": group.generation,
        "status": group.status,
        "status_display": group.get_status_display(),
        "degraded_from": degradation.get("from_status") or "",
        "members": members,
        "active_count": len(active_names),
        "projected_size": projected_size,
        "rounds": rounds,
        "minimum_dates": minimum_dates,
        "target_dates": target_dates,
        "members_meeting_target": members_meeting_target,
        "target_achieved": bool(target_achieved),
        "one_drop_resilient": fairness.get("one_drop_resilient"),
        "why": _why_sentences(fairness, projected_size=projected_size),
        "schedule": schedule,
    }


def _why_sentences(fairness, *, projected_size):
    """Plain words for the stored fairness decision. Nothing is recomputed.

    Target attainment and one-drop resilience are on the card's summary line
    already; this covers the part a coach cannot read off the roster: which
    compatibility track the group came from and why it was chosen over an
    alternative.
    """

    sentences = []
    track_size = fairness.get("track_size")
    if track_size:
        if track_size <= projected_size:
            sentences.append(
                _(
                    "Its members form a compatibility track of their own: no "
                    "other applicant was mutually compatible with anyone in it."
                )
            )
        else:
            sentences.append(
                _(
                    "Drawn from a compatibility track of %(track)d mutually "
                    "compatible applicants; group %(ordinal)d within that track."
                )
                % {"track": track_size, "ordinal": fairness.get("track_ordinal") or 1}
            )
    if fairness.get("underserved_priority"):
        sentences.append(
            _("Prioritised because its members had few alternative groups in the pool.")
        )
    pinned = fairness.get("pinned_member_count") or 0
    if pinned:
        sentences.append(
            ngettext(
                "%(count)d member already held a seat when this projection ran.",
                "%(count)d members already held a seat when this projection ran.",
                pinned,
            )
            % {"count": pinned}
        )
    return sentences
