import base64
from datetime import date, timedelta

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crush_event_lobby.services import acknowledge_consent, ensure_participation
from crush_lu.models import (
    CrushConnectMembership,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    UserDataConsent,
)

User = get_user_model()

DEMO_PASSWORD = "LobbyDemo123!"
DEMO_MEMBERS = (
    ("lobby.alex@example.com", "Alex"),
    ("lobby.blair@example.com", "Blair"),
    ("lobby.casey@example.com", "Casey"),
)
ONE_PIXEL_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")


class Command(BaseCommand):
    help = "Create an idempotent three-member live Event Lobby demo."

    def handle(self, *args, **options):
        from django.conf import settings

        if not settings.CRUSH_EVENT_LOBBY_ENABLED:
            raise CommandError(
                "Set CRUSH_EVENT_LOBBY_ENABLED=true before seeding the demo."
            )

        now = timezone.now()
        event = MeetupEvent.objects.filter(title="Prototype Event Lobby").first()
        if event is None:
            event = MeetupEvent(title="Prototype Event Lobby")
        event.description = "Local-only Crush Connect Event Lobby prototype."
        event.event_type = "mixer"
        event.location = "Luxembourg"
        event.address = "1 Prototype Place"
        event.date_time = now - timedelta(minutes=10)
        event.duration_minutes = 120
        event.max_participants = 20
        event.registration_deadline = now - timedelta(hours=1)
        event.is_published = True
        event.is_cancelled = False
        event.save()

        # PROTOTYPE-STUB: these synthetic members stand in for real LuxID-backed
        # attendees. No external identity provider or notification is contacted.
        for index, (email, first_name) in enumerate(DEMO_MEMBERS, start=1):
            user, _ = User.objects.get_or_create(
                username=email,
                defaults={"email": email, "first_name": first_name},
            )
            user.email = email
            user.first_name = first_name
            user.set_password(DEMO_PASSWORD)
            user.save()
            EmailAddress.objects.update_or_create(
                user=user,
                email=email,
                defaults={"verified": True, "primary": True},
            )
            SocialAccount.objects.update_or_create(
                provider="luxid",
                uid=f"event-lobby-{index}",
                defaults={"user": user},
            )

            profile, _ = CrushProfile.objects.get_or_create(user=user)
            profile.date_of_birth = date(1990 + index, 1, index)
            profile.gender = "NB"
            profile.location = "Luxembourg"
            profile.is_approved = True
            profile.is_active = True
            if not profile.photo_1:
                profile.photo_1.save(
                    f"event-lobby-{index}.gif",
                    ContentFile(ONE_PIXEL_GIF),
                    save=False,
                )
            profile.save()

            CrushConnectMembership.objects.update_or_create(
                user=user,
                defaults={
                    "onboarded_at": now,
                    "excluded_by_coach": False,
                    "photo_share_consent": True,
                    "onboarding_step": 8,
                },
            )
            UserDataConsent.objects.update_or_create(
                user=user, defaults={"crushlu_consent_given": True}
            )
            acknowledge_consent(user)
            registration, _ = EventRegistration.objects.update_or_create(
                event=event,
                user=user,
                defaults={"status": "attended", "checked_in_at": now},
            )
            ensure_participation(event, user)
            self.stdout.write(f"  {email} / {DEMO_PASSWORD}")

        self.stdout.write(self.style.SUCCESS("Event Lobby demo is ready."))
        self.stdout.write(
            f"Open http://crush.localhost:8000/en/crush-connect/event-lobby/{event.pk}/"
        )
