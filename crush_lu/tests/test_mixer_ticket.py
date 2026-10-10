"""Social Mixer "Find your number" check-in ticket."""

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import CrushProfile, EventRegistration, Interest, MeetupEvent
from crush_lu.services.mixer_ticket import (
    LIST_SIZE,
    affinity_list,
    assign_event_numbers,
    event_numbers,
)
from crush_lu.services.ticket_printer import (
    build_checkin_ticket_bytes,
    preview_checkin_ticket_text,
)


class MixerTicketTests(TestCase):
    def setUp(self):
        self.event = MeetupEvent.objects.create(
            title="1 year of Crush | Find your number",
            description="",
            event_type="mixer",
            location="Atmos",
            date_time=timezone.now() + timedelta(hours=2),
            registration_deadline=timezone.now() + timedelta(hours=1),
            duration_minutes=180,
            max_participants=60,
            is_published=True,
        )
        self.interests = [
            Interest.objects.create(
                slug=f"mixer-test-{i}",
                label=f"Interest {i}",
                category=Interest.Category.WELLNESS,
            )
            for i in range(6)
        ]
        self.regs = []
        for i in range(25):
            self.regs.append(
                self._guest(i, "confirmed", self.interests[i % 3 : i % 3 + 3])
            )

    def _numbers(self):
        """What a real print does: assign missing numbers, then read them."""
        assign_event_numbers(self.event)
        return event_numbers(self.event)

    def _guest(self, i, status, interests=(), dob=None):
        user = User.objects.create_user(
            username=f"g{i}@test.lu", email=f"g{i}@test.lu", first_name=f"First{i}"
        )
        profile = CrushProfile.objects.create(
            user=user,
            gender="F" if i % 2 else "M",
            date_of_birth=dob or date(1990 + i % 10, 1 + i % 12, 1),
            event_languages=["fr", "en"],
            preferred_language="fr",
        )
        if interests:
            profile.interests_new.set(interests)
        return EventRegistration.objects.create(
            user=user, event=self.event, status=status
        )

    def test_numbers_run_over_confirmed_in_registration_order(self):
        cancelled = self._guest(90, "cancelled")
        waiting = self._guest(91, "waitlist")
        numbers = self._numbers()
        self.assertEqual(sorted(numbers.values()), list(range(1, 27)))
        self.assertEqual(numbers[self.regs[0].pk], 1)
        self.assertNotIn(cancelled.pk, numbers)
        self.assertEqual(numbers[waiting.pk], 26)  # reserved after the regulars

    def test_no_show_keeps_numbers_stable_but_leaves_lists(self):
        before = self._numbers()
        absent = self.regs[3]
        absent.status = "no_show"
        absent.save()
        self.assertEqual(self._numbers(), before)
        _, rows, _ = affinity_list(self.regs[0], self.event)
        self.assertNotIn(before[absent.pk], [n for n, _ in rows])

    def test_affinity_is_symmetric_and_excludes_self(self):
        a, b = self.regs[4], self.regs[9]
        num_a, rows_a, others = affinity_list(a, self.event)
        num_b, rows_b, _ = affinity_list(b, self.event)
        self.assertEqual(others, 24)
        self.assertNotIn(num_a, [n for n, _ in rows_a])
        self.assertEqual(dict(rows_a)[num_b], dict(rows_b)[num_a])
        pcts = [p for _, p in rows_a]
        self.assertEqual(pcts, sorted(pcts, reverse=True))
        self.assertTrue(all(35 <= p <= 98 for p in pcts))

    def test_ticket_lists_numbers_never_names(self):
        text = preview_checkin_ticket_text(
            registration=self.regs[0],
            event=self.event,
            coach_authenticated=True,
            language="fr",
        )
        self.assertIn("TES AFFINITÉS", text)
        self.assertIn("FIRST0", text)  # own name on own ticket
        for i in range(1, len(self.regs)):
            self.assertNotIn(f"FIRST{i}", text.upper())
        self.assertEqual(text.count("____________"), LIST_SIZE)
        self.assertIn("+ 4 autres numéros", text)
        self.assertNotIn("SPEED DATING", text.upper())
        self.assertIn("IL Y A 1 AN", text)

    def test_badge_strip_is_cut_before_the_name(self):
        text = preview_checkin_ticket_text(
            registration=self.regs[0],
            event=self.event,
            coach_authenticated=True,
            language="en",
        )
        badge, _, rest = text.partition(">8")
        self.assertIn("FIND ME!", badge)
        self.assertNotIn("FIRST0", badge)
        self.assertIn("FIRST0", rest)

    def test_guest_without_interests_is_not_pushed_to_the_top(self):
        empty = self._guest(50, "confirmed")
        _, rows, _ = affinity_list(self.regs[0], self.event)
        numbers = self._numbers()
        ranked = [n for n, _ in rows]
        self.assertGreater(ranked.index(numbers[empty.pk]), 0)

    def test_payload_encodes_and_test_print_needs_no_registration(self):
        raw = build_checkin_ticket_bytes(
            registration=self.regs[0], event=self.event, language="de"
        )
        self.assertIn(b"\x1dv0", raw)  # big number raster
        self.assertGreaterEqual(raw.count(b"\x1dVB"), 2)  # badge cut + final cut
        sample = preview_checkin_ticket_text(event=self.event, language="fr")
        self.assertIn("#41", sample)

    def test_speed_dating_ticket_unchanged(self):
        self.event.event_type = "speed_dating"
        self.event.save()
        text = preview_checkin_ticket_text(
            registration=self.regs[0], event=self.event, language="en"
        )
        self.assertIn("SPEED DATING // CHECK-IN PASS", text)
        self.assertNotIn("YOUR AFFINITIES", text)

    def test_coach_checkin_endpoint_prints_the_mixer_ticket(self):
        import base64

        from django.core.signing import Signer

        from crush_lu.models import CrushCoach
        from crush_lu.models.profiles import UserDataConsent

        coach_user = User.objects.create_user(
            username="coach@crush.lu", email="coach@crush.lu", first_name="Coach"
        )
        UserDataConsent.objects.update_or_create(
            user=coach_user, defaults={"crushlu_consent_given": True}
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)
        self.client.force_login(coach_user)

        reg = self.regs[2]
        token = Signer().sign(f"{reg.id}:{self.event.id}")
        resp = self.client.post(f"/api/events/checkin/{reg.id}/{token}/")
        self.assertEqual(resp.status_code, 200)
        raw = base64.b64decode(resp.json()["print_payload_base64"])
        self.assertIn("SOCIAL MIXER".encode("cp858"), raw)
        self.assertIn(b"\x1dv0", raw)
        self.assertNotIn(b"SPEED DATING", raw)

        reg.refresh_from_db()
        self.assertEqual(reg.status, "attended")
        # Checking in (confirmed -> attended) must not renumber anyone.
        self.assertEqual(self._numbers()[reg.pk], 3)

    def test_door_promotion_is_appended_without_renumbering(self):
        # Registered before everyone else, so pk order alone would make it #1.
        early_waiter = self.regs[0]
        early_waiter.status = "waitlist"
        early_waiter.save()
        before = self._numbers()
        self.assertEqual(before[early_waiter.pk], len(before))

        early_waiter.status = "attended"
        early_waiter.checkin_prior_status = "waitlist"
        early_waiter.checked_in_at = timezone.now()
        early_waiter.save()
        after = self._numbers()
        for pk, number in before.items():
            self.assertEqual(after[pk], number)
        self.assertEqual(after, before)

    def test_qr_points_to_the_post_event_attendees_page(self):
        text = preview_checkin_ticket_text(
            registration=self.regs[0], event=self.event, language="fr"
        )
        self.assertIn(f"https://crush.lu/fr/events/{self.event.id}/attendees/", text)
        self.assertNotIn("my-crush", text)

    def test_unprintable_name_falls_back_to_a_label(self):
        user = self.regs[0].user
        user.first_name = "Анна"
        user.save()
        text = preview_checkin_ticket_text(
            registration=self.regs[0], event=self.event, language="fr"
        )
        name_line = next(
            line for line in text.splitlines() if line.rstrip().endswith("N° 1")
        )
        self.assertTrue(name_line.startswith("INVITÉ(E)"))

    def test_percentages_do_not_move_when_door_statuses_change(self):
        a, b = self.regs[4], self.regs[9]
        num_b = self._numbers()[b.pk]
        _, before, _ = affinity_list(a, self.event)

        absent = self.regs[12]
        absent.status = "no_show"
        absent.save()
        leaver = self.regs[13]
        leaver.status = "cancelled"
        leaver.save()

        num_a, after, _ = affinity_list(a, self.event)
        _, b_rows, _ = affinity_list(b, self.event)
        self.assertEqual(dict(before)[num_b], dict(after)[num_b])
        self.assertEqual(dict(after)[num_b], dict(b_rows)[num_a])

    def test_cancellation_after_the_first_print_keeps_every_number(self):
        before = self._numbers()
        leaver = self.regs[3]
        leaver.status = "cancelled"
        leaver.save()
        self.assertEqual(self._numbers(), before)
        _, rows, _ = affinity_list(self.regs[0], self.event)
        self.assertNotIn(before[leaver.pk], [n for n, _ in rows])

    def test_a_late_seat_gets_the_next_free_number(self):
        before = self._numbers()
        late = self._guest(77, "confirmed")
        after = self._numbers()
        self.assertEqual({k: after[k] for k in before}, before)
        self.assertEqual(after[late.pk], max(before.values()) + 1)

    def test_coach_test_print_assigns_nothing(self):
        preview_checkin_ticket_text(event=self.event, language="fr")
        self.assertEqual(event_numbers(self.event), {})

    def test_member_total_counts_only_active_profiles(self):
        from crush_lu.services.mixer_ticket import _member_count

        total = _member_count()
        profile = self.regs[0].user.crushprofile
        profile.is_active = False
        profile.save()
        self.assertEqual(_member_count(), total - 1)

    def test_undoing_a_door_admission_keeps_every_number(self):
        first = self._guest(80, "waitlist")
        second = self._guest(81, "waitlist")
        before = self._numbers()
        for reg in (first, second):
            reg.status = "attended"
            reg.checkin_prior_status = "waitlist"
            reg.checked_in_at = timezone.now()
            reg.save()
        self.assertEqual(self._numbers(), before)

        # coach_undo_checkin restores the prior status and clears provenance.
        first.status = "waitlist"
        first.checkin_prior_status = ""
        first.checked_in_at = None
        first.save()
        self.assertEqual(self._numbers(), before)

    def test_payment_settling_during_the_evening_keeps_numbers(self):
        payer = self._guest(85, "pending")
        before = self._numbers()
        payer.status = "confirmed"
        payer.payment_confirmed = True
        payer.payment_date = self.event.date_time + timedelta(minutes=5)
        payer.save()
        self.assertEqual(self._numbers(), before)

    def test_stale_ask_me_about_ids_are_ignored(self):
        from crush_lu.services.mixer_ticket import _Guest

        profile = self.regs[0].user.crushprofile
        profile.ask_me_about = [self.interests[5].pk, self.interests[0].pk]
        profile.save()
        guest = _Guest(self.regs[0])
        self.assertEqual(guest.ask, {self.interests[0].pk})

    def test_blocked_pairs_never_appear_on_each_others_list(self):
        from crush_lu.models import UserBlock

        a, b = self.regs[1], self.regs[2]
        numbers = self._numbers()
        UserBlock.objects.create(blocker=b.user, blocked=a.user)
        num_a, rows_a, others_a = affinity_list(a, self.event)
        _, rows_b, _ = affinity_list(b, self.event)
        self.assertNotIn(numbers[b.pk], [n for n, _ in rows_a])
        self.assertNotIn(num_a, [n for n, _ in rows_b])
        self.assertEqual(others_a, 23)

    def test_hidden_encounters_never_appear_on_each_others_list(self):
        from crush_lu.models import ConfirmedEncounter

        a, b = self.regs[3], self.regs[5]
        low, high = sorted((a.user, b.user), key=lambda u: u.pk)
        ConfirmedEncounter.objects.create(
            user_low=low, user_high=high, status="removal_pending"
        )
        numbers = self._numbers()
        _, rows_a, _ = affinity_list(a, self.event)
        _, rows_b, _ = affinity_list(b, self.event)
        self.assertNotIn(numbers[b.pk], [n for n, _ in rows_a])
        self.assertNotIn(numbers[a.pk], [n for n, _ in rows_b])

    def test_unpaid_no_show_keeps_its_reserved_number(self):
        self.event.registration_fee = 10
        self.event.save()
        for reg in self.regs:
            reg.payment_confirmed = True
            reg.save()
        unpaid = self._guest(86, "pending")
        before = self._numbers()
        unpaid.status = "no_show"
        unpaid.save()
        self.assertEqual(self._numbers(), before)

    def test_deactivated_or_banned_members_are_not_listed(self):
        from crush_lu.models.profiles import UserDataConsent

        numbers = self._numbers()
        gone_user, gone_profile, banned = self.regs[6], self.regs[7], self.regs[8]
        gone_user.user.is_active = False
        gone_user.user.save()
        profile = gone_profile.user.crushprofile
        profile.is_active = False
        profile.save()
        UserDataConsent.objects.update_or_create(
            user=banned.user, defaults={"crushlu_banned": True}
        )

        _, rows, others = affinity_list(self.regs[0], self.event)
        listed = [n for n, _ in rows]
        for reg in (gone_user, gone_profile, banned):
            self.assertNotIn(numbers[reg.pk], listed)
        self.assertEqual(others, 21)
        self.assertEqual(self._numbers(), numbers)

    def test_58mm_rows_fit_the_paper(self):
        from power_up.atmos.printing.layout import Paper

        text = preview_checkin_ticket_text(
            registration=self.regs[0],
            event=self.event,
            paper=Paper.MM58,
            language="fr",
        )
        rows = [line for line in text.splitlines() if line.startswith("#")]
        self.assertEqual(len(rows), LIST_SIZE)
        self.assertTrue(all(len(row) <= Paper.MM58.columns for row in rows))

    def test_registration_only_call_uses_the_event_languages(self):
        self.event.languages = ["de"]
        self.event.save()
        profile = self.regs[0].user.crushprofile
        profile.preferred_language = "en"
        profile.language_explicitly_set = False
        profile.save()
        text = preview_checkin_ticket_text(registration=self.regs[0])
        self.assertIn("DEINE AFFINITÄTEN", text)

    def test_58mm_ticket_has_no_line_wider_than_the_roll(self):
        from power_up.atmos.printing.layout import Paper

        for lang in ("fr", "de", "en"):
            text = preview_checkin_ticket_text(
                registration=self.regs[0],
                event=self.event,
                paper=Paper.MM58,
                language=lang,
            )
            too_wide = [
                line for line in text.splitlines() if len(line) > Paper.MM58.columns
            ]
            self.assertEqual(too_wide, [], lang)

    def test_80mm_ticket_lines_are_unchanged_by_the_fit_pass(self):
        text = preview_checkin_ticket_text(
            registration=self.regs[0], event=self.event, language="fr"
        )
        self.assertIn("Pas de noms. Pas de photos. Que des chiffres.", text)
        self.assertTrue(all(len(line) <= 48 for line in text.splitlines()))

    def test_stale_full_save_cannot_wipe_a_number(self):
        stale = EventRegistration.objects.get(pk=self.regs[5].pk)  # loaded first
        before = self._numbers()
        stale.special_requests = "edited in the admin"
        stale.save()
        self.assertEqual(self._numbers(), before)

    def test_moving_a_registration_leaves_its_number_with_the_old_event(self):
        before = self._numbers()
        other = MeetupEvent.objects.create(
            title="Other mixer",
            description="",
            event_type="mixer",
            location="Atmos",
            date_time=timezone.now() + timedelta(days=3),
            registration_deadline=timezone.now() + timedelta(days=2),
            duration_minutes=120,
            max_participants=20,
            is_published=True,
        )
        moved = self.regs[0]
        moved.event = other
        moved.save()
        assign_event_numbers(other)
        self.assertEqual(event_numbers(other), {moved.pk: 1})
        self.assertEqual(self._numbers(), before)
        _, rows, _ = affinity_list(self.regs[1], self.event)
        self.assertNotIn(before[moved.pk], [n for n, _ in rows])

    def test_58mm_badge_raster_fits_the_roll(self):
        from power_up.atmos.printing.layout import Image, Paper

        from crush_lu.services.ticket_printer import build_checkin_ticket_directives

        directives = build_checkin_ticket_directives(
            registration=self.regs[0], event=self.event, paper=Paper.MM58
        )
        widths = [d.width for d in directives if isinstance(d, Image)]
        self.assertTrue(widths)
        self.assertTrue(all(w <= 384 for w in widths))

    def test_an_erased_registrations_number_is_never_reused(self):
        before = self._numbers()
        top = max(before.values())
        last = next(pk for pk, n in before.items() if n == top)
        EventRegistration.objects.filter(pk=last).delete()
        late = self._guest(78, "confirmed")
        after = self._numbers()
        self.assertEqual(after[late.pk], top + 1)
        self.assertNotIn(last, after)

    def test_a_late_seat_does_not_move_printed_percentages(self):
        a, b = self.regs[4], self.regs[9]
        num_b = self._numbers()[b.pk]
        _, before, _ = affinity_list(a, self.event)
        self._guest(79, "confirmed", self.interests[:3])
        num_a, after, _ = affinity_list(a, self.event)
        _, b_rows, _ = affinity_list(b, self.event)
        self.assertEqual(dict(before)[num_b], dict(after)[num_b])
        self.assertEqual(dict(after)[num_b], dict(b_rows)[num_a])

    def test_a_removed_owner_gets_an_empty_list(self):
        owner = self.regs[0]
        owner.user.is_active = False
        owner.user.save()
        number, rows, others = affinity_list(owner, self.event)
        self.assertEqual(number, self._numbers()[owner.pk])
        self.assertEqual((rows, others), ([], 0))

    def test_a_door_arrival_printing_first_is_numbered_after_the_seated(self):
        walk_up = self.regs[0]  # lowest pk
        walk_up.status = "attended"
        walk_up.checkin_prior_status = "waitlist"
        walk_up.checked_in_at = timezone.now()
        walk_up.save()
        numbers = self._numbers()
        self.assertEqual(numbers[walk_up.pk], len(self.regs))
        self.assertEqual(numbers[self.regs[1].pk], 1)
