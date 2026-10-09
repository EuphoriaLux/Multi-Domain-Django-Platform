"""Social Mixer "Find your number" check-in ticket."""

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import CrushProfile, EventRegistration, Interest, MeetupEvent
from crush_lu.services.mixer_ticket import (
    LIST_SIZE,
    affinity_list,
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
        numbers = event_numbers(self.event)
        self.assertEqual(sorted(numbers.values()), list(range(1, 27)))
        self.assertEqual(numbers[self.regs[0].pk], 1)
        self.assertNotIn(cancelled.pk, numbers)
        self.assertEqual(numbers[waiting.pk], 26)  # reserved after the regulars

    def test_no_show_keeps_numbers_stable_but_leaves_lists(self):
        before = event_numbers(self.event)
        absent = self.regs[3]
        absent.status = "no_show"
        absent.save()
        self.assertEqual(event_numbers(self.event), before)
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
        numbers = event_numbers(self.event)
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
        self.assertEqual(event_numbers(self.event)[reg.pk], 3)

    def test_door_promotion_is_appended_without_renumbering(self):
        # Registered before everyone else, so pk order alone would make it #1.
        early_waiter = self.regs[0]
        early_waiter.status = "waitlist"
        early_waiter.save()
        before = event_numbers(self.event)
        self.assertEqual(before[early_waiter.pk], len(before))

        early_waiter.status = "attended"
        early_waiter.checkin_prior_status = "waitlist"
        early_waiter.checked_in_at = timezone.now()
        early_waiter.save()
        after = event_numbers(self.event)
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
        num_b = event_numbers(self.event)[b.pk]
        _, before, _ = affinity_list(a, self.event)

        absent = self.regs[12]
        absent.status = "no_show"
        absent.save()
        walk_up = self._guest(70, "attended", self.interests[:2])
        walk_up.checkin_prior_status = "waitlist"
        walk_up.checked_in_at = timezone.now()
        walk_up.save()

        num_a, after, _ = affinity_list(a, self.event)
        _, b_rows, _ = affinity_list(b, self.event)
        self.assertEqual(dict(before)[num_b], dict(after)[num_b])
        self.assertEqual(dict(after)[num_b], dict(b_rows)[num_a])

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
        before = event_numbers(self.event)
        for reg in (first, second):
            reg.status = "attended"
            reg.checkin_prior_status = "waitlist"
            reg.checked_in_at = timezone.now()
            reg.save()
        self.assertEqual(event_numbers(self.event), before)

        # coach_undo_checkin restores the prior status and clears provenance.
        first.status = "waitlist"
        first.checkin_prior_status = ""
        first.checked_in_at = None
        first.save()
        self.assertEqual(event_numbers(self.event), before)

    def test_payment_settling_during_the_evening_keeps_numbers(self):
        payer = self._guest(85, "pending")
        before = event_numbers(self.event)
        payer.status = "confirmed"
        payer.payment_confirmed = True
        payer.payment_date = self.event.date_time + timedelta(minutes=5)
        payer.save()
        self.assertEqual(event_numbers(self.event), before)

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
        numbers = event_numbers(self.event)
        UserBlock.objects.create(blocker=b.user, blocked=a.user)
        num_a, rows_a, others_a = affinity_list(a, self.event)
        _, rows_b, _ = affinity_list(b, self.event)
        self.assertNotIn(numbers[b.pk], [n for n, _ in rows_a])
        self.assertNotIn(num_a, [n for n, _ in rows_b])
        self.assertEqual(others_a, 23)
