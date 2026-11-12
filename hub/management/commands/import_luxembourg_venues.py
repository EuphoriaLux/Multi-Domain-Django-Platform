"""Management command to extract & import Luxembourg restaurants and bars into Hub.

Usage:
    # Import restaurants, bars, pubs and cafés from OpenStreetMap
    python manage.py import_luxembourg_venues

    # Also fill in missing details from echo.lu. This never adds a venue from
    # the live echo.lu registry: it only enriches venues that already exist.
    python manage.py import_luxembourg_venues --with-echo-lu

    # Preview: runs the whole import and rolls it back, so the counts are exact
    python manage.py import_luxembourg_venues --dry-run

    # Deterministic offline import from a saved Overpass JSON file
    python manage.py import_luxembourg_venues --osm-file path/to/overpass.json

Spec: ai-memory-hub/specs/2026-11-12-crush-hub-venue-extraction-and-claiming.md
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from crush_lu.models.echo_lu import EchoVenue
from crush_lu.services import echo_lu
from hub.venue_import import (
    fetch_osm_luxembourg_elements,
    load_osm_elements_from_file,
    normalize_postcode,
    osm_external_id,
    parse_echo_lu_venue_record,
    parse_osm_element,
    split_osm_elements,
    sync_cached_echo_venues_to_locations,
    upsert_venue_record,
)

# The registry held 5k+ venues in 2026-11. The first production run read the
# old default of 20 pages and stopped at the letter E without saying so.
DEFAULT_ECHO_PAGES = 80


class Command(BaseCommand):
    help = (
        "Import Luxembourg restaurants, bars, pubs, and cafés from OpenStreetMap "
        "into hub.Location (Prospect stage, unclaimed). --with-echo-lu enriches "
        "them from echo.lu without adding venues from its live registry."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--osm-file",
            help="Path to a local Overpass JSON file (skips live network call).",
        )
        parser.add_argument(
            "--skip-osm",
            action="store_true",
            help="Skip OpenStreetMap extraction and only process echo.lu.",
        )
        parser.add_argument(
            "--with-echo-lu",
            action="store_true",
            help=(
                "Also create venues for the echo.lu mappings already linked by "
                "hand (EchoVenue), and fill missing details of existing venues "
                "from the live echo.lu registry. Registry records that match "
                "no venue are counted and left out."
            ),
        )
        parser.add_argument(
            "--echo-pages",
            type=int,
            default=DEFAULT_ECHO_PAGES,
            help=(
                "Maximum 100-row pages to read from the live echo.lu registry "
                f"(default {DEFAULT_ECHO_PAGES}; each page takes 3-4 seconds)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the whole import, report the counts, then roll it back.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        osm_file = options.get("osm_file")
        skip_osm = bool(options.get("skip_osm"))
        with_echo_lu = bool(options.get("with_echo_lu"))
        echo_pages = max(1, int(options.get("echo_pages") or DEFAULT_ECHO_PAGES))

        # Every network read happens before the first write. The Overpass call
        # can take 200 s and each echo.lu page 3-4 s, far too long to hold a
        # transaction open, and a source that fails must leave nothing behind.
        elements = None
        if not skip_osm:
            if osm_file:
                self.stdout.write(f"Loading OSM elements from {osm_file}…")
                try:
                    elements = load_osm_elements_from_file(osm_file)
                except Exception as exc:
                    raise CommandError(f"Failed to read {osm_file}: {exc}") from exc
            else:
                self.stdout.write(
                    "Querying OpenStreetMap Overpass API for Luxembourg restaurants & bars…"
                )
                try:
                    elements = fetch_osm_luxembourg_elements()
                except Exception as exc:
                    raise CommandError(f"Overpass query failed: {exc}") from exc

        echo_records = None
        if with_echo_lu:
            client = echo_lu.EchoLuClient()
            if getattr(client, "api_key", None):
                self.stdout.write(
                    f"Reading live echo.lu venue registry (up to {echo_pages} pages)…"
                )
                try:
                    echo_records = list(
                        echo_lu.iter_venue_records(client, None, max_pages=echo_pages)
                    )
                except echo_lu.EchoLuError as exc:
                    raise CommandError(f"echo.lu registry read failed: {exc}") from exc
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "ECHO_LU_API_KEY not set; used cached EchoVenue rows only."
                    )
                )

        totals = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "ignored": 0,
            "conflicts": 0,
            "not_imported": 0,
        }

        # One transaction, so a dry run is the real import rolled back. Its
        # counts are then exact: an echo.lu record sees the venues the OSM pass
        # has just created, which a write-nothing preview could not show.
        with transaction.atomic():
            if elements is not None:
                self._import_osm(elements, totals)
            if with_echo_lu:
                self.stdout.write("Syncing cached EchoVenue rows to hub.Location…")
                cached_counts = sync_cached_echo_venues_to_locations()
                for k in ("created", "updated", "skipped"):
                    totals[k] += cached_counts[k]
            if echo_records is not None:
                self._enrich_from_echo_lu(echo_records, totals)
            if dry_run:
                transaction.set_rollback(True)

        if echo_records is not None and len(echo_records) >= echo_pages * 100:
            self.stdout.write(
                self.style.WARNING(
                    f"echo.lu registry read stopped at the {echo_pages}-page cap "
                    f"({len(echo_records)} venues): the rest of the registry was "
                    "NOT read. Re-run with a higher --echo-pages."
                )
            )

        prefix = "[DRY-RUN, rolled back] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Done: {totals['created']} created, "
                f"{totals['updated']} updated, "
                f"{totals['skipped']} unchanged, "
                f"{totals['ignored']} ignored, "
                f"{totals['not_imported']} echo.lu record(s) matching no venue "
                "left out, "
                f"{totals['conflicts']} echo.lu id conflict(s) left for review."
            )
        )

    def _import_osm(self, elements, totals):
        venues, communes_by_id = split_osm_elements(elements)
        self.stdout.write(
            f"Fetched {len(venues)} OSM venue element(s); "
            f"{len(communes_by_id)} placed in a commune."
        )
        if venues and not communes_by_id:
            self.stdout.write(
                self.style.WARNING(
                    "No commune boundaries in this OSM data: venues without "
                    "a postcode or city get a blank town and canton, which "
                    "a later run with boundaries fills in."
                )
            )
        for element in venues:
            parsed = parse_osm_element(
                element, communes_by_id.get(osm_external_id(element), ())
            )
            if parsed is None:
                totals["ignored"] += 1
                continue
            action, _ = upsert_venue_record(parsed)
            totals[action] += 1

    def _enrich_from_echo_lu(self, records, totals):
        """Fill missing details of existing venues from the echo.lu registry.

        Two things this must never do, both learned on the first production
        run (2026-11-13), which had to be cleaned up by hand:

        * **Create a venue.** The registry holds every kind of place and this
          scan has no category to filter on, so 1,832 schools, halls and bare
          street addresses landed in the coaches' directory.
        * **Create an ``EchoVenue`` row.** The event sync resolves venue ids
          through that table (``echo_lu._linked_venue_id``) and each row there
          is meant to be a link a person made. A scan that adds one per
          registry venue silently auto-links future events.

        An existing mapping is still honoured: its venue is linked, and a
        second registry id for the same key is reported and skipped.
        """
        for record in records:
            parsed = parse_echo_lu_venue_record(record)
            if parsed is None:
                totals["ignored"] += 1
                continue
            venue_id = echo_lu.extract_experience_id(record)
            if venue_id:
                key = echo_lu.venue_key(
                    parsed["name"],
                    normalize_postcode(parsed["address_postcode"]),
                )
                mapped = EchoVenue.objects.filter(key=key).first()
                if mapped is not None and mapped.venue_id != str(venue_id):
                    totals["conflicts"] += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"echo.lu id conflict for '{key}': kept "
                            f"{mapped.venue_id}, ignored {venue_id}."
                        )
                    )
                    continue
                parsed["echo_venue"] = mapped
            action, _ = upsert_venue_record(parsed, create=False)
            totals[action] += 1
