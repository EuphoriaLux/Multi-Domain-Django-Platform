"""Preview possible partner matches; apply only explicit reviewed ID mappings."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from crush_lu.models import MeetupEvent
from hub.models import Location


def normalized_name(value):
    return " ".join(value.casefold().split())


class Command(BaseCommand):
    help = "Preview partner matches, or apply reviewed --link EVENT_ID:PARTNER_ID mappings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--link", action="append", default=[], metavar="EVENT:PARTNER"
        )
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        mappings = {}
        for value in options["link"]:
            try:
                event_id, partner_id = map(int, value.split(":"))
                if min(event_id, partner_id) < 1 or event_id in mappings:
                    raise ValueError
            except ValueError as exc:
                raise CommandError(
                    "Use unique positive EVENT_ID:PARTNER_ID mappings."
                ) from exc
            mappings[event_id] = partner_id
        if options["apply"] and not mappings:
            raise CommandError("--apply requires explicit reviewed --link mappings.")

        if not mappings:
            partners = list(Location.objects.exclude(partnership_stage="Archived"))
            for event in MeetupEvent.objects.filter(partner__isnull=True).order_by(
                "pk"
            ):
                candidates = [
                    partner.pk
                    for partner in partners
                    if normalized_name(partner.name) == normalized_name(event.location)
                    and (
                        not event.address_postcode
                        or not partner.address_postcode
                        or event.address_postcode == partner.address_postcode
                    )
                ]
                self.stdout.write(
                    f"Event {event.pk}: {event.location!r}; candidate partners: {candidates}"
                )
            self.stdout.write(
                "Preview only. Review candidates and pass explicit --link mappings."
            )
            return

        # Validate the complete batch before writing anything. Only attribution
        # columns change; no save(), publication signals or snapshot rewrites.
        with transaction.atomic():
            events = {
                event.pk: event
                for event in MeetupEvent.objects.select_for_update(of=("self",))
                .filter(pk__in=mappings)
                .order_by("pk")
            }
            partners = Location.objects.in_bulk(mappings.values())
            for event_id, partner_id in mappings.items():
                if event_id not in events or partner_id not in partners:
                    raise CommandError(
                        f"Unknown event or partner in {event_id}:{partner_id}."
                    )
                event = events[event_id]
                if event.partner_id not in (None, partner_id):
                    raise CommandError(
                        f"Event {event_id} already has a different partner."
                    )
                if event.offer_id and event.offer.location_id != partner_id:
                    raise CommandError(
                        f"Event {event_id} has an offer from another partner."
                    )
            for event_id, partner_id in mappings.items():
                if options["apply"]:
                    MeetupEvent.objects.filter(pk=event_id).update(
                        partner_id=partner_id
                    )
                self.stdout.write(
                    f"{'Linked' if options['apply'] else 'Would link'} event {event_id} -> partner {partner_id}"
                )
