"""Management command to extract & import Luxembourg restaurants and bars into Hub.

Usage:
    # Pull live from OpenStreetMap Overpass API + cached/live echo.lu venues
    python manage.py import_luxembourg_venues --with-echo-lu

    # Dry-run preview
    python manage.py import_luxembourg_venues --dry-run

    # Deterministic offline import from a saved Overpass JSON file
    python manage.py import_luxembourg_venues --osm-file path/to/overpass.json

Spec: ai-memory-hub/specs/2026-11-12-crush-hub-venue-extraction-and-claiming.md
"""

from django.core.management.base import BaseCommand, CommandError

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


class Command(BaseCommand):
    help = (
        "Import Luxembourg restaurants, bars, pubs, and cafés from OpenStreetMap "
        "and echo.lu into hub.Location (Prospect stage, unclaimed)."
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
            help="Also link/import venues from EchoVenue and live echo.lu API if configured.",
        )
        parser.add_argument(
            "--echo-pages",
            type=int,
            default=20,
            help="Maximum 100-row pages to read from live echo.lu API (default 20).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and match venues without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        osm_file = options.get("osm_file")
        skip_osm = bool(options.get("skip_osm"))
        with_echo_lu = bool(options.get("with_echo_lu"))
        echo_pages = int(options.get("echo_pages") or 20)

        totals = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "ignored": 0,
            "conflicts": 0,
        }

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
                action, _ = upsert_venue_record(parsed, dry_run=dry_run)
                totals[action] += 1

        if with_echo_lu:
            self.stdout.write("Syncing cached EchoVenue rows to hub.Location…")
            cached_counts = sync_cached_echo_venues_to_locations(dry_run=dry_run)
            for k in ("created", "updated", "skipped"):
                totals[k] += cached_counts[k]

            client = echo_lu.EchoLuClient()
            if getattr(client, "api_key", None):
                self.stdout.write(
                    f"Scanning live echo.lu venue registry (up to {echo_pages} pages)…"
                )
                for record in echo_lu.iter_venue_records(
                    client, None, max_pages=echo_pages
                ):
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
                        # The event sync resolves venue ids through these rows
                        # (echo_lu._linked_venue_id), so a directory scan must
                        # never repoint an existing mapping. A second registry
                        # id for the same key is reported for review and its
                        # record skipped, so it cannot become a duplicate venue.
                        if dry_run:
                            mapped = EchoVenue.objects.filter(key=key).first()
                        else:
                            mapped, _ = EchoVenue.objects.get_or_create(
                                key=key,
                                defaults={
                                    "venue_id": str(venue_id),
                                    "title": parsed["name"],
                                },
                            )
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
                    action, _ = upsert_venue_record(parsed, dry_run=dry_run)
                    totals[action] += 1
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "ECHO_LU_API_KEY not set; used cached EchoVenue rows only."
                    )
                )

        prefix = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Done: {totals['created']} created, "
                f"{totals['updated']} updated, "
                f"{totals['skipped']} unchanged, "
                f"{totals['ignored']} ignored, "
                f"{totals['conflicts']} echo.lu id conflict(s) left for review."
            )
        )
