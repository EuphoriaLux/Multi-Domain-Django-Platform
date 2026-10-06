"""Render tests for four event/profile emails that said something untrue.

Each test sends the real email through ``crush_lu.email_helpers`` (so the
context is the one production builds) with the transport mocked, then reads the
HTML body in EN/DE/FR. The member's language is taken from
``CrushProfile.preferred_language``, exactly as the sweeps do.

Findings covered (CX review, Phase 4 bundle P4-3):

* SD-08  recap email, no connection activity yet: the connection window is
  still open (the recap goes out 12-24 h before it closes), so say where to go
  and until when. It must NOT promise a coach call (that call rests on a push
  reminder, CO-09) and must not claim the next event is "around the corner".
* XC-10  waitlist email on a paid event: promotion lands on ``pending`` and the
  member must pay to confirm. No payment deadline exists in code, so the copy
  names none and does not say the seat is lost.
* QN-15  quiz-night guests are told to bring a charged phone and what to tap.
* CO-03  the rejection email no longer suggests creating a new profile (a
  rejected account cannot resubmit; the web page says so).
* XC-20 fragment: the DE waitlist sentence read "halte Ausschaunach ...".

Review follow-up (automated review, three P2 findings, each checked in code):

* the recap nudge is shown only when the attendee page will open for that
  member AND have someone on it (verified attendee, another attendee who is
  not blocked or hidden); a door-rejected, profile-less or alone member gets
  the plain browse-events ending instead of a link that bounces them;
* the nudge also needs a pick to be left and someone who can receive it: the
  member's one "My Crush!" per event is unused, at least one other attendee has
  a verified profile (``request_connection`` refuses any other recipient) and
  no connection row with the member, and the pair is not one the Event Lobby
  recap takes over (both sides recap-admissible; judged read-only, because
  ``is_recap_admissible`` admits members to the lobby, which an email must not
  do; a member outside the lobby keeps the nudge);
* the paid-waitlist "you then complete the payment" bullet is not shown when
  the registration already carries a payment (a late canceller who re-registers
  keeps ``payment_confirmed``; promotion confirms that seat straight away).

* the quiz-night basics are shown only when the event has a QuizEvent with a
  table count: without one the Join Quiz page 404s and check-in assigns no
  table, so the copy would promise something the member cannot get.

Run with: pytest crush_lu/tests/test_cx_email_copy.py -v
"""

from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path
from unittest import mock

import polib
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.translation import gettext

User = get_user_model()

LANGS = ("en", "de", "fr")
_uid = count(1)


def _po_path(lang):
    return (
        Path(__file__).resolve().parent.parent
        / "locale"
        / lang
        / "LC_MESSAGES"
        / "django.po"
    )


class _EmailCase(TestCase):
    def setUp(self):
        cache.clear()

    # -- fixtures ---------------------------------------------------------

    def _member(self, lang, status="incomplete"):
        from crush_lu.models import CrushProfile

        n = next(_uid)
        user = User.objects.create_user(
            username=f"cx{n}@example.com",
            email=f"cx{n}@example.com",
            password="testpass123",
            first_name="Gaby",
        )
        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            preferred_language=lang,
        )
        # update(): save() syncs the legacy flags; the state is all this needs.
        CrushProfile.objects.filter(pk=profile.pk).update(verification_status=status)
        # ``user.crushprofile`` is this very instance (reverse one-to-one cache);
        # production loads registrations fresh, so keep the in-memory copy true.
        profile.refresh_from_db(fields=["verification_status"])
        return user

    def _event(self, *, start_offset, event_type="speed_dating", **extra):
        from crush_lu.models import MeetupEvent

        start = timezone.now() + start_offset
        fields = {
            "title": "CX Copy Test",
            "description": "d",
            "event_type": event_type,
            "date_time": start,
            "duration_minutes": 180,
            "location": "Luxembourg",
            "address": "1 Test Street",
            "max_participants": 20,
            "registration_deadline": start - timedelta(days=1),
            "is_published": True,
        }
        fields.update(extra)
        return MeetupEvent.objects.create(**fields)

    def _register(self, user, event, status):
        from crush_lu.models import EventRegistration

        extra = {}
        if status == "attended":
            extra["checked_in_at"] = timezone.now() - timedelta(hours=33)
        return EventRegistration.objects.create(
            event=event, user=user, status=status, **extra
        )

    def _send(self, sender_name, *args, **kwargs):
        """Run one real sender with the transport mocked; return the HTML."""
        from crush_lu import email_helpers

        sender = getattr(email_helpers, sender_name)
        with mock.patch.object(
            email_helpers, "send_domain_email", return_value=1
        ) as send:
            sender(*args, **kwargs)
        self.assertEqual(send.call_count, 1, f"{sender_name} sent {send.call_count}")
        return send.call_args.kwargs["html_message"]

    def _t(self, lang, msgid):
        with translation.override(lang):
            return gettext(msgid)


# ---------------------------------------------------------------------------
# SD-08: recap email
# ---------------------------------------------------------------------------

# What the member read before: an unsupported claim and no way forward.
OLD_RECAP = {
    "en": "The next event is around the corner",
    "de": "Das nächste Event steht schon bald an",
    "fr": "Le prochain événement approche",
}
# Anything that would promise a human action the code does not enforce.
COACH_CALL_PROMISES = (
    "coach will call",
    "Coach ruft",
    "vous appellera",
    "call you",
)


class RecapNudgeTests(_EmailCase):
    """The recap tells a member with no activity that the window is open."""

    def _recap(
        self,
        lang,
        *,
        window_hours=48,
        incoming=False,
        outgoing=False,
        viewer_status="verified",
        other_status="attended",
        other_verification="verified",
        block_other=False,
        crush_declared=False,
        sent_status=None,
        received_status=None,
        received_flow=None,
        lobby=None,
    ):
        from crush_lu.models import EventConnection, UserBlock

        # Real attendees are verified (the door scan does it); the nudge is only
        # for members the attendee page will let in.
        member = self._member(lang, status=viewer_status)
        # Ended 30 h ago: the recap sweep sends 24-36 h after the end.
        event = self._event(
            start_offset=-timedelta(hours=33),
            connection_window_hours=window_hours,
        )
        reg = self._register(member, event, "attended")
        other = self._member(lang, status=other_verification)
        if other_status:
            self._register(other, event, other_status)
        if block_other:
            UserBlock.objects.create(blocker=member, blocked=other)
        if incoming:
            EventConnection.objects.create(
                requester=other, recipient=member, event=event, status="pending"
            )
        if outgoing:
            EventConnection.objects.create(
                requester=member, recipient=other, event=event, status="pending"
            )
        if sent_status:
            # An "outgoing" row in a status the activity counts skip (declined).
            EventConnection.objects.create(
                requester=member, recipient=other, event=event, status=sent_status
            )
        if received_status:
            EventConnection.objects.create(
                requester=other,
                recipient=member,
                event=event,
                status=received_status,
                flow=received_flow or EventConnection.FLOW_LEGACY,
            )
        if crush_declared:
            # A private "My Crush!" lead: hidden from the activity counts
            # (excluding_unshared_crushes) but it uses the one-per-event quota.
            EventConnection.objects.create(
                requester=member,
                recipient=other,
                event=event,
                status="pending",
                flow=EventConnection.FLOW_CRUSH,
            )
        if lobby is None:
            html = self._send("send_event_recap", reg, request=None)
        else:
            # Event Lobby on, and the recap phase is open (the event ended 30 h
            # ago, the lobby recap lasts 48 h). ``lobby`` maps "viewer"/"other"
            # to the (ok, reason) their participant_gate would return, and
            # "may_learn" to may_learn_lobby_exists.
            gates = {member.pk: lobby["viewer"], other.pk: lobby["other"]}
            with mock.patch(
                "crush_lu.services.event_lobby.lobby_feature_enabled",
                return_value=True,
            ), mock.patch(
                "crush_lu.services.event_lobby.participant_gate",
                side_effect=lambda user: gates.get(user.pk, (False, "no_membership")),
            ), mock.patch(
                "crush_lu.services.event_lobby.may_learn_lobby_exists",
                return_value=lobby.get("may_learn", False),
            ):
                html = self._send("send_event_recap", reg, request=None)
        return event, html

    def _attendees_path(self, lang, event):
        return f"/{lang}/events/{event.id}/attendees/"

    def test_no_activity_window_open_points_to_attendees_with_real_deadline(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang)
                self.assertTrue(event.connection_window_active)

                with translation.override(lang):
                    deadline = date_format(
                        timezone.localtime(event.connection_window_deadline),
                        "DATETIME_FORMAT",
                    )
                self.assertIn(self._t(lang, "Who caught your eye?"), html)
                self.assertIn(
                    self._t(
                        lang,
                        "Until %(deadline)s you can still make your picks from "
                        "the attendee list.",
                    )
                    % {"deadline": deadline},
                    html,
                )
                self.assertIn(self._t(lang, "It was lovely to have you."), html)
                # The button goes to the attendees page, in the member's language.
                self.assertIn(self._attendees_path(lang, event), html)
                self.assertIn(self._t(lang, "View Attendees & Connections"), html)
                # Browse-events stays, as the secondary link.
                self.assertIn(self._t(lang, "Browse upcoming events"), html)

    def test_exact_copy_in_each_language(self):
        """Literal strings, independent of gettext, so a wrong msgstr cannot hide."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang)
                with translation.override(lang):
                    deadline = date_format(
                        timezone.localtime(event.connection_window_deadline),
                        "DATETIME_FORMAT",
                    )
                heading = {
                    "en": "Who caught your eye?",
                    "de": "Wer ist dir aufgefallen?",
                    "fr": "Qui a attiré votre attention ?",
                }
                sentence = {
                    "en": (
                        f"Until {deadline} you can still make your picks from "
                        "the attendee list."
                    ),
                    "de": (
                        f"Bis {deadline} kannst du deine Auswahl noch in der "
                        "Teilnehmerliste treffen."
                    ),
                    "fr": (
                        f"Jusqu'au {deadline}, vous pouvez encore faire vos "
                        "choix dans la liste des participants."
                    ),
                }
                self.assertIn(heading[lang], html)
                self.assertIn(sentence[lang], html)

    def test_old_unsupported_claim_is_gone(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                _, html = self._recap(lang)
                self.assertNotIn(OLD_RECAP[lang], html)

    def test_no_coach_call_promise_is_added(self):
        """The call depends on a fragile push reminder (CO-09): never promise it."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                _, html = self._recap(lang)
                for promise in COACH_CALL_PROMISES:
                    self.assertNotIn(promise, html)

    def test_window_closed_keeps_only_browse_button(self):
        """Negative control: with the window shut there is nothing to nudge."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang, window_hours=1)
                self.assertFalse(event.connection_window_active)
                self.assertNotIn(self._t(lang, "Who caught your eye?"), html)
                self.assertNotIn(self._attendees_path(lang, event), html)
                self.assertIn(self._t(lang, "Browse upcoming events"), html)
                self.assertIn(self._t(lang, "It was lovely to have you."), html)
                self.assertNotIn(OLD_RECAP[lang], html)

    def test_incoming_request_keeps_the_existing_action_block(self):
        """Negative control: the has_action branch is untouched."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang, incoming=True)
                self.assertIn(self._t(lang, "Don't lose the moment."), html)
                self.assertIn(self._t(lang, "See attendees & finish your picks"), html)
                self.assertIn(self._attendees_path(lang, event), html)
                self.assertNotIn(self._t(lang, "Who caught your eye?"), html)
                self.assertNotIn(self._t(lang, "It was lovely to have you."), html)

    def test_member_who_already_sent_a_request_gets_no_nudge(self):
        """Negative control: the nudge is for members with no activity at all."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang, outgoing=True)
                self.assertNotIn(self._t(lang, "Who caught your eye?"), html)
                self.assertNotIn(self._attendees_path(lang, event), html)
                self.assertIn(self._t(lang, "Browse upcoming events"), html)

    # -- review follow-up: no nudge toward a page that will not open ---------

    def _assert_plain_ending(self, lang, event, html):
        self.assertNotIn(self._t(lang, "Who caught your eye?"), html)
        self.assertNotIn(self._attendees_path(lang, event), html)
        self.assertNotIn(self._t(lang, "View Attendees & Connections"), html)
        self.assertIn(self._t(lang, "Browse upcoming events"), html)
        self.assertIn(self._t(lang, "It was lovely to have you."), html)
        self.assertNotIn(OLD_RECAP[lang], html)

    def test_door_rejected_attendee_gets_no_link_to_a_page_that_bounces_them(self):
        """A rejected member stays ``attended`` (so the sweep mails them) but
        ``can_make_connections`` is False and event_attendees redirects them."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang, viewer_status="rejected")
                self.assertTrue(event.connection_window_active)
                self._assert_plain_ending(lang, event, html)

    def test_member_without_a_profile_gets_no_nudge(self):
        from crush_lu.models import EventRegistration

        event = self._event(
            start_offset=-timedelta(hours=33), connection_window_hours=48
        )
        n = next(_uid)
        ghost = User.objects.create_user(
            username=f"cx{n}@example.com",
            email=f"cx{n}@example.com",
            password="testpass123",
        )
        reg = self._register(ghost, event, "attended")
        self.assertFalse(EventRegistration.objects.get(pk=reg.pk).can_make_connections)
        other = self._member("en", status="verified")
        self._register(other, event, "attended")

        html = self._send("send_event_recap", reg, request=None)

        self._assert_plain_ending("en", event, html)

    def test_no_other_attendee_means_no_nudge_to_an_empty_roster(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang, other_status="no_show")
                self.assertTrue(event.connection_window_active)
                self._assert_plain_ending(lang, event, html)
        with self.subTest("nobody else registered at all"):
            event, html = self._recap("en", other_status=None)
            self._assert_plain_ending("en", event, html)

    def test_only_blocked_attendees_means_no_nudge(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang, block_other=True)
                self._assert_plain_ending(lang, event, html)

    def test_member_who_used_their_crush_gets_no_nudge_to_pick_again(self):
        """Every post-event pick is a crush lead and the quota is one per event.
        The private lead is hidden from the activity counts, so without the
        quota check this member would be told to make picks they cannot make."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang, crush_declared=True)
                self.assertTrue(event.connection_window_active)
                self._assert_plain_ending(lang, event, html)

    def test_attendees_who_cannot_receive_a_pick_do_not_count(self):
        """``request_connection`` refuses a recipient whose own registration
        fails ``can_make_connections`` (door-rejected, unverified)."""
        for lang in LANGS:
            for status in ("rejected", "pending", "incomplete"):
                with self.subTest(lang=lang, other_verification=status):
                    event, html = self._recap(lang, other_verification=status)
                    self._assert_plain_ending(lang, event, html)

    def test_attendee_without_a_profile_does_not_count(self):
        from crush_lu.models import EventRegistration

        member = self._member("en", status="verified")
        event = self._event(
            start_offset=-timedelta(hours=33), connection_window_hours=48
        )
        reg = self._register(member, event, "attended")
        n = next(_uid)
        ghost = User.objects.create_user(
            username=f"cx{n}@example.com",
            email=f"cx{n}@example.com",
            password="testpass123",
        )
        ghost_reg = self._register(ghost, event, "attended")
        self.assertFalse(
            EventRegistration.objects.get(pk=ghost_reg.pk).can_make_connections
        )

        html = self._send("send_event_recap", reg, request=None)

        self._assert_plain_ending("en", event, html)

    def test_attendee_already_targeted_by_the_member_is_not_a_pick(self):
        """``request_connection`` refuses any same-direction row whatever its
        status, and the page renders a declined one as non-actionable. A
        declined row is not in the activity counts, so it must be excluded
        here."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang, sent_status="declined")
                self._assert_plain_ending(lang, event, html)

    def test_attendee_with_a_closed_request_towards_the_member_is_not_a_pick(self):
        """The page marks such an attendee non-actionable too."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(lang, received_status="declined")
                self._assert_plain_ending(lang, event, html)

    def test_private_crush_lead_towards_the_member_does_not_hide_the_pick(self):
        """Control: the page hides a pre-``shared`` crush lead from its
        recipient, so that attendee is still an ordinary pick, and the email
        must not change (it must not reveal the lead either way)."""
        from crush_lu.models import EventConnection

        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(
                    lang,
                    received_status="pending",
                    received_flow=EventConnection.FLOW_CRUSH,
                )
                self.assertIn(self._t(lang, "Who caught your eye?"), html)
                self.assertIn(self._attendees_path(lang, event), html)

    def test_one_pickable_attendee_among_unpickable_ones_is_enough(self):
        """Control: unverified and already-targeted guests do not hide a fresh,
        verified one."""
        from crush_lu.models import EventConnection

        member = self._member("en", status="verified")
        event = self._event(
            start_offset=-timedelta(hours=33), connection_window_hours=48
        )
        reg = self._register(member, event, "attended")
        self._register(self._member("en", status="rejected"), event, "attended")
        targeted = self._member("en", status="verified")
        self._register(targeted, event, "attended")
        EventConnection.objects.create(
            requester=member, recipient=targeted, event=event, status="declined"
        )
        self._register(self._member("en", status="verified"), event, "attended")

        html = self._send("send_event_recap", reg, request=None)

        self.assertIn("Who caught your eye?", html)
        self.assertIn(self._attendees_path("en", event), html)

    # -- Event Lobby recap: only a pair the lobby takes over is not a pick ----

    OK = (True, "ok")
    OUTSIDE = (False, "no_membership")  # no Connect membership
    NO_PHOTO = (False, "no_photo")  # onboarded but not lobby-capable
    NOT_ONBOARDED = (False, "not_onboarded")

    def _assert_nudged(self, lang, event, html):
        self.assertIn(self._t(lang, "Who caught your eye?"), html)
        self.assertIn(self._attendees_path(lang, event), html)

    def test_member_outside_the_lobby_keeps_the_nudge_while_the_lobby_recap_is_open(
        self,
    ):
        """The attendee page shows the pick button to a viewer who is not a lobby
        participant (their recap list is empty), so the email must not go quiet
        just because the lobby feature is on: the recap email always goes out
        inside the 48 h lobby recap."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(
                    lang,
                    lobby={
                        "viewer": self.OUTSIDE,
                        "other": self.OK,
                        "may_learn": False,
                    },
                )
                self._assert_nudged(lang, event, html)

    def test_lobby_member_still_has_a_pick_when_the_other_attendee_is_outside_it(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                event, html = self._recap(
                    lang, lobby={"viewer": self.OK, "other": self.NO_PHOTO}
                )
                self._assert_nudged(lang, event, html)

    def test_pair_the_lobby_recap_takes_over_is_not_a_pick(self):
        """``request_connection`` sends a pair to the lobby recap when both
        sides are admissible, so the email says nothing about picks."""
        cases = {
            "both lobby participants": {"viewer": self.OK, "other": self.OK},
            "viewer participant, other a Connect guest who could onboard": {
                "viewer": self.OK,
                "other": self.NOT_ONBOARDED,
                "may_learn": True,
            },
            "viewer a guest who could onboard, other a participant": {
                "viewer": self.NOT_ONBOARDED,
                "other": self.OK,
                "may_learn": True,
            },
        }
        for label, lobby in cases.items():
            for lang in LANGS:
                with self.subTest(case=label, lang=lang):
                    event, html = self._recap(lang, lobby=lobby)
                    self._assert_plain_ending(lang, event, html)

    def test_one_free_attendee_next_to_a_lobby_one_is_enough(self):
        """Control, no lobby simulation needed beyond the gate: a second
        attendee outside the lobby keeps the pick."""
        member = self._member("en", status="verified")
        event = self._event(
            start_offset=-timedelta(hours=33), connection_window_hours=48
        )
        reg = self._register(member, event, "attended")
        in_lobby = self._member("en", status="verified")
        outside = self._member("en", status="verified")
        self._register(in_lobby, event, "attended")
        self._register(outside, event, "attended")
        gates = {member.pk: self.OK, in_lobby.pk: self.OK}
        with mock.patch(
            "crush_lu.services.event_lobby.lobby_feature_enabled", return_value=True
        ), mock.patch(
            "crush_lu.services.event_lobby.participant_gate",
            side_effect=lambda user: gates.get(user.pk, self.OUTSIDE),
        ), mock.patch(
            "crush_lu.services.event_lobby.may_learn_lobby_exists",
            return_value=False,
        ):
            html = self._send("send_event_recap", reg, request=None)
        self._assert_nudged("en", event, html)

    def test_read_only_admissibility_matches_is_recap_admissible(self):
        """``_recap_lobby_admissible`` must give the answer ``is_recap_admissible``
        gives (that one admits the member, a write the email must not make)."""
        from crush_lu.email_helpers import _recap_lobby_admissible
        from crush_lu.services.event_lobby import is_recap_admissible

        states = [
            ("participant", self.OK, False),
            ("no membership, may learn", self.OUTSIDE, True),
            ("no membership, may not learn", self.OUTSIDE, False),
            ("not onboarded, may learn", self.NOT_ONBOARDED, True),
            ("not onboarded, may not learn", self.NOT_ONBOARDED, False),
            ("onboarded but no photo, may learn", self.NO_PHOTO, True),
            ("excluded, may learn", (False, "excluded"), True),
            ("paused, may learn", (False, "paused"), True),
        ]
        for label, gate, may_learn in states:
            with self.subTest(label):
                member = self._member("en", status="verified")
                event = self._event(
                    start_offset=-timedelta(hours=33), connection_window_hours=48
                )
                self._register(member, event, "attended")
                with mock.patch(
                    "crush_lu.services.event_lobby.lobby_feature_enabled",
                    return_value=True,
                ), mock.patch(
                    "crush_lu.services.event_lobby.participant_gate",
                    return_value=gate,
                ), mock.patch(
                    "crush_lu.services.event_lobby.may_learn_lobby_exists",
                    return_value=may_learn,
                ):
                    expected = is_recap_admissible(member, event)
                    self.assertEqual(_recap_lobby_admissible(member), expected)

    def test_lobby_feature_off_keeps_the_nudge(self):
        """Control: the flag is off by default."""
        with mock.patch(
            "crush_lu.services.event_lobby.lobby_feature_enabled",
            return_value=False,
        ):
            event, html = self._recap("en")
        self.assertIn("Who caught your eye?", html)

    def test_negative_control_verified_member_with_a_visible_attendee_is_nudged(self):
        event, html = self._recap("en")
        self.assertIn("Who caught your eye?", html)
        self.assertIn(self._attendees_path("en", event), html)


# ---------------------------------------------------------------------------
# XC-10 (+ XC-20 fragment): waitlist email
# ---------------------------------------------------------------------------

OLD_WAITLIST_PROMISE = {
    "en": "automatically moved to confirmed",
    "de": "automatisch in den bestätigten Status verschoben",
    "fr": "automatiquement confirmé(e)",
}
PAID_WAITLIST_MARKER = {
    "en": "we hold the spot for you",
    "de": "halten wir den Platz für dich frei",
    "fr": "nous vous réservons la place",
}


class WaitlistEmailTests(_EmailCase):
    def _waitlist(self, lang, fee, *, already_paid=False):
        from crush_lu.models import EventRegistration

        member = self._member(lang)
        event = self._event(
            start_offset=timedelta(days=5),
            max_participants=1,
            registration_fee=fee,
        )
        reg = self._register(member, event, "waitlist")
        if already_paid:
            # What a late cancel leaves on the row; re-registering on a full
            # event reuses it and lands on the waitlist (views_events).
            EventRegistration.objects.filter(pk=reg.pk).update(payment_confirmed=True)
            reg.refresh_from_db()
        return self._send("send_event_waitlist_notification", reg, None)

    def test_waitlister_whose_payment_is_already_held_is_not_asked_to_pay(self):
        """Promotion confirms such a seat at once (``_admitted_status``), so the
        original bullet is true for them and the payment bullet is not."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self._waitlist(lang, Decimal("15.50"), already_paid=True)
                self.assertIn(OLD_WAITLIST_PROMISE[lang], html)
                self.assertNotIn(PAID_WAITLIST_MARKER[lang], html)
                self.assertNotIn("15.50", html)
                self.assertNotIn("15,50", html)

    def test_negative_control_unpaid_waitlister_still_gets_the_payment_bullet(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self._waitlist(lang, Decimal("15.50"), already_paid=False)
                self.assertIn(PAID_WAITLIST_MARKER[lang], html)
                self.assertNotIn(OLD_WAITLIST_PROMISE[lang], html)

    def test_paid_event_says_payment_is_needed_to_confirm(self):
        expected = {
            "en": (
                "If someone cancels, we hold the spot for you and email you "
                "straight away. You then complete the payment (15.50 EUR) to "
                "confirm it."
            ),
            "de": (
                "Wenn jemand storniert, halten wir den Platz für dich frei und "
                "schreiben dir sofort. Du schließt dann die Zahlung (15,50 EUR) "
                "ab, um den Platz zu bestätigen."
            ),
            "fr": (
                "Si quelqu'un annule, nous vous réservons la place et vous "
                "écrivons immédiatement. Vous réglez alors 15,50 EUR pour la "
                "confirmer."
            ),
        }
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self._waitlist(lang, Decimal("15.50"))
                self.assertIn(expected[lang], html)
                self.assertNotIn(OLD_WAITLIST_PROMISE[lang], html)

    def test_paid_event_copy_invents_no_deadline_and_no_seat_loss(self):
        """No code releases an unpaid pending seat, so the email must not say so."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self._waitlist(lang, Decimal("15.50")).lower()
                for word in (
                    "deadline",
                    "expire",
                    "released",
                    "forfeit",
                    "within ",
                    "délai",
                    "frist",
                    "innerhalb",
                    "verfällt",
                    "perdu",
                ):
                    self.assertNotIn(word, html)

    def test_free_event_keeps_the_original_bullet(self):
        """Negative control: a free event is promoted straight to confirmed."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self._waitlist(lang, Decimal("0.00"))
                self.assertIn(OLD_WAITLIST_PROMISE[lang], html)
                self.assertNotIn(PAID_WAITLIST_MARKER[lang], html)
                self.assertNotIn(" EUR)", html)

    def test_other_bullets_and_button_unchanged_on_paid_event(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self._waitlist(lang, Decimal("15.50"))
                self.assertIn(
                    self._t(lang, "You'll receive an email if your status changes"),
                    html,
                )
                self.assertIn(self._t(lang, "Browse Other Events"), html)

    def test_german_closing_line_has_its_spaces_back(self):
        html = self._waitlist("de", Decimal("0.00"))
        self.assertNotIn("Ausschaunach", html)
        self.assertIn(
            "Wir veranstalten regelmäßig Veranstaltungen, also halte Ausschau "
            "nach weiteren Möglichkeiten!",
            html,
        )


# ---------------------------------------------------------------------------
# QN-15: quiz-night basics in the confirmation and reminder emails
# ---------------------------------------------------------------------------

QUIZ_BASICS = {
    "en": (
        "For Quiz Night: bring a charged phone. After the coach checks you in "
        "you'll get your table number — then tap Join Quiz on the event page. "
        "No team needed."
    ),
    "de": (
        "Für den Quiz-Abend: Bring ein aufgeladenes Handy mit. Nach dem "
        "Check-in beim Coach bekommst du deine Tischnummer – dann tippst du "
        "auf der Eventseite auf „Quiz beitreten“. Ein eigenes Team brauchst "
        "du nicht."
    ),
    "fr": (
        "Pour la soirée quiz : apportez un téléphone chargé. Après votre "
        "enregistrement auprès du coach, vous recevrez votre numéro de table ; "
        "touchez ensuite « Rejoindre le quiz » sur la page de l'événement. "
        "Pas besoin d'équipe."
    ),
}
QUIZ_SENDERS = ("send_event_registration_confirmation", "send_event_reminder")


class QuizNightEmailTests(_EmailCase):
    def _mail(self, sender_name, lang, event_type, *, quiz_tables=4):
        """Render ``sender_name`` for a confirmed guest.

        ``quiz_tables``: ``None`` means the quiz_night event has no QuizEvent
        row at all (an explicitly supported state); ``0`` is a QuizEvent whose
        table count was never set; a positive number is a configured quiz.
        Only quiz_night events get a QuizEvent.
        """
        from crush_lu.models.quiz import QuizEvent

        member = self._member(lang)
        event = self._event(start_offset=timedelta(days=2), event_type=event_type)
        if event_type == "quiz_night" and quiz_tables is not None:
            QuizEvent.objects.create(
                event=event,
                created_by=member,
                num_tables=quiz_tables or None,
            )
        reg = self._register(member, event, "confirmed")
        return self._send(sender_name, reg, request=None)

    def test_confirmation_and_reminder_carry_the_quiz_basics(self):
        for sender in QUIZ_SENDERS:
            for lang in LANGS:
                with self.subTest(sender=sender, lang=lang):
                    html = self._mail(sender, lang, "quiz_night")
                    self.assertIn(QUIZ_BASICS[lang], html)

    def test_button_name_matches_the_shipped_event_page_label(self):
        """The email names the button the member will actually see."""
        for lang in LANGS:
            with self.subTest(lang=lang):
                self.assertIn(self._t(lang, "Join Quiz"), QUIZ_BASICS[lang])

    def test_other_event_types_get_no_quiz_block(self):
        """Negative control: a speed-dating guest must not be told about quizzes."""
        for sender in QUIZ_SENDERS:
            for lang in LANGS:
                with self.subTest(sender=sender, lang=lang):
                    html = self._mail(sender, lang, "speed_dating")
                    self.assertNotIn(QUIZ_BASICS[lang], html)
                    self.assertNotIn(self._t(lang, "Join Quiz"), html)
                    # ...and the rest of the email is still there.
                    self.assertIn(self._t(lang, "Arrive 10-15 minutes early"), html)

    def test_no_quiz_block_until_a_quiz_with_tables_is_configured(self):
        """The block promises a table number and a Join Quiz button.

        Both need a QuizEvent: ``quiz_live_view`` 404s without one, and
        check-in only assigns a table when ``num_tables`` is set. A quiz_night
        event with no QuizEvent, or one with a blank table count, must not
        make that promise.
        """
        for sender in QUIZ_SENDERS:
            for lang in LANGS:
                for label, tables in (("no QuizEvent", None), ("blank tables", 0)):
                    with self.subTest(sender=sender, lang=lang, setup=label):
                        html = self._mail(
                            sender, lang, "quiz_night", quiz_tables=tables
                        )
                        self.assertNotIn(QUIZ_BASICS[lang], html)
                        self.assertNotIn(self._t(lang, "Join Quiz"), html)
                        # ...and the rest of the email is still there.
                        self.assertIn(self._t(lang, "Arrive 10-15 minutes early"), html)

    def test_existing_what_to_bring_list_is_untouched_for_quiz_night(self):
        html = self._mail("send_event_registration_confirmation", "en", "quiz_night")
        self.assertIn("Valid ID (for age verification)", html)
        self.assertIn("Your best self!", html)
        html = self._mail("send_event_reminder", "en", "quiz_night")
        self.assertIn("Valid ID for age verification", html)
        self.assertIn("Yourself - no one else!", html)


# ---------------------------------------------------------------------------
# CO-03: rejection email
# ---------------------------------------------------------------------------

OLD_REJECTED_ADVICE = {
    "en": "create a new profile",
    "de": "neues Profil erstellen",
    "fr": "créer un nouveau profil",
}


class RejectedEmailTests(_EmailCase):
    def _rejected(self, lang):
        member = self._member(lang)
        return self._send(
            "send_profile_rejected_notification",
            member.crushprofile,
            None,
            "Photos do not show your face.",
        )

    def test_no_longer_suggests_creating_a_new_profile(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self._rejected(lang)
                self.assertNotIn(OLD_REJECTED_ADVICE[lang], html)

    def test_still_tells_the_member_to_contact_support(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self._rejected(lang)
                self.assertIn("support@crush.lu", html)
                self.assertIn(self._t(lang, "What you can do:"), html)
                self.assertIn(
                    self._t(
                        lang,
                        "If you believe this was an error, please contact us "
                        "at support@crush.lu",
                    ),
                    html,
                )

    def test_reason_and_sign_off_are_untouched(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                html = self._rejected(lang)
                self.assertIn("Photos do not show your face.", html)
                self.assertIn(self._t(lang, "Reason:"), html)
                self.assertIn(self._t(lang, "The Crush.lu Team"), html)


# ---------------------------------------------------------------------------
# The .po entries themselves
# ---------------------------------------------------------------------------

NEW_MSGIDS = {
    "Until %(deadline)s you can still make your picks from the attendee list.": (
        "%(deadline)s",
    ),
    "If someone cancels, we hold the spot for you and email you straight away. "
    "You then complete the payment (%(fee)s EUR) to confirm it.": ("%(fee)s",),
    QUIZ_BASICS["en"]: (),
    "It was lovely to have you.": (),
}


class CatalogueEntryTests(TestCase):
    """New and edited entries are translated, not fuzzy, and keep placeholders."""

    def test_new_entries_are_translated_in_de_and_fr(self):
        for lang in ("de", "fr"):
            catalogue = {e.msgid: e for e in polib.pofile(str(_po_path(lang)))}
            for msgid, placeholders in NEW_MSGIDS.items():
                with self.subTest(lang=lang, msgid=msgid[:40]):
                    entry = catalogue.get(msgid)
                    self.assertIsNotNone(entry, "missing from the catalogue")
                    self.assertTrue(entry.msgstr.strip(), "empty msgstr")
                    self.assertNotEqual(entry.msgstr, msgid, "left in English")
                    self.assertFalse(entry.fuzzy, "fuzzy entries are not shipped")
                    for placeholder in placeholders:
                        self.assertIn(placeholder, entry.msgstr)
                        self.assertIn("python-format", entry.flags)

    def test_old_recap_msgid_is_gone_and_typo_is_fixed(self):
        old = (
            "It was lovely to have you. The next event is around the corner — "
            "keep an eye on your inbox."
        )
        for lang in ("de", "fr"):
            catalogue = {e.msgid: e for e in polib.pofile(str(_po_path(lang)))}
            with self.subTest(lang=lang):
                self.assertNotIn(old, catalogue)
        de = {e.msgid: e for e in polib.pofile(str(_po_path("de")))}
        entry = de[
            "We host events regularly, so keep an eye out for more opportunities!"
        ]
        self.assertNotIn("Ausschaunach", entry.msgstr)
        self.assertIn("halte Ausschau nach weiteren", entry.msgstr)
