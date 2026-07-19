"""
Seed a fully demoable Crush Connect Event Lobby on the local dev database.

Creates one live event (started 30 minutes ago, 120-minute duration) plus:

- three eligible, checked-in Crush Connect members (alice/ben/chloe) who see
  each other in the lobby;
- one checked-in NON-Connect attendee (dan) who checks in normally but never
  appears anywhere (spec §5.3);
- one checked-in member with an unfinished Connect onboarding (eve) who gets
  the "Finish Crush Connect" CTA instead of the roster.

Photos are locally generated solid-colour JPEGs — no network needed.
Idempotent; pass ``--reset`` to wipe and recreate the demo objects.

Usage (dev only):

    python manage.py seed_event_lobby_demo [--reset] [--password demo1234]

Remember the flags: CRUSH_EVENT_LOBBY_ENABLED=true and CRUSH_CONNECT_LAUNCHED
(or CRUSH_CONNECT_CANDIDATE_OPEN)=true must be set for the lobby to open.
"""

import io
import os
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

DEMO_PREFIX = "lobby_"
DEMO_EVENT_TITLE = "Event Lobby Demo Night"

# username -> (first_name, gender, photo colour, kind)
DEMO_USERS = {
    "lobby_alice": ("Alice", "F", (233, 79, 138), "connect"),
    "lobby_ben": ("Ben", "M", (108, 92, 231), "connect"),
    "lobby_chloe": ("Chloe", "F", (0, 148, 133), "connect"),
    "lobby_dan": ("Dan", "M", (90, 90, 90), "non_connect"),
    "lobby_eve": ("Eve", "F", (214, 137, 16), "not_onboarded"),
}


def _demo_photo(color):
    """A 400x400 solid-colour JPEG generated locally (no network)."""
    from PIL import Image

    img = Image.new("RGB", (400, 400), color=color)
    buffer = io.BytesIO()
    img.save(buffer, "JPEG")
    return ContentFile(buffer.getvalue())


class Command(BaseCommand):
    help = "Seed a live Event Lobby demo (event + checked-in members) on the dev DB"

    def add_arguments(self, parser):
        parser.add_argument("--password", default="demo1234")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the demo users/event first and recreate them fresh",
        )

    def handle(self, *args, **options):
        # Dev-only guard: this command creates verified, Connect-onboarded
        # accounts with a shared default password. WEBSITE_HOSTNAME is set on
        # every Azure App Service slot (it is how manage.py selects production
        # settings), so refuse to run there — including Kudu SSH shells.
        if os.environ.get("WEBSITE_HOSTNAME"):
            raise CommandError(
                "seed_event_lobby_demo is a local-dev tool and refuses to run "
                "on Azure (WEBSITE_HOSTNAME is set): it would create demo "
                "accounts with a publicly documented password."
            )

        from crush_lu.models import (
            CrushConnectMembership,
            EventRegistration,
            MeetupEvent,
        )
        from crush_lu.services.event_lobby import handle_checkin

        password = options["password"]
        now = timezone.now()

        if options["reset"]:
            User.objects.filter(username__startswith=DEMO_PREFIX).delete()
            MeetupEvent.objects.filter(title=DEMO_EVENT_TITLE).delete()
            self.stdout.write("Reset: removed existing demo users and event.")

        event, created = MeetupEvent.objects.get_or_create(
            title=DEMO_EVENT_TITLE,
            defaults={
                "description": "Local demo event for the Crush Connect Event Lobby prototype.",
                "event_type": "mixer",
                "location": "Demo Bar, Luxembourg",
                "address": "1 Demo Street, Luxembourg City",
                "canton": "Luxembourg",
                "date_time": now - timedelta(minutes=30),
                "duration_minutes": 120,
                "max_participants": 30,
                "registration_deadline": now - timedelta(minutes=45),
                "is_published": True,
                "profile_requirement": "none",
            },
        )
        if not created:
            # Re-arm the live window on every run so the demo is always live.
            event.date_time = now - timedelta(minutes=30)
            event.duration_minutes = 120
            event.is_published = True
            event.is_cancelled = False
            event.save(
                update_fields=[
                    "date_time",
                    "duration_minutes",
                    "is_published",
                    "is_cancelled",
                ]
            )
        self.stdout.write(
            f"Event: {event.title} (id={event.pk}) — live until {timezone.localtime(event.end_time):%H:%M}"
        )

        for username, (first_name, gender, color, kind) in DEMO_USERS.items():
            user = self._ensure_user(username, first_name, password)
            self._ensure_profile(user, gender, color, kind)
            if kind == "connect":
                CrushConnectMembership.objects.update_or_create(
                    user=user,
                    defaults={
                        "onboarded_at": now,
                        "photo_share_consent": True,
                        "excluded_by_coach": False,
                    },
                )
            elif kind == "not_onboarded":
                CrushConnectMembership.objects.update_or_create(
                    user=user,
                    defaults={"onboarded_at": None, "photo_share_consent": False},
                )

            registration, _ = EventRegistration.objects.update_or_create(
                event=event,
                user=user,
                defaults={"status": "attended", "checked_in_at": now},
            )
            # Exercise the real post-check-in integration path.
            participation, _created = handle_checkin(registration)
            state = "in lobby" if participation else "checked in (no lobby)"
            self.stdout.write(f"  {username} / {password} — {kind}: {state}")

        recap_event = self._seed_recap_event(now)
        self._seed_people_ive_met(now)

        launched = bool(
            getattr(settings, "CRUSH_CONNECT_LAUNCHED", False)
            or getattr(settings, "CRUSH_CONNECT_CANDIDATE_OPEN", False)
        )
        lobby_on = bool(getattr(settings, "CRUSH_EVENT_LOBBY_ENABLED", False))
        self.stdout.write("")
        self.stdout.write(
            f"Flags now: CRUSH_EVENT_LOBBY_ENABLED={lobby_on}  connect_phase_open={launched}"
        )
        if not (launched and lobby_on):
            self.stdout.write(
                self.style.WARNING(
                    "Set CRUSH_EVENT_LOBBY_ENABLED=true and CRUSH_CONNECT_LAUNCHED=true "
                    "in the environment, then (re)start runserver."
                )
            )
        self.stdout.write("")
        self.stdout.write(f"Live lobby:   /en/events/{event.pk}/lobby/")
        self.stdout.write(f"48h recap:    /en/events/{recap_event.pk}/lobby/")
        self.stdout.write("People I've Met: /en/crush-connect/people-ive-met/")
        self.stdout.write("Hub:          /en/crush-connect/home/ (live-lobby + People I've Met cards)")
        self.stdout.write("Login at /accounts/login/ with <username>@example.com / password above.")
        self.stdout.write(
            "  Demo: log in as lobby_alice — confirm lobby_chloe in the recap, "
            "then confirm alice back from lobby_chloe to mint a People-I've-Met entry."
        )

    # ------------------------------------------------------------------

    def _seed_recap_event(self, now):
        """A second event already inside its 48h recap window, with the three
        Connect members joined (participations created directly, since the
        event has ended) and one live mutual (alice↔chloe) pinned so the recap
        grid shows the 'You wanted to meet' highlight."""
        from datetime import timedelta

        from crush_lu.models import (
            EventLobbyParticipation,
            EventMeetSignal,
            EventRegistration,
            MeetupEvent,
        )

        title = "Event Lobby Recap Demo"
        # Ended 3h ago → inside the recap window (opens at end, closes +48h).
        start = now - timedelta(hours=5)
        event, _ = MeetupEvent.objects.update_or_create(
            title=title,
            defaults={
                "description": "Local demo event in its 48-hour recap window.",
                "event_type": "mixer",
                "location": "Demo Bar, Luxembourg",
                "address": "1 Demo Street, Luxembourg City",
                "canton": "Luxembourg",
                "date_time": start,
                "duration_minutes": 120,
                "max_participants": 30,
                "registration_deadline": start - timedelta(hours=1),
                "is_published": True,
                "is_cancelled": False,
                "profile_requirement": "none",
            },
        )
        connect_usernames = [
            u for u, (_f, _g, _c, kind) in DEMO_USERS.items() if kind == "connect"
        ]
        members = list(User.objects.filter(username__in=connect_usernames))
        joined_at = start + timedelta(minutes=10)
        for member in members:
            registration, _ = EventRegistration.objects.update_or_create(
                event=event,
                user=member,
                defaults={"status": "attended", "checked_in_at": joined_at},
            )
            # Direct create — the event has already ended, so the real
            # check-in path would (correctly) refuse; this simulates a join
            # that happened while it was live.
            EventLobbyParticipation.objects.update_or_create(
                event_registration=registration,
                defaults={
                    "event": event,
                    "user": member,
                    "joined_at": joined_at,
                    "eligibility_source": "checkin",
                },
            )
        # A live mutual alice↔chloe (both signalled during the event).
        by_name = {m.username: m for m in members}
        alice = by_name.get("lobby_alice")
        chloe = by_name.get("lobby_chloe")
        if alice and chloe:
            revealed = joined_at + timedelta(minutes=20)
            for sender, recipient in [(alice, chloe), (chloe, alice)]:
                EventMeetSignal.objects.update_or_create(
                    event=event,
                    sender=sender,
                    recipient=recipient,
                    defaults={"mutual_revealed_at": revealed},
                )
        self.stdout.write("")
        self.stdout.write(
            f"Recap event:  {event.title} (id={event.pk}) — in 48h recap window"
        )
        return event

    def _seed_people_ive_met(self, now):
        """One pre-existing permanent encounter (alice↔ben) so People I've Met
        has content and the live grid shows the 'You've already met' tile."""
        from crush_lu.models import ConfirmedEncounter, MeetupEvent

        alice = User.objects.filter(username="lobby_alice").first()
        ben = User.objects.filter(username="lobby_ben").first()
        if not (alice and ben):
            return
        low, high = ConfirmedEncounter.canonical_pair(alice, ben)
        source = MeetupEvent.objects.filter(title=DEMO_EVENT_TITLE).first()
        ConfirmedEncounter.objects.update_or_create(
            user_low=low,
            user_high=high,
            defaults={"created_from_event": source, "status": "active"},
        )
        self.stdout.write("People I've Met: seeded 1 encounter (lobby_alice ↔ lobby_ben)")

    # ------------------------------------------------------------------

    def _ensure_user(self, username, first_name, password):
        from allauth.account.models import EmailAddress

        from crush_lu.models import UserDataConsent

        email = f"{username}@example.com"
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "first_name": first_name, "last_name": "Demo"},
        )
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])
        # Recent last_login + verified email + GDPR consent so login flows
        # and Connect gates behave exactly like a real member's.
        User.objects.filter(pk=user.pk).update(last_login=timezone.now())
        EmailAddress.objects.update_or_create(
            user=user,
            email=email,
            defaults={"verified": True, "primary": True},
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        user.refresh_from_db()
        return user

    def _ensure_profile(self, user, gender, color, kind):
        from allauth.socialaccount.models import SocialAccount

        from crush_lu.models import CrushProfile

        verified = kind != "non_connect"
        profile, _ = CrushProfile.objects.update_or_create(
            user=user,
            defaults={
                "date_of_birth": date(1994, 6, 15),
                "gender": gender,
                "location": "Luxembourg City",
                "bio": "Event Lobby demo profile (local dev only).",
                "interests": "music, hiking",
                "is_active": True,
                "is_approved": verified,
                "verification_status": "verified" if verified else "incomplete",
                "verification_method": "admin" if verified else "",
                "event_languages": ["en"],
            },
        )
        if not profile.photo_1:
            profile.photo_1.save(f"{user.pk}_lobby_demo.jpg", _demo_photo(color), save=True)
        if kind != "non_connect":
            SocialAccount.objects.get_or_create(
                user=user,
                provider="luxid",
                uid=f"luxid-demo-{user.pk}",
                defaults={"extra_data": {"sub": f"luxid-demo-{user.pk}"}},
            )
        return profile
