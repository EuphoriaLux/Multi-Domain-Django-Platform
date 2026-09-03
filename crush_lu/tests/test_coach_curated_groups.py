"""Coach "Groups" panel on the coach event page (curated speed dating).

The panel is read-only: it names the stage of the evening and the next admin
action, previews what the projector would do with the pool, mirrors the
stored generation (members, dates, fairness audit, pairing schedule) and
lists who is left out and why. Nothing here changes a group.

Paths use ``reverse()`` under the crush.lu urlconf, like the workflow
integration tests, because the coach page lives behind i18n_patterns there.
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CrushCoach
from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedEventPairing,
    EventRegistration,
    EventRegistrationPreference,
    MeetupEvent,
)
from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services.curated_group_insights import (
    INELIGIBILITY_LABELS,
    NEXT_ACTION_LABELS,
    coach_group_panel,
)
from crush_lu.services.curated_group_workflow import (
    approve_current_generation,
    generate_group_projection,
    lock_current_generation,
    start_curated_rounds,
)
from crush_lu.services.event_grouping import project_event_groups

User = get_user_model()

PANEL_MARKERS = (
    "data-curated-groups-panel",
    "data-curated-groups-tab",
    "Why this group",
    "Show pairing schedule",
    "Would be left out",
    "Cannot be placed",
    "Eligible but not placed",
    "Next step:",
    "Open the coach panel",
)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CoachCuratedGroupsPanelTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.coach = self.make_user("coach@example.com", first_name="Cam")
        CrushCoach.objects.create(user=self.coach, is_active=True)
        self.client.force_login(self.coach)

    # -- fixtures -----------------------------------------------------------

    def make_user(self, email, *, first_name):
        user = User.objects.create_user(
            username=email, email=email, password="testpass123", first_name=first_name
        )
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        return user

    def make_event(self, **overrides):
        values = {
            "title": "Elastic evening",
            "description": "A fair parallel speed-dating evening",
            "event_type": "speed_dating",
            "registration_mode": "curated",
            "date_time": timezone.now() + timedelta(days=7),
            "registration_deadline": timezone.now() - timedelta(hours=1),
            "location": "Luxembourg",
            "address": "Test venue",
            "max_participants": 12,
            "group_size": 6,
            "planned_groups": 1,
            "registration_fee": Decimal("15.00"),
            "profile_requirement": "none",
            "is_published": True,
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def make_applicant(
        self,
        event,
        index,
        *,
        gender="NB",
        date_of_birth=date(1995, 1, 1),
        with_preference=True,
        status="applied",
    ):
        user = self.make_user(
            f"member-{event.pk}-{index}@example.com", first_name=f"Member {index}"
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date_of_birth,
            gender=gender,
            location="Luxembourg",
            event_languages=["en"],
        )
        registration = EventRegistration.objects.create(
            event=event, user=user, status=status
        )
        if with_preference:
            EventRegistrationPreference.objects.create(
                registration=registration,
                preferred_genders=[],
                preferred_age_min=18,
                preferred_age_max=99,
                languages=[],
            )
        return registration

    def make_applicants(self, event, count=6):
        first = EventRegistration.objects.filter(event=event).count()
        return [
            self.make_applicant(event, index) for index in range(first, first + count)
        ]

    def certify(self, event):
        self.make_applicants(event)
        stored = generate_group_projection(event, deterministic_seed="coach-panel")
        approved = approve_current_generation(event)
        EventRegistration.objects.filter(
            pk__in=approved.applied_registration_ids
        ).update(status="pending")
        return CuratedEventGroup.objects.get(pk=stored.group_ids[0])

    @staticmethod
    def stable_html(content):
        html = content.decode("utf-8")
        html = re.sub(r'nonce="[^"]+"', 'nonce="X"', html)
        html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "csrf", html)
        return re.sub(r"\?status=\w+", "", html)

    def page(self, event, status=None, language="en"):
        # reverse() prefixes the language that is active in this process,
        # which the previous request may have left at fr or de.
        url = re.sub(
            r"^/[a-z]{2}/",
            f"/{language}/",
            reverse("crush_lu:coach_event_detail", args=[event.pk]),
        )
        if status:
            url = f"{url}?status={status}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response

    # -- gating -------------------------------------------------------------

    def test_direct_event_is_unchanged(self):
        event = self.make_event(
            registration_mode="direct", group_size=None, planned_groups=None
        )
        EventRegistration.objects.create(
            event=event,
            user=self.make_user("direct@example.com", first_name="Dee"),
            status="confirmed",
        )

        default = self.page(event)
        groups_tab = self.page(event, status="groups")
        unknown_tab = self.page(event, status="bogus")

        self.assertIsNone(default.context["curated_groups_panel"])
        self.assertEqual(groups_tab.context["status_filter"], "all")
        # "groups" is just another unknown filter on a direct event. The only
        # legitimate differences between the two renders are the per-request
        # CSP nonce, the CSRF token and the query string echoed into form
        # actions.
        self.assertEqual(
            self.stable_html(groups_tab.content), self.stable_html(unknown_tab.content)
        )
        for marker in PANEL_MARKERS:
            self.assertNotContains(default, marker)

    def test_legacy_curated_event_without_group_size_shows_nothing_new(self):
        event = self.make_event(group_size=None, planned_groups=None)
        self.make_applicants(event, 3)

        response = self.page(event)

        self.assertIsNone(response.context["curated_groups_panel"])
        self.assertContains(response, "awaiting selection")
        for marker in PANEL_MARKERS:
            self.assertNotContains(response, marker)

    def test_groups_tab_renders_only_the_panel(self):
        event = self.make_event()
        self.make_applicants(event)

        response = self.page(event, status="groups")

        self.assertEqual(response.context["status_filter"], "groups")
        self.assertContains(response, "data-curated-groups-panel")
        self.assertContains(response, "data-curated-groups-tab")
        self.assertNotContains(response, "awaiting selection")

    # -- stages -------------------------------------------------------------

    def test_stage_none_before_deadline_waits_and_previews_the_pool(self):
        event = self.make_event(
            registration_deadline=timezone.now() + timedelta(days=2)
        )
        self.make_applicants(event)

        response = self.page(event)
        panel = response.context["curated_groups_panel"]

        self.assertEqual(panel["stage"], "none")
        self.assertEqual(panel["next_action"], "wait_for_deadline")
        self.assertEqual(
            panel["preflight"],
            {
                "applications": 6,
                "eligible": 6,
                "viable_groups": 1,
                "left_out": 0,
                "error": None,
                "stale": False,
            },
        )
        self.assertTrue(panel["left_out"]["preview"])
        self.assertContains(response, 'data-curated-stage="none"')
        self.assertContains(response, "No groups generated yet")
        self.assertContains(response, "Applications close ")
        self.assertContains(response, NEXT_ACTION_LABELS["wait_for_deadline"])
        self.assertContains(response, "Would be left out")
        self.assertContains(response, "Nobody is left out.")
        self.assertContains(
            response, "/crush-admin/crush_lu/meetupevent/?q=Elastic+evening"
        )
        self.assertNotContains(response, "data-curated-group=")

    def test_stage_none_after_deadline_explains_who_would_be_left_out(self):
        event = self.make_event(min_age=25, max_age=45)
        self.make_applicants(event, 7)
        no_preference = self.make_applicant(event, 7, with_preference=False)
        no_birthday = self.make_applicant(event, 8, date_of_birth=None)
        too_young = self.make_applicant(
            event, 9, date_of_birth=date.today() - timedelta(days=365 * 21)
        )
        no_gender = self.make_applicant(event, 10, gender="")

        response = self.page(event)
        panel = response.context["curated_groups_panel"]

        self.assertEqual(panel["stage"], "none")
        self.assertEqual(panel["next_action"], "generate")
        self.assertContains(response, NEXT_ACTION_LABELS["generate"])
        self.assertEqual(panel["preflight"]["applications"], 11)
        self.assertEqual(panel["preflight"]["eligible"], 7)
        self.assertEqual(panel["preflight"]["viable_groups"], 1)
        self.assertEqual(panel["preflight"]["left_out"], 5)

        blocked = {
            p["registration_id"]: p["reasons"] for p in panel["left_out"]["blocked"]
        }
        self.assertEqual(
            blocked,
            {
                no_preference.pk: [INELIGIBILITY_LABELS["missing_event_preferences"]],
                no_birthday.pk: [INELIGIBILITY_LABELS["missing_age"]],
                too_young.pk: [INELIGIBILITY_LABELS["outside_event_age_range"]],
                no_gender.pk: [INELIGIBILITY_LABELS["missing_gender"]],
            },
        )
        # Seven eligible, six seats: exactly one eligible person is not placed.
        self.assertEqual(len(panel["left_out"]["eligible"]), 1)
        self.assertContains(response, "Cannot be placed")
        self.assertContains(response, "Eligible but not placed")
        self.assertContains(response, "no event preferences on the application")
        self.assertContains(response, "no date of birth on the profile")
        self.assertContains(response, "outside the event&#x27;s age range")
        self.assertContains(response, "no gender on the profile")

    def test_left_out_reasons_match_the_projection(self):
        """The panel reads the projector's own eligibility rule; pin that it
        never drifts from what a stored generation would record."""
        event = self.make_event(min_age=25, max_age=45)
        self.make_applicants(event, 6)
        self.make_applicant(event, 6, with_preference=False)
        self.make_applicant(event, 7, date_of_birth=None, gender="")
        self.make_applicant(
            event, 8, date_of_birth=date.today() - timedelta(days=365 * 21)
        )

        panel = coach_group_panel(event)
        projection = project_event_groups(event)

        expected = {
            registration_id: [INELIGIBILITY_LABELS[code] for code in codes]
            for registration_id, codes in projection.ineligibility_reasons
        }
        self.assertEqual(
            {p["registration_id"]: p["reasons"] for p in panel["left_out"]["blocked"]},
            expected,
        )
        self.assertEqual(len(expected), 3)

    def test_draft_stage_mirrors_the_stored_generation(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        stored = generate_group_projection(event, deterministic_seed="coach-panel")
        group = CuratedEventGroup.objects.get(pk=stored.group_ids[0])

        response = self.page(event)
        panel = response.context["curated_groups_panel"]

        self.assertEqual(panel["stage"], "draft")
        self.assertEqual(panel["generation"], group.generation)
        self.assertEqual(panel["next_action"], "approve")
        self.assertFalse(panel["preflight"]["stale"])
        self.assertContains(response, 'data-curated-stage="draft"')
        self.assertContains(response, "is a draft")
        self.assertContains(response, NEXT_ACTION_LABELS["approve"])
        self.assertContains(response, 'data-curated-group="1"')
        self.assertContains(response, "Group 1")

        [card] = panel["groups"]
        self.assertEqual(card["status"], "draft")
        self.assertEqual(card["active_count"], 6)
        self.assertEqual(card["rounds"], group.viability_summary["rounds"])
        self.assertEqual(
            card["minimum_dates"], group.viability_summary["minimum_dates"]
        )
        fairness = group.audit_data["fairness_decision"]
        # Six people give five dates each, so the seven-date target is out of
        # reach and the card must say so rather than round it up.
        self.assertFalse(card["target_achieved"])
        self.assertEqual(card["members_meeting_target"], 0)
        self.assertEqual(card["target_dates"], 7)
        self.assertEqual(card["projected_size"], 6)
        self.assertEqual(card["one_drop_resilient"], fairness["one_drop_resilient"])
        self.assertEqual(
            {m["registration_id"]: m["dates"] for m in card["members"]},
            {r.pk: 5 for r in registrations},
        )
        for registration in registrations:
            self.assertContains(response, registration.user.first_name)
        self.assertContains(response, "5 mini-dates")
        self.assertContains(response, "5 rounds")
        self.assertContains(response, "0 of 6 meet the 7-date target")
        self.assertContains(response, "at least 5 dates each")
        self.assertNotContains(response, "everyone meets the 7-date target")
        self.assertContains(response, "Why this group")
        self.assertContains(response, "compatibility track of their own")
        self.assertContains(response, "Applied — awaiting selection")
        self.assertContains(response, "Nobody is left out.")
        self.assertFalse(panel["left_out"]["preview"])

    def test_draft_goes_stale_when_the_pool_changes(self):
        event = self.make_event()
        self.make_applicants(event)
        generate_group_projection(event, deterministic_seed="coach-panel")
        self.make_applicants(event, 1)

        response = self.page(event)
        panel = response.context["curated_groups_panel"]

        self.assertTrue(panel["preflight"]["stale"])
        self.assertEqual(panel["next_action"], "regenerate")
        self.assertContains(response, "Draft is stale")
        self.assertContains(response, NEXT_ACTION_LABELS["regenerate"])
        # The newcomer holds no place in the stored draft.
        self.assertEqual(len(panel["left_out"]["eligible"]), 1)
        self.assertContains(response, "Eligible but not placed")

    def test_draft_goes_stale_when_the_deadline_moves_past_it(self):
        event = self.make_event()
        self.make_applicants(event)
        generate_group_projection(event, deterministic_seed="coach-panel")
        MeetupEvent.objects.filter(pk=event.pk).update(
            registration_deadline=timezone.now() + timedelta(days=1)
        )

        panel = self.page(event).context["curated_groups_panel"]

        self.assertTrue(panel["preflight"]["stale"])
        self.assertEqual(panel["next_action"], "regenerate")

    def test_provisional_stage_says_invite_then_check_in(self):
        event = self.make_event()
        self.make_applicants(event)
        generate_group_projection(event, deterministic_seed="coach-panel")
        approve_current_generation(event)

        response = self.page(event)
        panel = response.context["curated_groups_panel"]
        self.assertEqual(panel["stage"], "provisional")
        self.assertEqual(panel["next_action"], "invite")
        self.assertIsNone(panel["preflight"])
        self.assertContains(response, "is provisional: selected and payable")
        self.assertContains(response, NEXT_ACTION_LABELS["invite"])
        self.assertContains(response, ">Provisional<")
        self.assertNotContains(response, "data-curated-preflight")

        EventRegistration.objects.filter(event=event).update(status="pending")
        response = self.page(event)
        panel = response.context["curated_groups_panel"]
        self.assertEqual(panel["next_action"], "check_in_and_lock")
        self.assertContains(response, NEXT_ACTION_LABELS["check_in_and_lock"])
        self.assertContains(response, "Pending Payment")

    def test_locked_and_started_stages_never_run_the_projector(self):
        event = self.make_event()
        group = self.certify(event)
        EventRegistration.objects.filter(event=event).update(
            status="attended", payment_confirmed=True, payment_date=timezone.now()
        )
        MeetupEvent.objects.filter(pk=event.pk).update(
            date_time=timezone.now() - timedelta(minutes=10)
        )
        event.refresh_from_db()
        lock_current_generation(event, actor=self.coach)

        with patch(
            "crush_lu.services.curated_group_insights.project_event_groups",
            side_effect=AssertionError("projector must not run after approval"),
        ):
            response = self.page(event)
            panel = response.context["curated_groups_panel"]
            self.assertEqual(panel["stage"], "locked")
            self.assertEqual(panel["next_action"], "start")
            self.assertContains(response, "is locked: final evening roster")
            self.assertContains(response, NEXT_ACTION_LABELS["start"])
            self.assertContains(response, ">Locked<")
            self.assertContains(response, ">Attended<")

            start_curated_rounds(event, actor=self.coach)
            response = self.page(event)
            panel = response.context["curated_groups_panel"]
            self.assertEqual(panel["stage"], "started")
            self.assertEqual(panel["next_action"], "delivered")
            self.assertTrue(panel["rounds_started"])
            self.assertContains(response, "Round one has started")
            self.assertContains(response, NEXT_ACTION_LABELS["delivered"])

        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_LOCKED)

    def test_degraded_stage_shows_released_members_struck_through(self):
        event = self.make_event()
        group = self.certify(event)
        dropped = group.memberships.order_by("position").first()
        with transaction.atomic():
            locked = CuratedEventGroup.objects.select_for_update().get(pk=group.pk)
            locked._mark_degraded_locked(
                reason=CuratedEventGroup.DEGRADATION_REASON_ORGANISER
            )
        CuratedEventGroupMembership.objects.filter(pk=dropped.pk).update(
            released_at=timezone.now(), release_reason="test"
        )

        response = self.page(event)
        panel = response.context["curated_groups_panel"]

        self.assertEqual(panel["stage"], "degraded")
        self.assertEqual(panel["next_action"], "repair")
        self.assertContains(response, 'data-curated-stage="degraded"')
        self.assertContains(response, "is degraded and needs repair")
        self.assertContains(response, NEXT_ACTION_LABELS["repair"])
        self.assertContains(response, "was provisional")
        [card] = panel["groups"]
        self.assertEqual(card["status"], "degraded")
        self.assertEqual(card["degraded_from"], "provisional")
        self.assertEqual(card["active_count"], 5)
        released = [m for m in card["members"] if m["released"]]
        self.assertEqual([m["registration_id"] for m in released], [dropped.pk])
        # Active members come first; the released one closes the list.
        self.assertTrue(card["members"][-1]["released"])
        self.assertContains(response, "line-through")
        self.assertContains(response, ">Released<")
        # The released member holds no place: they are listed as left out.
        self.assertEqual(
            [p["registration_id"] for p in panel["left_out"]["eligible"]],
            [dropped.pk],
        )

    def test_cancelled_event_names_no_action(self):
        event = self.make_event(is_cancelled=True)
        self.make_applicants(event)

        panel = self.page(event).context["curated_groups_panel"]

        self.assertEqual(panel["next_action"], "cancelled")
        self.assertTrue(panel["event_cancelled"])

    # -- roster details -----------------------------------------------------

    def test_left_out_after_generation_lists_the_unplaced_with_reasons(self):
        event = self.make_event()
        self.make_applicants(event, 7)
        blocked = self.make_applicant(event, 7, with_preference=False)
        generate_group_projection(event, deterministic_seed="coach-panel")

        response = self.page(event)
        panel = response.context["curated_groups_panel"]

        self.assertFalse(panel["left_out"]["preview"])
        self.assertEqual(
            [p["registration_id"] for p in panel["left_out"]["blocked"]],
            [blocked.pk],
        )
        self.assertEqual(len(panel["left_out"]["eligible"]), 1)
        placed = {m["registration_id"] for m in panel["groups"][0]["members"]}
        self.assertNotIn(panel["left_out"]["eligible"][0]["registration_id"], placed)
        self.assertContains(response, "Left out")
        self.assertContains(response, "Cannot be placed")
        self.assertContains(response, "no event preferences on the application")
        self.assertContains(response, "Eligible but not placed")

    def test_pairing_schedule_is_collapsed_and_names_both_seats(self):
        event = self.make_event()
        self.make_applicants(event)
        stored = generate_group_projection(event, deterministic_seed="coach-panel")
        pairings = list(
            CuratedEventPairing.objects.filter(group_id=stored.group_ids[0])
            .prefetch_related("participants__registration__user")
            .order_by("round_number", "table_number")
        )

        response = self.page(event)
        [card] = response.context["curated_groups_panel"]["groups"]

        self.assertContains(response, "<details")
        self.assertContains(response, "Show pairing schedule")
        self.assertContains(response, "Table 1")
        self.assertContains(response, "Sitting out")
        cells = [
            (round_["round"], table["table"], {table["a"], table["b"]})
            for round_ in card["schedule"]
            for table in round_["tables"]
        ]
        expected = [
            (
                pairing.round_number,
                pairing.table_number,
                {p.registration.user.first_name for p in pairing.participants.all()},
            )
            for pairing in pairings
        ]
        self.assertEqual(cells, expected)
        # Six people, three tables: nobody sits out in any round.
        self.assertTrue(all(not round_["sitting_out"] for round_ in card["schedule"]))
        self.assertContains(response, "↔", count=len(pairings))

    def test_projector_size_error_is_shown_not_raised(self):
        event = self.make_event(group_size=42, max_participants=42)
        self.make_applicants(event, 2)

        with patch(
            "crush_lu.services.curated_group_insights.project_event_groups",
            side_effect=__import__(
                "crush_lu.services.event_grouping", fromlist=["GroupingPoolTooLarge"]
            ).GroupingPoolTooLarge("too big"),
        ):
            response = self.page(event)

        panel = response.context["curated_groups_panel"]
        self.assertEqual(panel["preflight"]["error"], "too big")
        self.assertIsNone(panel["preflight"]["viable_groups"])
        self.assertContains(response, "The projector refused this pool: too big")

    # -- privacy and i18n ---------------------------------------------------

    def test_member_page_never_gains_the_coach_panel(self):
        event = self.make_event()
        group = self.certify(event)
        members = [
            m.registration
            for m in group.memberships.select_related("registration__user")
        ]
        viewer, *others = members
        self.client.force_login(viewer.user)

        response = self.client.get(f"/en/events/{event.pk}/")

        self.assertEqual(response.status_code, 200)
        for marker in PANEL_MARKERS:
            self.assertNotContains(response, marker)
        for other in others:
            self.assertNotContains(response, other.user.first_name)
            self.assertNotContains(response, other.user.email)

    def test_banner_and_reasons_are_localized(self):
        event = self.make_event()
        self.make_applicants(event, 5)
        self.make_applicant(event, 5, with_preference=False)

        expectations = {
            "fr": (
                "Aucun groupe généré pour l’instant",
                "Lancez « Generate fair curated groups »",
                "aucune préférence d’événement dans la candidature",
                "Ne peut pas être placé·e",
            ),
            "de": (
                "Noch keine Gruppen erstellt",
                "Führe „Generate fair curated groups“ aus",
                "keine Event-Präferenzen in der Bewerbung",
                "Kann nicht platziert werden",
            ),
        }
        for language, phrases in expectations.items():
            with self.subTest(language=language):
                response = self.page(event, language=language)
                for phrase in phrases:
                    self.assertContains(response, phrase)
