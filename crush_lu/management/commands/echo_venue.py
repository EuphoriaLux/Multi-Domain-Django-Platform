"""Link a Crush.lu venue name to an echo.lu venue id.

echo.lu's ``venues`` field takes ids from its own registry — a shared national
table of 5,000+ rows that every organiser reads and writes. Two things follow,
and together they are why this command exists instead of the sync doing it:

* **We never register a venue.** ``CreateVenue`` wants a description, a
  category, a website and a contact, and for premises we merely book the only
  values we hold are Crush.lu's own. Registering under them would publish
  somebody else's venue with our contact on it.
* **We cannot usefully search for one at request time.** There is no text
  search, and paging 5,000 rows at 3-4 seconds a page takes minutes. That is
  fine here, in a shell, once per venue — and impossible inside an admin
  action that allows five seconds.

So the mapping is made once, by a person, and cached in ``EchoVenue``. After
that the sync path is a single indexed lookup with no network at all.

Usage:
    # Find the id (slow, exhaustive, read-only)
    python manage.py echo_venue --search "MAIN Experience"

    # Record it, keyed the same way the sync looks it up
    python manage.py echo_venue --link "MAIN Experience" --postcode 1450 \\
        --venue-id main-experience-abc123

    # What is linked today
    python manage.py echo_venue --list

If the venue is not in the registry at all, create it in the echo.lu organiser
back office first — where it can be given a real description, the right
category and the venue's own contact details — then link the id it is given.
"""

from django.core.management.base import BaseCommand, CommandError

from crush_lu.services import echo_lu


class Command(BaseCommand):
    help = "Link a Crush.lu venue name to an echo.lu venue id."

    def add_arguments(self, parser):
        parser.add_argument(
            "--search",
            metavar="NAME",
            help=(
                "Search echo.lu's venue registry for NAME (case- and "
                "spacing-insensitive substring). Read-only and slow — it pages "
                "the whole registry."
            ),
        )
        parser.add_argument(
            "--link",
            metavar="LOCATION",
            help=(
                "The MeetupEvent.location string to map, exactly as events " "spell it"
            ),
        )
        parser.add_argument(
            "--postcode",
            default="",
            help=(
                "Postcode that goes with --link. The lookup key is name plus "
                "postcode, so this must match what parse_address extracts from "
                "the event's address, or the sync will not find the mapping. "
                "Check it with `sync_events_to_echo --event-id N --dry-run "
                "--show-payload`."
            ),
        )
        parser.add_argument("--venue-id", help="The echo.lu venue id to record")
        parser.add_argument(
            "--list", action="store_true", dest="show", help="Show every mapping"
        )
        parser.add_argument(
            "--pages",
            type=int,
            default=60,
            help="How many 100-row pages to read when searching (default 60)",
        )

    def handle(self, *args, **options):
        if options["show"]:
            return self._list()
        if options["search"]:
            return self._search(options["search"], options["pages"])
        if options["link"]:
            return self._link(options["link"], options["postcode"], options["venue_id"])
        raise CommandError("Pass one of --search, --link or --list.")

    def _list(self):
        from crush_lu.models.echo_lu import EchoVenue

        rows = list(EchoVenue.objects.all())
        if not rows:
            self.stdout.write("No venues linked yet.")
            return
        self.stdout.write(f"{len(rows)} linked venue(s):")
        for row in rows:
            self.stdout.write(f"  {row.key}\n      -> {row.venue_id}  {row.title}")

    def _search(self, name, pages):
        import re

        needle = re.sub(r"\s+", " ", name.strip().lower())
        client = echo_lu.EchoLuClient()
        self.stdout.write(
            f"Searching echo.lu's venue registry for {name!r} "
            f"(up to {pages} pages — this takes a few minutes)…"
        )

        found = 0
        scanned = 0
        for record in echo_lu.iter_venue_records(client, None, max_pages=pages):
            scanned += 1
            title = record.get("title") or ""
            if isinstance(title, dict):
                title = title.get("en") or next(iter(title.values()), "")
            address = (record.get("location") or {}).get("address") or {}
            haystack = re.sub(r"\s+", " ", str(title).strip().lower())
            if needle in haystack:
                found += 1
                where = " ".join(
                    str(address.get(k, ""))
                    for k in ("street", "number", "postcode", "town")
                ).strip()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {echo_lu.extract_experience_id(record)}\n"
                        f"      {title}\n"
                        f"      {where}"
                    )
                )

        self.stdout.write(f"\nScanned {scanned} venue(s).")
        if not found:
            self.stdout.write(
                self.style.WARNING(
                    "No match. If the venue really is not on echo.lu, create it "
                    "in the organiser back office — with its own description, "
                    "category and contact details, not ours — then link the id "
                    "it is given."
                )
            )

    def _link(self, location, postcode, venue_id):
        from crush_lu.models.echo_lu import EchoVenue

        if not venue_id:
            raise CommandError("--link needs --venue-id")

        key = echo_lu.venue_key(location, postcode)
        clash = EchoVenue.objects.filter(key=key).first()
        row, created = EchoVenue.objects.update_or_create(
            key=key,
            defaults={"venue_id": venue_id.strip(), "title": location},
        )
        verb = "Linked" if created else "Re-linked"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {location!r} (key {key}) -> {row.venue_id}")
        )
        if clash and clash.venue_id != row.venue_id:
            self.stdout.write(
                self.style.WARNING(
                    f"  was {clash.venue_id}. Events already published against "
                    f"the old venue keep it until they are re-synced."
                )
            )
        self.stdout.write(
            "Check the key matches what the event produces:\n"
            "  manage.py sync_events_to_echo --event-id N --dry-run --show-payload"
        )
