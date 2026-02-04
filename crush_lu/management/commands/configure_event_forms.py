"""
Management command to configure existing events with new registration form settings.
"""
from django.core.management.base import BaseCommand
from crush_lu.models import MeetupEvent


class Command(BaseCommand):
    help = "Configure existing events with food component and plus-one settings"

    def handle(self, *args, **options):
        events = MeetupEvent.objects.all()
        updated = 0

        for event in events:
            # Determine if event has food based on type
            if event.event_type in ["mixer", "themed"]:
                event.has_food_component = True
            else:
                event.has_food_component = False

            # Allow plus-ones for mixers and some themed events
            if event.event_type in ["mixer", "themed"]:
                event.allow_plus_ones = True
            else:
                event.allow_plus_ones = False

            event.save()
            updated += 1

            self.stdout.write(
                f"- {event.title} ({event.event_type}): "
                f"Food={event.has_food_component}, Plus-ones={event.allow_plus_ones}"
            )

        self.stdout.write(
            self.style.SUCCESS(f"\nUpdated {updated} event(s) successfully")
        )
