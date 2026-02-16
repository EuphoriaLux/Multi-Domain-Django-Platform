"""
Populate a past event with attendees for testing the attendees/connections/sparks pages.

Registers sample profiles to an event and marks them as 'attended',
so the post-event attendees page has realistic data to display.

Usage:
    python manage.py populate_event_attendees
    python manage.py populate_event_attendees --event-id 18
    python manage.py populate_event_attendees --count 15
"""

import random

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone

from crush_lu.models import CrushProfile, MeetupEvent, EventRegistration


class Command(BaseCommand):
    help = "Populate a past event with attended registrations for testing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--event-id",
            type=int,
            default=None,
            help="Specific event ID to populate (default: most recent past event)",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=15,
            help="Number of attendees to register (default: 15)",
        )
        parser.add_argument(
            "--include-current-user",
            type=str,
            default=None,
            help="Username or email to also register as attended (e.g. your test account)",
        )

    def handle(self, *args, **options):
        event_id = options["event_id"]
        count = options["count"]
        include_user = options["include_current_user"]

        # Find or select event
        if event_id:
            try:
                event = MeetupEvent.objects.get(id=event_id)
            except MeetupEvent.DoesNotExist:
                self.stderr.write(f"Event {event_id} not found.")
                return
        else:
            # Pick the most recent event (past or future - doesn't matter for testing)
            event = MeetupEvent.objects.order_by("-date_time").first()
            if not event:
                self.stderr.write("No events found. Run create_sample_events first.")
                return

        self.stdout.write(f"\nPopulating event: {event.title} (ID: {event.id})")
        self.stdout.write(f"  Date: {event.date_time}")

        # Get profiles with photos first, then without
        profiles_with_photos = list(
            CrushProfile.objects.filter(
                photo_1__isnull=False, is_approved=True
            )
            .exclude(photo_1="")
            .select_related("user")
        )
        profiles_without_photos = list(
            CrushProfile.objects.filter(
                is_approved=True
            )
            .exclude(
                pk__in=[p.pk for p in profiles_with_photos]
            )
            .select_related("user")
        )

        self.stdout.write(
            f"  Available profiles: {len(profiles_with_photos)} with photos, "
            f"{len(profiles_without_photos)} without"
        )

        # Prioritize profiles with photos
        all_profiles = profiles_with_photos + profiles_without_photos
        random.shuffle(profiles_with_photos)
        random.shuffle(profiles_without_photos)
        selected = profiles_with_photos[:count]
        if len(selected) < count:
            remaining = count - len(selected)
            selected.extend(profiles_without_photos[:remaining])

        created = 0
        for profile in selected:
            reg, was_created = EventRegistration.objects.get_or_create(
                event=event,
                user=profile.user,
                defaults={
                    "status": "attended",
                    "registered_at": timezone.now(),
                },
            )
            if was_created:
                created += 1
            elif reg.status != "attended":
                reg.status = "attended"
                reg.save(update_fields=["status"])

        # Also add the specified user if provided
        if include_user:
            try:
                user = User.objects.get(
                    username=include_user
                ) if "@" not in include_user else User.objects.get(
                    email=include_user
                )
                reg, was_created = EventRegistration.objects.get_or_create(
                    event=event,
                    user=user,
                    defaults={
                        "status": "attended",
                        "registered_at": timezone.now(),
                    },
                )
                if was_created:
                    created += 1
                elif reg.status != "attended":
                    reg.status = "attended"
                    reg.save(update_fields=["status"])
                self.stdout.write(
                    self.style.SUCCESS(f"  Added {user.username} as attendee")
                )
            except User.DoesNotExist:
                self.stderr.write(f"  Warning: User '{include_user}' not found")

        total = EventRegistration.objects.filter(
            event=event, status="attended"
        ).count()

        self.stdout.write(
            self.style.SUCCESS(
                f"\n  Created {created} new registrations "
                f"({total} total attendees for this event)"
            )
        )
        self.stdout.write(
            f"\n  Test it: http://localhost:8000/en/events/{event.id}/attendees/"
        )
