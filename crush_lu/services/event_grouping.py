"""Build privacy-safe signals and fixed groups from reciprocal preferences.

The member-facing helpers at the top of this module deliberately return only a
bucketed mutual-match signal. The projection engine below uses the exact same
applicant adapter and hard-filter predicate, so a recruiting signal and the
organiser's eventual groups can never disagree about what "compatible" means.

Nothing in here reads or returns another member's preferences. The adapter
exists so that comparisons happen on plain values in Python, and the only thing
that ever leaves this module for a member surface is a count -- bucketed by the
caller before display, because an exact integer over Art. 9-adjacent preference
rows is a derived disclosure (flip your own stated preference, diff the count,
infer the pool).

Grouping is intentionally a deterministic, bounded optimiser rather than a
demographic router. Its tracks are connected components of the reciprocal
compatibility graph; labels such as ``mf``/``ff``/``mm`` never enter the
decision. A projected group is viable only when the module has constructed an
actual no-repeat round schedule in which every member receives the guaranteed
minimum number of mutually compatible mini-dates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from crush_lu.matching import passes_event_hard_filters

GROUPING_POLICY_VERSION = "reciprocal-graph-v1"
DEFAULT_MIN_DATES = 5
DEFAULT_TARGET_DATES = 7
MAX_GROUPING_APPLICANTS = 500
MAX_PROJECTED_GROUP_SIZE = 42

# ``applied`` is still deliberately not seat-holding. It belongs here because
# a final check-in projection must see both candidates and the people already
# selected. Pinned statuses may never be silently displaced by a later run.
GROUPING_CANDIDATE_STATUSES = ("applied", "pending", "confirmed", "attended")
PINNED_REGISTRATION_STATUSES = frozenset({"pending", "confirmed", "attended"})

_MAX_MEMBERSHIP_CANDIDATES_PER_TRACK = 128
_MAX_VIABLE_CANDIDATES_PER_TRACK = 96
_MAX_LARGE_GROUP_MEMBERSHIP_CANDIDATES = 24
_MAX_LARGE_GROUP_VIABLE_CANDIDATES = 16
_LOCAL_PLAN_BEAM_WIDTH = 128
# Cross-track objectives are monotone under union for an exact group count;
# retain the single dominant state per count. Seeded tie variation may differ,
# but never a safety, fairness, coverage, leximin, target, or resilience score.
_GLOBAL_TRACK_PLAN_BEAM_WIDTH = 1
_EXACT_MATCHING_MEMBER_LIMIT = 18

# How big a mutual-match pool is, expressed in tables rather than people. The
# caller turns these into copy; they exist as keys so the thresholds live in one
# place and the templates cannot invent their own.
MATCH_BUCKET_FEW = "few"
MATCH_BUCKET_HALF = "half"
MATCH_BUCKET_EVENING = "evening"
MATCH_BUCKET_MULTIPLE = "multiple"


@dataclass(frozen=True, slots=True)
class EventApplicant:
    """Immutable, event-specific input to compatibility checks and grouping."""

    user_id: int
    registration_id: int | None
    gender: str | None
    age: int | None
    preferred_genders: tuple[str, ...]
    preferred_age_min: int
    preferred_age_max: int
    languages: tuple[str, ...]
    status: str | None = None
    pinned: bool = False
    eligible_for_grouping: bool = True
    incomplete_reasons: tuple[str, ...] = ()


class GroupingPoolTooLarge(ValueError):
    """Raised rather than silently truncating an operational applicant pool."""


class GroupingGroupSizeTooLarge(ValueError):
    """Raised when online projection cannot safely support a configured group."""


@dataclass(frozen=True, slots=True)
class ScheduledPair:
    """One mutual, no-repeat mini-date in a particular round."""

    registration_a_id: int
    registration_b_id: int


@dataclass(frozen=True, slots=True)
class ScheduledRound:
    """All simultaneous mini-dates and breaks for one numbered round."""

    number: int
    pairs: tuple[ScheduledPair, ...]
    break_registration_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ProjectedGroup:
    """A fixed all-evening group backed by a concrete round schedule."""

    key: str
    registration_ids: tuple[int, ...]
    pinned_registration_ids: tuple[int, ...]
    rounds: tuple[ScheduledRound, ...]
    date_counts: tuple[tuple[int, int], ...]
    viable: bool
    minimum_dates_required: int
    minimum_dates_achieved: int
    target_dates_requested: int
    members_meeting_target: int
    target_achieved: bool
    compatibility_track_id: str
    compatibility_track_size: int
    group_ordinal_in_track: int
    underserved_priority: bool
    alternative_scarcity_score: int
    one_drop_resilient: bool

    @property
    def scheduled_pair_count(self) -> int:
        return sum(len(round_.pairs) for round_ in self.rounds)


@dataclass(frozen=True, slots=True)
class GroupProjection:
    """Persistence-friendly result of one deterministic grouping run."""

    policy_version: str
    deterministic_seed: str
    group_size_limit: int
    group_limit: int
    minimum_dates_required: int
    target_dates_requested: int
    viable_groups: tuple[ProjectedGroup, ...]
    selected_registration_ids: tuple[int, ...]
    unassigned_registration_ids: tuple[int, ...]
    infeasible_registration_ids: tuple[int, ...]
    ineligibility_reasons: tuple[tuple[int, tuple[str, ...]], ...]
    pinned_registration_ids: tuple[int, ...]
    pinned_unassigned_registration_ids: tuple[int, ...]
    pinned_infeasible_registration_ids: tuple[int, ...]
    retains_all_pinned: bool

    @property
    def groups(self) -> tuple[ProjectedGroup, ...]:
        """Compatibility alias for callers that do not need the qualifier."""

        return self.viable_groups


@dataclass(frozen=True, slots=True)
class CompatibilityGraph:
    """Reciprocal compatibility graph keyed by stable registration IDs."""

    registration_ids: tuple[int, ...]
    edges: tuple[tuple[int, int], ...]
    _adjacency: Mapping[int, frozenset[int]] = field(repr=False, compare=False)

    def neighbours(self, registration_id: int) -> frozenset[int]:
        return self._adjacency.get(registration_id, frozenset())

    def has_edge(self, left_id: int, right_id: int) -> bool:
        return right_id in self.neighbours(left_id)


@dataclass(frozen=True, slots=True)
class _Schedule:
    rounds: tuple[ScheduledRound, ...]
    date_counts: tuple[tuple[int, int], ...]
    minimum_dates_achieved: int
    members_meeting_target: int


@dataclass(frozen=True, slots=True)
class _GroupCandidate:
    registration_ids: tuple[int, ...]
    pinned_registration_ids: tuple[int, ...]
    schedule: _Schedule
    track_id: str
    track_size: int
    scarcity_score: int
    resilience_potential: bool


@dataclass(frozen=True, slots=True)
class _PlanState:
    member_mask: int
    candidates: tuple[_GroupCandidate, ...]


def _applicant(registration):
    """Adapt one application to the duck-typed shape the filters expect.

    Identity (gender, age) comes from the profile; what they are looking for
    comes from the application's own preference snapshot, not the profile --
    the snapshot is what they said for *this* event, and Phase 1 collects it
    precisely so a preference change later cannot rewrite a past application.

    Neutral values keep the object safe to compare, but guaranteed grouping
    fails closed when the preference snapshot, gender, or age is absent. An
    explicitly stored empty gender/language list remains a real "open to all"
    answer; no preference row is an omission, not consent.
    """
    profile = getattr(registration.user, "crushprofile", None)
    pref = getattr(registration, "preference", None)
    status = getattr(registration, "status", None)
    gender = getattr(profile, "gender", None) or None
    age = getattr(profile, "age", None)
    incomplete_reasons = []
    if pref is None:
        incomplete_reasons.append("missing_event_preferences")
    if gender is None:
        incomplete_reasons.append("missing_gender")
    if age is None:
        incomplete_reasons.append("missing_age")
    return EventApplicant(
        user_id=registration.user_id,
        registration_id=registration.pk,
        gender=gender,
        age=age,
        preferred_genders=tuple(getattr(pref, "preferred_genders", None) or ()),
        preferred_age_min=getattr(pref, "preferred_age_min", None) or 18,
        preferred_age_max=getattr(pref, "preferred_age_max", None) or 99,
        languages=tuple(getattr(pref, "languages", None) or ()),
        status=status,
        pinned=status in PINNED_REGISTRATION_STATUSES,
        eligible_for_grouping=not incomplete_reasons,
        incomplete_reasons=tuple(incomplete_reasons),
    )


def load_applicants(event):
    """Every application on ``event``, adapted, in one query.

    ``user__crushprofile`` and ``preference`` are both reverse one-to-ones, so
    this joins outward twice -- fine here, and deliberately never combined with
    ``select_for_update()``: PostgreSQL refuses row locks on the nullable side
    of an outer join, the trap ``confirm_registrations`` documents at length.
    """
    registrations = (
        event.eventregistration_set.filter(status="applied")
        .select_related("user__crushprofile", "preference")
        .order_by("pk")
    )
    return [_applicant(reg) for reg in registrations]


def load_grouping_candidates(event):
    """Load applications plus every already selected seat-holder.

    This is intentionally separate from :func:`load_applicants`, whose applied
    only semantics feed the public pool signal. A re-projection immediately
    before round one must include pending-payment, paid/confirmed, and checked
    in registrations and mark them as pinned. The optimiser then maximises
    pinned retention before every other objective and reports any impossible
    retention explicitly.
    """

    registrations = (
        event.eventregistration_set.filter(status__in=GROUPING_CANDIDATE_STATUSES)
        .select_related("user__crushprofile", "preference")
        .order_by("pk")
    )
    return [_applicant(reg) for reg in registrations]


def viewer_applicant(user, profile, event, registration=None):
    """The viewer's own side of the comparison.

    Their live application when they have one; otherwise the same prefill chain
    the application form itself offers (Connect membership -> legacy profile
    preferences -> blank), so the number someone sees *before* applying is
    computed against the answers they would arrive with rather than against
    nothing.
    """
    if registration is not None and registration.status == "applied":
        return _applicant(registration)

    from crush_lu.forms import EventPreferenceForm

    initial = EventPreferenceForm.initial_for(user, profile, event) or {}
    return EventApplicant(
        user_id=user.pk,
        registration_id=None,
        gender=getattr(profile, "gender", None) or None,
        age=getattr(profile, "age", None),
        preferred_genders=tuple(initial.get("preferred_genders") or ()),
        preferred_age_min=initial.get("preferred_age_min") or 18,
        preferred_age_max=initial.get("preferred_age_max") or 99,
        languages=tuple(initial.get("languages") or ()),
    )


def count_mutual_matches(viewer, applicants):
    """How many applicants this viewer and the applicant both want to meet.

    Self-excluded by ``user_id``: an applicant is always a perfect match for
    themselves, and counting it would inflate every number by one and make the
    signal appear on a pool of one. Incomplete identity or event-preference
    data contributes no count, matching the fail-closed grouping guarantee.
    """
    if _grouping_incomplete_reasons(viewer):
        return 0
    return sum(
        1
        for applicant in applicants
        if applicant.user_id != viewer.user_id
        and not _grouping_incomplete_reasons(applicant)
        and passes_event_hard_filters(viewer, applicant)
    )


def match_bucket(count, group_size):
    """Size a mutual-match pool in tables.

    Bucketed rather than exact for the privacy reason in the module docstring,
    and because "enough to fill an evening" is the more motivating sentence than
    "23". ``MATCH_BUCKET_FEW`` is the caller's cue to offer widening the
    preferences instead of printing a discouraging number.
    """
    if group_size < 2:
        return MATCH_BUCKET_FEW
    if count >= 2 * group_size:
        return MATCH_BUCKET_MULTIPLE
    if count >= group_size:
        return MATCH_BUCKET_EVENING
    if count >= max(1, group_size // 2):
        return MATCH_BUCKET_HALF
    return MATCH_BUCKET_FEW


def build_compatibility_graph(applicants: Iterable[Any]) -> CompatibilityGraph:
    """Return the reciprocal hard-filter graph for event applicants.

    No Connect membership, profile-completeness, score, or demographic route is
    consulted. Duplicate or absent registration IDs are rejected because a
    schedule that cannot be persisted unambiguously is not a valid projection.
    """

    by_id: dict[int, Any] = {}
    for applicant in applicants:
        registration_id = getattr(applicant, "registration_id", None)
        if registration_id is None:
            raise ValueError("Grouping applicants need a registration_id")
        if registration_id in by_id:
            raise ValueError(f"Duplicate grouping registration_id: {registration_id}")
        by_id[registration_id] = applicant
        if len(by_id) > MAX_GROUPING_APPLICANTS:
            raise GroupingPoolTooLarge(
                "Curated grouping received more than "
                f"{MAX_GROUPING_APPLICANTS} applicants; run an explicit "
                "offline/manual review instead of truncating the pool."
            )

    registration_ids = tuple(sorted(by_id))
    adjacency: dict[int, set[int]] = {
        registration_id: set() for registration_id in registration_ids
    }
    edges: list[tuple[int, int]] = []
    for index, left_id in enumerate(registration_ids):
        left = by_id[left_id]
        if _grouping_incomplete_reasons(left):
            continue
        for right_id in registration_ids[index + 1 :]:
            right = by_id[right_id]
            if _grouping_incomplete_reasons(right):
                continue
            if not passes_event_hard_filters(left, right):
                continue
            adjacency[left_id].add(right_id)
            adjacency[right_id].add(left_id)
            edges.append((left_id, right_id))

    frozen_adjacency = MappingProxyType(
        {
            registration_id: frozenset(neighbours)
            for registration_id, neighbours in adjacency.items()
        }
    )
    return CompatibilityGraph(
        registration_ids=registration_ids,
        edges=tuple(edges),
        _adjacency=frozen_adjacency,
    )


def project_event_groups(
    event,
    *,
    minimum_dates: int = DEFAULT_MIN_DATES,
    target_dates: int = DEFAULT_TARGET_DATES,
    deterministic_seed: str | int | None = None,
) -> GroupProjection:
    """Load lifecycle-aware candidates and project fixed groups for ``event``."""

    return project_groups(
        event,
        load_grouping_candidates(event),
        minimum_dates=minimum_dates,
        target_dates=target_dates,
        deterministic_seed=deterministic_seed,
    )


def project_groups(
    event,
    applicants: Sequence[Any],
    *,
    minimum_dates: int = DEFAULT_MIN_DATES,
    target_dates: int = DEFAULT_TARGET_DATES,
    deterministic_seed: str | int | None = None,
) -> GroupProjection:
    """Project deterministic, fixed-membership groups and concrete rounds.

    Objectives are applied lexicographically:

    1. retain as many pinned/seat-holding registrations as possible;
    2. cover distinct viable compatibility tracks before repeating a track;
    3. cover applicants with fewer reciprocal alternatives, so a flexible
       bridge cannot hide an underserved viable community;
    4. maximise applicants receiving the guaranteed minimum;
    5. maximise the leximin vector of scheduled opportunities;
    6. maximise target attainment and then total mini-dates;
    7. prefer groups with structural one-drop resilience potential;
    8. resolve any remaining tie from the returned deterministic seed.

    This is a bounded optimiser. It never calls a group viable from headcount
    or graph degree alone: candidate membership survives only when
    :func:`_build_schedule` constructs an actual schedule. Consequently the
    engine may conservatively leave someone unassigned rather than promise a
    guarantee it cannot demonstrate.
    """

    if minimum_dates < 1:
        raise ValueError("minimum_dates must be at least 1")
    if target_dates < minimum_dates:
        raise ValueError("target_dates must be at least minimum_dates")

    applicants = tuple(applicants)
    group_size = _event_group_size(event, len(applicants))
    if group_size > MAX_PROJECTED_GROUP_SIZE:
        raise GroupingGroupSizeTooLarge(
            f"Configured group_size {group_size} exceeds the online projection "
            f"limit of {MAX_PROJECTED_GROUP_SIZE}; run an explicit offline/manual "
            "review instead."
        )
    graph = build_compatibility_graph(applicants)
    by_id = {applicant.registration_id: applicant for applicant in applicants}

    group_limit = _event_group_limit(event)
    search_group_limit = min(
        group_limit,
        len(applicants) // (minimum_dates + 1),
    )
    seed = _projection_seed(
        event, graph.registration_ids, deterministic_seed=deterministic_seed
    )
    pinned_ids = frozenset(
        registration_id
        for registration_id, applicant in by_id.items()
        if bool(getattr(applicant, "pinned", False))
        or getattr(applicant, "status", None) in PINNED_REGISTRATION_STATUSES
    )
    ineligibility_reasons = {
        registration_id: reasons
        for registration_id, applicant in by_id.items()
        if (reasons := _grouping_incomplete_reasons(applicant))
    }

    if (
        not graph.registration_ids
        or search_group_limit < 1
        or group_size < minimum_dates + 1
    ):
        return _empty_projection(
            graph.registration_ids,
            pinned_ids,
            seed=seed,
            group_size=group_size,
            group_limit=group_limit,
            minimum_dates=minimum_dates,
            target_dates=target_dates,
            ineligibility_reasons=ineligibility_reasons,
        )

    candidates: list[_GroupCandidate] = []
    for component in _connected_components(graph):
        core = _minimum_degree_core(component, graph, minimum_dates)
        if len(core) < minimum_dates + 1:
            continue
        candidates.extend(
            _viable_candidates_for_track(
                core,
                graph,
                group_size=group_size,
                minimum_dates=minimum_dates,
                target_dates=target_dates,
                pinned_ids=pinned_ids,
                seed=seed,
            )
        )

    selected_candidates = _select_candidate_plan(
        candidates,
        graph.registration_ids,
        pinned_ids=pinned_ids,
        group_limit=search_group_limit,
        target_dates=target_dates,
        seed=seed,
    )

    coverage = frozenset(
        registration_id
        for candidate in candidates
        for registration_id in candidate.registration_ids
    )
    selected_ids = frozenset(
        registration_id
        for candidate in selected_candidates
        for registration_id in candidate.registration_ids
    )
    track_sizes = {candidate.track_id: candidate.track_size for candidate in candidates}
    dominant_track_size = max(track_sizes.values(), default=0)
    selected_scarcity_floor = min(
        (candidate.scarcity_score for candidate in selected_candidates),
        default=0,
    )

    ordered_candidates = _fair_group_order(selected_candidates, seed=seed)
    track_ordinals: dict[str, int] = {}
    groups: list[ProjectedGroup] = []
    schedule_cache: dict[tuple[int, ...], _Schedule] = {
        candidate.registration_ids: candidate.schedule
        for candidate in selected_candidates
    }
    for index, candidate in enumerate(ordered_candidates, start=1):
        ordinal = track_ordinals.get(candidate.track_id, 0) + 1
        track_ordinals[candidate.track_id] = ordinal
        one_drop_resilient = _is_one_drop_resilient(
            candidate.registration_ids,
            graph,
            minimum_dates=minimum_dates,
            target_dates=target_dates,
            seed=f"{seed}:drop:{index}",
            schedule_cache=schedule_cache,
        )
        schedule = candidate.schedule
        groups.append(
            ProjectedGroup(
                key=f"group-{index:02d}",
                registration_ids=candidate.registration_ids,
                pinned_registration_ids=candidate.pinned_registration_ids,
                rounds=schedule.rounds,
                date_counts=schedule.date_counts,
                viable=schedule.minimum_dates_achieved >= minimum_dates,
                minimum_dates_required=minimum_dates,
                minimum_dates_achieved=schedule.minimum_dates_achieved,
                target_dates_requested=target_dates,
                members_meeting_target=schedule.members_meeting_target,
                target_achieved=(
                    schedule.members_meeting_target == len(candidate.registration_ids)
                ),
                compatibility_track_id=candidate.track_id,
                compatibility_track_size=candidate.track_size,
                group_ordinal_in_track=ordinal,
                underserved_priority=(
                    candidate.track_size < dominant_track_size
                    or candidate.scarcity_score > selected_scarcity_floor
                ),
                alternative_scarcity_score=candidate.scarcity_score,
                one_drop_resilient=one_drop_resilient,
            )
        )

    ordered_selected_ids = tuple(sorted(selected_ids))
    unassigned_ids = tuple(sorted(set(graph.registration_ids) - selected_ids))
    infeasible_ids = tuple(sorted(set(graph.registration_ids) - coverage))
    pinned_unassigned = tuple(sorted(pinned_ids - selected_ids))
    pinned_infeasible = tuple(sorted((pinned_ids - selected_ids) - coverage))
    return GroupProjection(
        policy_version=GROUPING_POLICY_VERSION,
        deterministic_seed=seed,
        group_size_limit=group_size,
        group_limit=group_limit,
        minimum_dates_required=minimum_dates,
        target_dates_requested=target_dates,
        viable_groups=tuple(groups),
        selected_registration_ids=ordered_selected_ids,
        unassigned_registration_ids=unassigned_ids,
        infeasible_registration_ids=infeasible_ids,
        ineligibility_reasons=tuple(sorted(ineligibility_reasons.items())),
        pinned_registration_ids=tuple(sorted(pinned_ids)),
        pinned_unassigned_registration_ids=pinned_unassigned,
        pinned_infeasible_registration_ids=pinned_infeasible,
        retains_all_pinned=not pinned_unassigned,
    )


def _event_group_size(event, applicant_count: int) -> int:
    configured = getattr(event, "group_size", None)
    if configured:
        return int(configured)
    maximum = getattr(event, "max_participants", None)
    if maximum:
        return int(maximum)
    return applicant_count


def _grouping_incomplete_reasons(applicant: Any) -> tuple[str, ...]:
    """Normalise fail-closed eligibility for every grouping entry point."""

    reasons = list(getattr(applicant, "incomplete_reasons", ()) or ())
    if not getattr(applicant, "gender", None) and "missing_gender" not in reasons:
        reasons.append("missing_gender")
    if getattr(applicant, "age", None) is None and "missing_age" not in reasons:
        reasons.append("missing_age")
    if not bool(getattr(applicant, "eligible_for_grouping", True)) and not reasons:
        reasons.append("incomplete_application")
    return tuple(reasons)


def _event_group_limit(event) -> int:
    planned = getattr(event, "planned_groups", None)
    if planned:
        return max(0, int(planned))
    maximum = getattr(event, "max_groups", 1)
    return max(0, int(maximum))


def _projection_seed(
    event,
    registration_ids: Sequence[int],
    *,
    deterministic_seed: str | int | None,
) -> str:
    if deterministic_seed is not None:
        return str(deterministic_seed)
    event_id = getattr(event, "pk", None)
    payload = "|".join(
        [
            GROUPING_POLICY_VERSION,
            str(event_id if event_id is not None else "unsaved"),
            *(str(registration_id) for registration_id in registration_ids),
        ]
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:20]


def _empty_projection(
    registration_ids: Sequence[int],
    pinned_ids: frozenset[int],
    *,
    seed: str,
    group_size: int,
    group_limit: int,
    minimum_dates: int,
    target_dates: int,
    ineligibility_reasons: Mapping[int, tuple[str, ...]],
) -> GroupProjection:
    ordered_ids = tuple(sorted(registration_ids))
    ordered_pinned = tuple(sorted(pinned_ids))
    return GroupProjection(
        policy_version=GROUPING_POLICY_VERSION,
        deterministic_seed=seed,
        group_size_limit=group_size,
        group_limit=group_limit,
        minimum_dates_required=minimum_dates,
        target_dates_requested=target_dates,
        viable_groups=(),
        selected_registration_ids=(),
        unassigned_registration_ids=ordered_ids,
        infeasible_registration_ids=ordered_ids,
        ineligibility_reasons=tuple(sorted(ineligibility_reasons.items())),
        pinned_registration_ids=ordered_pinned,
        pinned_unassigned_registration_ids=ordered_pinned,
        pinned_infeasible_registration_ids=ordered_pinned,
        retains_all_pinned=not ordered_pinned,
    )


def _connected_components(graph: CompatibilityGraph) -> tuple[tuple[int, ...], ...]:
    unseen = set(graph.registration_ids)
    components: list[tuple[int, ...]] = []
    while unseen:
        start = min(unseen)
        stack = [start]
        unseen.remove(start)
        component: list[int] = []
        while stack:
            current = stack.pop()
            component.append(current)
            new_neighbours = sorted(graph.neighbours(current) & unseen, reverse=True)
            for neighbour in new_neighbours:
                unseen.remove(neighbour)
                stack.append(neighbour)
        components.append(tuple(sorted(component)))
    return tuple(components)


def _minimum_degree_core(
    registration_ids: Sequence[int],
    graph: CompatibilityGraph,
    minimum_dates: int,
) -> tuple[int, ...]:
    """Peel applicants who cannot have ``minimum_dates`` unique partners."""

    core = set(registration_ids)
    while True:
        rejected = {
            registration_id
            for registration_id in core
            if len(graph.neighbours(registration_id) & core) < minimum_dates
        }
        if not rejected:
            return tuple(sorted(core))
        core -= rejected
        if not core:
            return ()


def _tie_value(seed: str, *parts: object) -> int:
    payload = "|".join([seed, *(str(part) for part in parts)])
    return int.from_bytes(sha256(payload.encode("utf-8")).digest()[:8], "big")


def _track_id(registration_ids: Sequence[int]) -> str:
    payload = ",".join(str(registration_id) for registration_id in registration_ids)
    return f"track-{sha256(payload.encode('ascii')).hexdigest()[:12]}"


def _viable_candidates_for_track(
    registration_ids: Sequence[int],
    graph: CompatibilityGraph,
    *,
    group_size: int,
    minimum_dates: int,
    target_dates: int,
    pinned_ids: frozenset[int],
    seed: str,
) -> list[_GroupCandidate]:
    track_ids = tuple(sorted(registration_ids))
    membership_options = _candidate_memberships(
        track_ids,
        graph,
        group_size=group_size,
        minimum_dates=minimum_dates,
        pinned_ids=pinned_ids,
        seed=seed,
    )
    track_id = _track_id(track_ids)
    candidates: list[_GroupCandidate] = []
    viable_limit = (
        _MAX_LARGE_GROUP_VIABLE_CANDIDATES
        if group_size > _EXACT_MATCHING_MEMBER_LIMIT
        else _MAX_VIABLE_CANDIDATES_PER_TRACK
    )
    for membership in membership_options:
        schedule = _build_schedule(
            membership,
            graph,
            minimum_dates=minimum_dates,
            target_dates=target_dates,
            seed=f"{seed}:{track_id}",
        )
        if schedule.minimum_dates_achieved < minimum_dates:
            continue
        member_set = set(membership)
        minimum_degree = min(
            len(graph.neighbours(registration_id) & member_set)
            for registration_id in membership
        )
        candidates.append(
            _GroupCandidate(
                registration_ids=membership,
                pinned_registration_ids=tuple(sorted(member_set & pinned_ids)),
                schedule=schedule,
                track_id=track_id,
                track_size=len(track_ids),
                scarcity_score=sum(
                    len(graph.registration_ids)
                    - 1
                    - len(graph.neighbours(registration_id))
                    for registration_id in membership
                ),
                resilience_potential=minimum_degree >= minimum_dates + 1,
            )
        )
        if len(candidates) >= viable_limit:
            break
    return candidates


def _candidate_memberships(
    registration_ids: Sequence[int],
    graph: CompatibilityGraph,
    *,
    group_size: int,
    minimum_dates: int,
    pinned_ids: frozenset[int],
    seed: str,
) -> tuple[tuple[int, ...], ...]:
    """Generate bounded, diverse possible memberships for one graph track."""

    registration_ids = tuple(registration_ids)
    maximum_size = min(group_size, len(registration_ids))
    minimum_size = minimum_dates + 1
    if maximum_size < minimum_size:
        return ()

    tie_order = tuple(
        sorted(
            registration_ids,
            key=lambda registration_id: (
                _tie_value(seed, "member", registration_id),
                registration_id,
            ),
        )
    )
    raw: set[tuple[int, ...]] = set()

    # Cyclic windows preserve whole-partition alternatives in dense tracks.
    # In a 42-person track with 14-person groups, offsets 0/14/28 therefore
    # remain available as a disjoint three-group plan instead of every
    # candidate competing for the same low-ID members.
    for size in range(maximum_size, minimum_size - 1, -1):
        if size == len(tie_order):
            raw.add(tuple(sorted(tie_order)))
            continue
        for offset in range(len(tie_order)):
            window = tuple(
                tie_order[(offset + step) % len(tie_order)] for step in range(size)
            )
            raw.add(tuple(sorted(window)))

    # Compatibility-led growth supplies non-contiguous candidates when the
    # stable order cuts across a bipartite or otherwise structured graph.
    tie_positions = {
        registration_id: index for index, registration_id in enumerate(tie_order)
    }
    for seed_id in tie_order:
        selected = [seed_id]
        remaining = set(registration_ids) - {seed_id}
        while remaining and len(selected) < maximum_size:
            selected_set = set(selected)
            current_degree = {
                registration_id: len(graph.neighbours(registration_id) & selected_set)
                for registration_id in selected
            }

            def growth_key(candidate_id: int):
                neighbours_in_group = graph.neighbours(candidate_id) & selected_set
                support = sum(
                    max(0, minimum_dates - current_degree[neighbour_id])
                    for neighbour_id in neighbours_in_group
                )
                circular_distance = (
                    tie_positions[candidate_id] - tie_positions[seed_id]
                ) % len(tie_order)
                return (
                    candidate_id in pinned_ids,
                    support,
                    len(neighbours_in_group),
                    -len(graph.neighbours(candidate_id)),
                    -circular_distance,
                    _tie_value(seed, "grow", seed_id, candidate_id),
                )

            chosen = max(remaining, key=growth_key)
            selected.append(chosen)
            remaining.remove(chosen)
            if len(selected) >= minimum_size:
                raw.add(tuple(sorted(selected)))

    structurally_possible: list[tuple[int, ...]] = []
    for membership in raw:
        member_set = set(membership)
        if all(
            len(graph.neighbours(registration_id) & member_set) >= minimum_dates
            for registration_id in membership
        ):
            structurally_possible.append(membership)

    def membership_score(membership: tuple[int, ...]):
        member_set = set(membership)
        degrees = tuple(
            sorted(
                len(graph.neighbours(registration_id) & member_set)
                for registration_id in membership
            )
        )
        edge_count = sum(degrees) // 2
        return (
            len(member_set & pinned_ids),
            len(membership),
            degrees,
            edge_count,
            min(degrees) >= minimum_dates + 1,
            _tie_value(seed, "membership", *membership),
        )

    structurally_possible.sort(key=membership_score, reverse=True)
    membership_limit = (
        _MAX_LARGE_GROUP_MEMBERSHIP_CANDIDATES
        if maximum_size > _EXACT_MATCHING_MEMBER_LIMIT
        else _MAX_MEMBERSHIP_CANDIDATES_PER_TRACK
    )
    return tuple(structurally_possible[:membership_limit])


def _build_schedule(
    registration_ids: Sequence[int],
    graph: CompatibilityGraph,
    *,
    minimum_dates: int,
    target_dates: int,
    seed: str,
) -> _Schedule:
    ids = tuple(sorted(registration_ids))
    if not ids:
        return _Schedule((), (), 0, 0)

    if _is_complete_graph(ids, graph):
        rounds = _complete_graph_rounds(ids, target_dates=target_dates, seed=seed)
        return _summarise_schedule(ids, rounds, target_dates=target_dates)

    bipartition = _complete_bipartite_partitions(ids, graph)
    if bipartition is not None:
        rounds = _complete_bipartite_rounds(
            *bipartition,
            target_dates=target_dates,
            seed=seed,
        )
        return _summarise_schedule(ids, rounds, target_dates=target_dates)

    schedules: list[_Schedule] = []
    for variant in range(3):
        counts = {registration_id: 0 for registration_id in ids}
        used_edges: set[tuple[int, int]] = set()
        rounds: list[ScheduledRound] = []
        for round_number in range(1, target_dates + 1):
            pair_ids = _best_round_matching(
                ids,
                graph,
                counts=counts,
                used_edges=used_edges,
                minimum_dates=minimum_dates,
                target_dates=target_dates,
                seed=f"{seed}:variant:{variant}:round:{round_number}",
            )
            if not pair_ids:
                break
            paired_ids: set[int] = set()
            pairs: list[ScheduledPair] = []
            for left_id, right_id in pair_ids:
                pair = tuple(sorted((left_id, right_id)))
                used_edges.add(pair)
                counts[left_id] += 1
                counts[right_id] += 1
                paired_ids.update(pair)
                pairs.append(ScheduledPair(*pair))
            rounds.append(
                ScheduledRound(
                    number=round_number,
                    pairs=tuple(sorted(pairs, key=_scheduled_pair_key)),
                    break_registration_ids=tuple(sorted(set(ids) - paired_ids)),
                )
            )
        schedules.append(
            _summarise_schedule(ids, tuple(rounds), target_dates=target_dates)
        )

    return max(
        schedules,
        key=lambda schedule: (
            schedule.minimum_dates_achieved,
            tuple(sorted(count for _, count in schedule.date_counts)),
            schedule.members_meeting_target,
            sum(count for _, count in schedule.date_counts),
            _tie_value(seed, "schedule", _schedule_signature(schedule.rounds)),
        ),
    )


def _best_round_matching(
    registration_ids: Sequence[int],
    graph: CompatibilityGraph,
    *,
    counts: Mapping[int, int],
    used_edges: set[tuple[int, int]],
    minimum_dates: int,
    target_dates: int,
    seed: str,
) -> tuple[tuple[int, int], ...]:
    ids = tuple(registration_ids)
    if len(ids) > _EXACT_MATCHING_MEMBER_LIMIT:
        return _bounded_greedy_round_matching(
            ids,
            graph,
            counts=counts,
            used_edges=used_edges,
            minimum_dates=minimum_dates,
            target_dates=target_dates,
            seed=seed,
        )

    index_by_id = {registration_id: index for index, registration_id in enumerate(ids)}
    available_neighbour_masks: list[int] = []
    remaining_degrees: dict[int, int] = {}
    for left_id in ids:
        neighbours = []
        for right_id in graph.neighbours(left_id):
            edge = tuple(sorted((left_id, right_id)))
            if right_id in index_by_id and edge not in used_edges:
                neighbours.append(right_id)
        mask = 0
        for neighbour_id in neighbours:
            mask |= 1 << index_by_id[neighbour_id]
        available_neighbour_masks.append(mask)
        remaining_degrees[left_id] = len(neighbours)

    priorities: list[int] = []
    for registration_id in ids:
        count = counts[registration_id]
        scarcity = max(0, len(ids) - 1 - remaining_degrees[registration_id])
        if count < minimum_dates:
            priority = 1_000_000 + (minimum_dates - count) * 10_000
        elif count < target_dates:
            priority = 10_000 + (target_dates - count) * 100
        else:
            priority = 1
        priorities.append(priority + scarcity)

    @lru_cache(maxsize=None)
    def solve(mask: int):
        if not mask:
            return (0, 0, 0), ()
        first_bit = mask & -mask
        left_index = first_bit.bit_length() - 1
        remaining_mask = mask ^ first_bit
        best_score, best_pairs = solve(remaining_mask)
        possible = available_neighbour_masks[left_index] & remaining_mask
        while possible:
            right_bit = possible & -possible
            right_index = right_bit.bit_length() - 1
            child_score, child_pairs = solve(remaining_mask ^ right_bit)
            left_id, right_id = ids[left_index], ids[right_index]
            pair = tuple(sorted((left_id, right_id)))
            score = (
                child_score[0] + priorities[left_index] + priorities[right_index],
                child_score[1] + 1,
                child_score[2] + _tie_value(seed, "pair", *pair) % 1009,
            )
            pairs = (pair, *child_pairs)
            if score > best_score or (score == best_score and pairs < best_pairs):
                best_score, best_pairs = score, pairs
            possible ^= right_bit
        return best_score, best_pairs

    all_members_mask = (1 << len(ids)) - 1
    _score, pairs = solve(all_members_mask)
    return tuple(sorted(pairs))


def _bounded_greedy_round_matching(
    registration_ids: Sequence[int],
    graph: CompatibilityGraph,
    *,
    counts: Mapping[int, int],
    used_edges: set[tuple[int, int]],
    minimum_dates: int,
    target_dates: int,
    seed: str,
) -> tuple[tuple[int, int], ...]:
    """Polynomial deterministic matching fallback for unusually large groups.

    Exact bitmask matching is bounded to ``_EXACT_MATCHING_MEMBER_LIMIT``.
    Above it, this produces a maximal priority-weighted matching and applies
    deterministic length-three augmenting paths. The surrounding scheduler
    still rejects the group unless the resulting concrete rounds prove the
    minimum guarantee, so the fallback trades completeness for a hard runtime
    bound, never correctness.
    """

    ids = tuple(registration_ids)
    id_set = set(ids)
    available: dict[int, set[int]] = {}
    for registration_id in ids:
        available[registration_id] = {
            neighbour_id
            for neighbour_id in graph.neighbours(registration_id) & id_set
            if tuple(sorted((registration_id, neighbour_id))) not in used_edges
        }

    def priority(registration_id: int) -> tuple[int, int, int]:
        count = counts[registration_id]
        if count < minimum_dates:
            need = 2
            deficit = minimum_dates - count
        elif count < target_dates:
            need = 1
            deficit = target_dates - count
        else:
            need = 0
            deficit = 0
        return (
            need,
            deficit,
            -len(available[registration_id]),
        )

    order = sorted(
        ids,
        key=lambda registration_id: (
            priority(registration_id),
            _tie_value(seed, "large-member", registration_id),
            -registration_id,
        ),
        reverse=True,
    )
    partner: dict[int, int] = {}
    for left_id in order:
        if left_id in partner:
            continue
        choices = available[left_id] - partner.keys()
        if not choices:
            continue
        right_id = max(
            choices,
            key=lambda registration_id: (
                priority(registration_id),
                _tie_value(seed, "large-pair", left_id, registration_id),
                -registration_id,
            ),
        )
        partner[left_id] = right_id
        partner[right_id] = left_id

    # Repair a maximal matching with deterministic u-v / w-x augmentations.
    # Each successful path increases cardinality by one; at most n/2 succeed,
    # and every search is O(n^3), keeping the whole fallback polynomial.
    while True:
        augmented = False
        unmatched = [
            registration_id
            for registration_id in order
            if registration_id not in partner
        ]
        unmatched_set = set(unmatched)
        for left_id in unmatched:
            for middle_id in sorted(
                available[left_id] & partner.keys(),
                key=lambda registration_id: (
                    priority(registration_id),
                    _tie_value(seed, "augment-middle", left_id, registration_id),
                ),
                reverse=True,
            ):
                displaced_id = partner[middle_id]
                endpoints = available[displaced_id] & unmatched_set - {
                    left_id,
                    middle_id,
                    displaced_id,
                }
                if not endpoints:
                    continue
                endpoint_id = max(
                    endpoints,
                    key=lambda registration_id: (
                        priority(registration_id),
                        _tie_value(
                            seed,
                            "augment-end",
                            displaced_id,
                            registration_id,
                        ),
                    ),
                )
                del partner[middle_id]
                del partner[displaced_id]
                partner[left_id] = middle_id
                partner[middle_id] = left_id
                partner[displaced_id] = endpoint_id
                partner[endpoint_id] = displaced_id
                augmented = True
                break
            if augmented:
                break
        if not augmented:
            break

    pairs = {
        tuple(sorted((left_id, right_id))) for left_id, right_id in partner.items()
    }
    return tuple(sorted(pairs))


def _is_complete_graph(
    registration_ids: Sequence[int], graph: CompatibilityGraph
) -> bool:
    member_set = set(registration_ids)
    return all(
        len(graph.neighbours(registration_id) & member_set) == len(member_set) - 1
        for registration_id in registration_ids
    )


def _complete_graph_rounds(
    registration_ids: Sequence[int], *, target_dates: int, seed: str
) -> tuple[ScheduledRound, ...]:
    ordered: list[int | None] = sorted(
        registration_ids,
        key=lambda registration_id: (
            _tie_value(seed, "round-robin", registration_id),
            registration_id,
        ),
    )
    if len(ordered) % 2:
        ordered.append(None)
    rounds: list[ScheduledRound] = []
    for round_number in range(1, min(target_dates, len(ordered) - 1) + 1):
        pairs: list[ScheduledPair] = []
        breaks: list[int] = []
        for index in range(len(ordered) // 2):
            left = ordered[index]
            right = ordered[-index - 1]
            if left is None:
                if right is not None:
                    breaks.append(right)
                continue
            if right is None:
                breaks.append(left)
                continue
            pair = tuple(sorted((left, right)))
            pairs.append(ScheduledPair(*pair))
        rounds.append(
            ScheduledRound(
                number=round_number,
                pairs=tuple(sorted(pairs, key=_scheduled_pair_key)),
                break_registration_ids=tuple(sorted(breaks)),
            )
        )
        ordered = [ordered[0], ordered[-1], *ordered[1:-1]]
    return tuple(rounds)


def _complete_bipartite_partitions(
    registration_ids: Sequence[int], graph: CompatibilityGraph
) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    ids = tuple(registration_ids)
    if len(ids) < 2:
        return None
    id_set = set(ids)
    colours: dict[int, int] = {}
    for start in ids:
        if start in colours:
            continue
        colours[start] = 0
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbour in graph.neighbours(current) & id_set:
                expected = 1 - colours[current]
                if neighbour in colours:
                    if colours[neighbour] != expected:
                        return None
                    continue
                colours[neighbour] = expected
                stack.append(neighbour)
    left = tuple(sorted(key for key, colour in colours.items() if colour == 0))
    right = tuple(sorted(key for key, colour in colours.items() if colour == 1))
    if not left or not right:
        return None
    if any(
        not graph.has_edge(left_id, right_id) for left_id in left for right_id in right
    ):
        return None
    if any(
        graph.has_edge(side[index], side[later])
        for side in (left, right)
        for index in range(len(side))
        for later in range(index + 1, len(side))
    ):
        return None
    return left, right


def _complete_bipartite_rounds(
    left_ids: Sequence[int],
    right_ids: Sequence[int],
    *,
    target_dates: int,
    seed: str,
) -> tuple[ScheduledRound, ...]:
    if len(left_ids) > len(right_ids):
        left_ids, right_ids = right_ids, left_ids
    left = tuple(sorted(left_ids, key=lambda key: (_tie_value(seed, "left", key), key)))
    right = tuple(
        sorted(right_ids, key=lambda key: (_tie_value(seed, "right", key), key))
    )
    all_ids = set(left) | set(right)
    rounds: list[ScheduledRound] = []
    for round_index in range(min(target_dates, len(right))):
        pairs = []
        paired_ids: set[int] = set()
        for left_index, left_id in enumerate(left):
            right_id = right[(left_index + round_index) % len(right)]
            pair = tuple(sorted((left_id, right_id)))
            pairs.append(ScheduledPair(*pair))
            paired_ids.update(pair)
        rounds.append(
            ScheduledRound(
                number=round_index + 1,
                pairs=tuple(sorted(pairs, key=_scheduled_pair_key)),
                break_registration_ids=tuple(sorted(all_ids - paired_ids)),
            )
        )
    return tuple(rounds)


def _scheduled_pair_key(pair: ScheduledPair) -> tuple[int, int]:
    return pair.registration_a_id, pair.registration_b_id


def _summarise_schedule(
    registration_ids: Sequence[int],
    rounds: Sequence[ScheduledRound],
    *,
    target_dates: int,
) -> _Schedule:
    counts = {registration_id: 0 for registration_id in registration_ids}
    for round_ in rounds:
        for pair in round_.pairs:
            counts[pair.registration_a_id] += 1
            counts[pair.registration_b_id] += 1
    date_counts = tuple(sorted(counts.items()))
    return _Schedule(
        rounds=tuple(rounds),
        date_counts=date_counts,
        minimum_dates_achieved=min(counts.values(), default=0),
        members_meeting_target=sum(count >= target_dates for count in counts.values()),
    )


def _schedule_signature(rounds: Sequence[ScheduledRound]) -> str:
    return ";".join(
        ",".join(
            f"{pair.registration_a_id}-{pair.registration_b_id}"
            for pair in round_.pairs
        )
        for round_ in rounds
    )


def _select_candidate_plan(
    candidates: Sequence[_GroupCandidate],
    registration_ids: Sequence[int],
    *,
    pinned_ids: frozenset[int],
    group_limit: int,
    target_dates: int,
    seed: str,
) -> tuple[_GroupCandidate, ...]:
    if not candidates or group_limit < 1:
        return ()

    index_by_id = {
        registration_id: index for index, registration_id in enumerate(registration_ids)
    }
    masks = {
        candidate: sum(
            1 << index_by_id[registration_id]
            for registration_id in candidate.registration_ids
        )
        for candidate in candidates
    }
    pinned_mask = sum(1 << index_by_id[key] for key in pinned_ids)
    candidates_by_track: dict[str, list[_GroupCandidate]] = {}
    for candidate in candidates:
        candidates_by_track.setdefault(candidate.track_id, []).append(candidate)

    # Tracks are disjoint connected components, so solve set-packing locally
    # and combine only the best local plan for each feasible group count. This
    # avoids multiplying every membership alternative from one component by
    # every alternative in another (50 ten-person tracks previously produced
    # more than 2,000 candidates across 50 x 512 global beam buckets).
    track_options: list[tuple[str, tuple[tuple[int, _PlanState], ...]]] = []
    for track_id in sorted(candidates_by_track):
        track_candidates = tuple(candidates_by_track[track_id])
        track_members = {
            registration_id
            for candidate in track_candidates
            for registration_id in candidate.registration_ids
        }
        minimum_candidate_size = min(
            len(candidate.registration_ids) for candidate in track_candidates
        )
        local_group_limit = min(
            group_limit,
            len(track_members) // minimum_candidate_size,
        )
        options = _best_local_track_plans(
            track_candidates,
            masks=masks,
            pinned_mask=pinned_mask,
            group_limit=local_group_limit,
            target_dates=target_dates,
            seed=f"{seed}:{track_id}",
        )
        track_options.append((track_id, options))

    beams: dict[int, list[_PlanState]] = {0: [_PlanState(member_mask=0, candidates=())]}
    for _track_id, options in track_options:
        combined: dict[int, list[_PlanState]] = {
            count: list(states) for count, states in beams.items()
        }
        for current_count, states in beams.items():
            for option_count, option in options:
                total_count = current_count + option_count
                if total_count > group_limit:
                    continue
                destination = combined.setdefault(total_count, [])
                for state in states:
                    # Components are disjoint by construction. Keep the check
                    # as an invariant backstop if track generation ever changes.
                    if state.member_mask & option.member_mask:
                        continue
                    destination.append(
                        _PlanState(
                            member_mask=state.member_mask | option.member_mask,
                            candidates=(*state.candidates, *option.candidates),
                        )
                    )
        beams = {
            count: _prune_plan_states(
                states,
                pinned_mask=pinned_mask,
                target_dates=target_dates,
                seed=seed,
                limit=_GLOBAL_TRACK_PLAN_BEAM_WIDTH,
            )
            for count, states in combined.items()
            if states
        }

    finalists = [state for states in beams.values() for state in states]
    if not finalists:
        return ()
    best = max(
        finalists,
        key=lambda state: _plan_score(
            state,
            pinned_mask=pinned_mask,
            target_dates=target_dates,
            seed=seed,
        ),
    )
    return best.candidates


def _best_local_track_plans(
    candidates: Sequence[_GroupCandidate],
    *,
    masks: Mapping[_GroupCandidate, int],
    pinned_mask: int,
    group_limit: int,
    target_dates: int,
    seed: str,
) -> tuple[tuple[int, _PlanState], ...]:
    if group_limit < 1:
        return ()
    ordered_candidates = tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -len(candidate.pinned_registration_ids),
                -len(candidate.registration_ids),
                _tie_value(seed, "candidate", *candidate.registration_ids),
            ),
        )
    )
    beams: dict[int, list[_PlanState]] = {count: [] for count in range(group_limit + 1)}
    beams[0] = [_PlanState(member_mask=0, candidates=())]
    for candidate in ordered_candidates:
        previous = {count: tuple(states) for count, states in beams.items()}
        candidate_mask = masks[candidate]
        for count in range(1, group_limit + 1):
            expanded = list(beams[count])
            for state in previous[count - 1]:
                if state.member_mask & candidate_mask:
                    continue
                expanded.append(
                    _PlanState(
                        member_mask=state.member_mask | candidate_mask,
                        candidates=(*state.candidates, candidate),
                    )
                )
            beams[count] = _prune_plan_states(
                expanded,
                pinned_mask=pinned_mask,
                target_dates=target_dates,
                seed=seed,
                limit=_LOCAL_PLAN_BEAM_WIDTH,
            )

    # For disjoint tracks, every objective before the seeded final tie is
    # monotone under union with the same outside plan. One locally best state
    # per exact group count is therefore enough for the global composition.
    return tuple(
        (count, states[0])
        for count, states in sorted(beams.items())
        if count and states
    )


def _prune_plan_states(
    states: Sequence[_PlanState],
    *,
    pinned_mask: int,
    target_dates: int,
    seed: str,
    limit: int,
) -> list[_PlanState]:
    unique: dict[tuple[int, tuple[tuple[int, ...], ...]], _PlanState] = {}
    for state in states:
        key = (
            state.member_mask,
            tuple(sorted(candidate.registration_ids for candidate in state.candidates)),
        )
        unique[key] = state
    return sorted(
        unique.values(),
        key=lambda state: _plan_score(
            state,
            pinned_mask=pinned_mask,
            target_dates=target_dates,
            seed=seed,
        ),
        reverse=True,
    )[:limit]


def _plan_score(
    state: _PlanState,
    *,
    pinned_mask: int,
    target_dates: int,
    seed: str,
):
    counts = tuple(
        sorted(
            count
            for candidate in state.candidates
            for _registration_id, count in candidate.schedule.date_counts
        )
    )
    tracks = {candidate.track_id for candidate in state.candidates}
    members_meeting_target = sum(
        candidate.schedule.members_meeting_target for candidate in state.candidates
    )
    total_dates = sum(counts)
    resilience_potential = sum(
        candidate.resilience_potential for candidate in state.candidates
    )
    scarcity_coverage = sum(candidate.scarcity_score for candidate in state.candidates)
    signature = ";".join(
        ",".join(str(key) for key in candidate.registration_ids)
        for candidate in sorted(
            state.candidates, key=lambda candidate: candidate.registration_ids
        )
    )
    return (
        (state.member_mask & pinned_mask).bit_count(),
        len(tracks),
        scarcity_coverage,
        state.member_mask.bit_count(),
        counts,
        members_meeting_target,
        total_dates,
        resilience_potential,
        _tie_value(seed, "plan", target_dates, signature),
    )


def _fair_group_order(
    candidates: Sequence[_GroupCandidate], *, seed: str
) -> tuple[_GroupCandidate, ...]:
    by_track: dict[str, list[_GroupCandidate]] = {}
    for candidate in candidates:
        by_track.setdefault(candidate.track_id, []).append(candidate)
    for track_candidates in by_track.values():
        track_candidates.sort(
            key=lambda candidate: (
                -len(candidate.pinned_registration_ids),
                -len(candidate.registration_ids),
                -candidate.scarcity_score,
                -candidate.schedule.minimum_dates_achieved,
                _tie_value(seed, "group-order", *candidate.registration_ids),
            )
        )

    ordered: list[_GroupCandidate] = []
    maximum_depth = max((len(groups) for groups in by_track.values()), default=0)
    track_order = sorted(
        by_track,
        key=lambda track_id: (
            -by_track[track_id][0].track_size,
            _tie_value(seed, "track-order", track_id),
        ),
    )
    for ordinal in range(maximum_depth):
        for track_id in track_order:
            track_candidates = by_track[track_id]
            if ordinal < len(track_candidates):
                ordered.append(track_candidates[ordinal])
    return tuple(ordered)


def _is_one_drop_resilient(
    registration_ids: Sequence[int],
    graph: CompatibilityGraph,
    *,
    minimum_dates: int,
    target_dates: int,
    seed: str,
    schedule_cache: dict[tuple[int, ...], _Schedule],
) -> bool:
    if len(registration_ids) - 1 < minimum_dates + 1:
        return False
    for dropped_id in registration_ids:
        remaining = tuple(
            registration_id
            for registration_id in registration_ids
            if registration_id != dropped_id
        )
        schedule = schedule_cache.get(remaining)
        if schedule is None:
            schedule = _build_schedule(
                remaining,
                graph,
                minimum_dates=minimum_dates,
                target_dates=target_dates,
                seed=f"{seed}:{dropped_id}",
            )
            schedule_cache[remaining] = schedule
        if schedule.minimum_dates_achieved < minimum_dates:
            return False
    return True
