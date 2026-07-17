from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.files.base import ContentFile
from allauth.socialaccount.models import SocialAccount

from crush_lu.models import CrushProfile, CrushConnectMembership, EventRegistration, MeetupEvent, CrushCoach, UserDataConsent
from crush_connect_lobby.models import EventLobbyParticipation, EventMeetSignal
from crush_connect_lobby.services import evaluate_and_join_lobby

User = get_user_model()


class Command(BaseCommand):
    help = "Seeds database with test data for the Event Lobby prototype demo."

    def handle(self, *args, **options):
        self.stdout.write("Seeding Event Lobby test data...")

        # 1. Create coach
        coach_user, _ = User.objects.get_or_create(
            username="cc_coach",
            defaults={"email": "cc_coach@example.com", "first_name": "Coach"},
        )
        if coach_user.is_staff is False:
            coach_user.is_staff = True
            coach_user.save()
        coach, _ = CrushCoach.objects.get_or_create(
            user=coach_user,
            defaults={
                "bio": "Crush Connect Coach",
                "specializations": "General",
                "phone_number": "+352123456",
                "is_active": True,
            },
        )

        # 2. Create the target event (Live Mixer starting 30 mins ago)
        event, _ = MeetupEvent.objects.get_or_create(
            title="Luxembourg Social Mixer",
            defaults={
                "description": "Premium social mixer for Crush Connect members.",
                "event_type": "mixer",
                "date_time": timezone.now() - timedelta(minutes=30),
                "duration_minutes": 120,
                "location": "Grounded Cafe",
                "address": "45 Route d'Esch, Luxembourg",
                "max_participants": 30,
                "registration_deadline": timezone.now() - timedelta(days=2),
                "is_published": True,
                "is_cancelled": False,
            }
        )

        # 3. Create or update verifyghost@example.com (The member we log in as)
        ghost_user, created = User.objects.get_or_create(
            username="verifyghost",
            defaults={
                "email": "verifyghost@example.com",
                "first_name": "Tom",
                "last_name": "Ghost",
            }
        )
        ghost_user.set_password("verify-pass-123")
        ghost_user.save()

        # Data Consent
        consent, _ = UserDataConsent.objects.get_or_create(user=ghost_user)
        consent.crushlu_consent_given = True
        consent.save()

        # Profile
        ghost_profile, _ = CrushProfile.objects.get_or_create(
            user=ghost_user,
            defaults={
                "date_of_birth": date(1995, 8, 20),
                "gender": "M",
                "location": "Luxembourg",
                "is_approved": True,
                "verification_status": "verified",
                "verification_method": "luxid",
            }
        )
        ghost_profile.verification_status = "verified"
        ghost_profile.is_approved = True
        if not ghost_profile.photo_1:
            dummy_pixel = b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\x00\x00\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
            ghost_profile.photo_1.save("verifyghost_photo.gif", ContentFile(dummy_pixel), save=False)
        ghost_profile.save()

        # LuxID
        SocialAccount.objects.get_or_create(user=ghost_user, provider="luxid", defaults={"uid": "verifyghost_uid"})

        # Membership
        ghost_membership, _ = CrushConnectMembership.objects.get_or_create(
            user=ghost_user,
            defaults={
                "onboarded_at": timezone.now(),
                "lobby_consent_given": True,
                "photo_share_consent": True,
                "relationship_goal": "serious",
                "lifestyle_energy": "mix",
                "lifestyle_social": "flexible",
            }
        )
        ghost_membership.onboarded_at = timezone.now()
        ghost_membership.lobby_consent_given = True
        ghost_membership.photo_share_consent = True
        ghost_membership.save()

        # Registration & Check-in
        ghost_reg, _ = EventRegistration.objects.get_or_create(
            event=event,
            user=ghost_user,
            defaults={"status": "attended", "payment_confirmed": True, "checked_in_at": timezone.now()}
        )
        ghost_reg.status = "attended"
        ghost_reg.save()

        # Clean old lobby participation/signals for a fresh run
        EventLobbyParticipation.objects.filter(event=event).delete()
        EventMeetSignal.objects.filter(event=event).delete()

        # Join Event Lobby
        evaluate_and_join_lobby(ghost_user, event, source="checkin")

        # 4. Create other mock attendees in lobby
        names = ["Alice", "Bob", "Clara", "Daniel"]
        mock_users = []
        for i, name in enumerate(names):
            username = name.lower()
            u, _ = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@example.com",
                    "first_name": name,
                    "last_name": "Test",
                }
            )
            u.set_password("password123")
            u.save()

            u_consent, _ = UserDataConsent.objects.get_or_create(user=u)
            u_consent.crushlu_consent_given = True
            u_consent.crushlu_banned = False
            u_consent.save()

            u_profile, _ = CrushProfile.objects.get_or_create(
                user=u,
                defaults={
                    "date_of_birth": date(1996 - i, 4, 12),
                    "gender": "F" if i % 2 == 0 else "M",
                    "location": "Luxembourg",
                    "is_approved": True,
                    "verification_status": "verified",
                    "verification_method": "luxid",
                }
            )
            u_profile.verification_status = "verified"
            u_profile.is_approved = True
            if not u_profile.photo_1:
                dummy_pixel = b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\x00\x00\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
                u_profile.photo_1.save(f"{username}_photo.gif", ContentFile(dummy_pixel), save=False)
            u_profile.save()

            SocialAccount.objects.get_or_create(user=u, provider="luxid", defaults={"uid": f"{username}_uid"})

            u_membership, _ = CrushConnectMembership.objects.get_or_create(
                user=u,
                defaults={
                    "onboarded_at": timezone.now(),
                    "lobby_consent_given": True,
                    "photo_share_consent": True,
                    "relationship_goal": "open",
                    "lifestyle_energy": "adventurer",
                }
            )
            u_membership.onboarded_at = timezone.now()
            u_membership.lobby_consent_given = True
            u_membership.photo_share_consent = True
            u_membership.excluded_by_coach = False
            u_membership.save()

            u_reg, _ = EventRegistration.objects.get_or_create(
                event=event,
                user=u,
                defaults={"status": "attended", "payment_confirmed": True, "checked_in_at": timezone.now()}
            )
            u_reg.status = "attended"
            u_reg.save()

            evaluate_and_join_lobby(u, event, source="checkin")
            mock_users.append(u)

        # 5. Pre-seed signals
        # Alice (mock_users[0]) signals verifyghost
        EventMeetSignal.objects.get_or_create(event=event, sender=mock_users[0], recipient=ghost_user)

        # Bob (mock_users[1]) and verifyghost mutually signal
        EventMeetSignal.objects.get_or_create(event=event, sender=mock_users[1], recipient=ghost_user)
        EventMeetSignal.objects.get_or_create(
            event=event,
            sender=ghost_user,
            recipient=mock_users[1],
            defaults={"mutual_revealed_at": timezone.now()}
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded database! Event ID: {event.id}. "
                "Log in as verifyghost@example.com / verify-pass-123 to test."
            )
        )
