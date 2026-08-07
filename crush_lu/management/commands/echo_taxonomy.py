"""
List echo.lu's category / audience / format / environment vocabularies.

echo.lu validates these facets against its own controlled vocabularies and
rejects the *entire* experience when a value is unknown, so the slugs cannot
be guessed — they have to be read off the API for our organisation's key. That
is why ECHO_LU_DEFAULT_* ship empty: an empty facet is accepted, an invented
one is not.

Usage:
    # Print every vocabulary
    python manage.py echo_taxonomy

    # One vocabulary
    python manage.py echo_taxonomy --kind categories

    # Verify the slugs currently configured in ECHO_LU_DEFAULT_* / the
    # per-event-type category map actually exist
    python manage.py echo_taxonomy --check

Read-only — this command never writes to echo.lu, so it works with the sync
switch off as long as ECHO_LU_API_KEY is set.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from crush_lu.services import echo_lu


class Command(BaseCommand):
    help = "Show the category/audience/format/environment slugs echo.lu accepts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            choices=echo_lu.EchoLuClient.TAXONOMIES,
            help="Only fetch one vocabulary (default: all of them)",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Verify the configured ECHO_LU_DEFAULT_* slugs exist upstream",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Print the raw API response instead of a slug list",
        )

    def handle(self, *args, **options):
        # Read-only, so this needs the key but NOT the sync switch — the point
        # of the command is to get the configuration right before turning
        # syncing on.
        if not getattr(settings, "ECHO_LU_API_KEY", ""):
            raise CommandError(
                "ECHO_LU_API_KEY is not set. Issue a key from the echo.lu "
                "organiser back office and put it in the environment."
            )

        client = echo_lu.EchoLuClient()
        kinds = [options["kind"]] if options["kind"] else list(client.TAXONOMIES)

        available = {}
        for kind in kinds:
            try:
                response = client.list_taxonomy(kind)
            except echo_lu.EchoLuError as exc:
                self.stdout.write(self.style.ERROR(f"{kind}: {exc}"))
                continue

            if options["as_json"]:
                self.stdout.write(
                    json.dumps(response, indent=2, ensure_ascii=False, default=str)
                )
                continue

            slugs = _extract_slugs(response)
            available[kind] = slugs
            self.stdout.write(self.style.SUCCESS(f"\n{kind} ({len(slugs)}):"))
            for slug, label in slugs.items():
                self.stdout.write(f"  {slug}" + (f"  — {label}" if label else ""))

        if options["check"]:
            self._check_configured(available)

    def _check_configured(self, available):
        """Compare what we are configured to send against what exists."""
        configured = {
            "categories": _split(settings.ECHO_LU_DEFAULT_CATEGORIES),
            "audiences": _split(settings.ECHO_LU_DEFAULT_AUDIENCES),
            "formats": _split(settings.ECHO_LU_DEFAULT_FORMATS),
            "environments": _split(settings.ECHO_LU_DEFAULT_ENVIRONMENTS),
        }

        # The per-event-type map contributes category slugs too, and those are
        # the ones most likely to be typo'd because they are edited least.
        raw_map = getattr(settings, "ECHO_LU_CATEGORY_MAP", "") or ""
        malformed_map = False
        if raw_map:
            try:
                mapping = json.loads(raw_map)
            except ValueError:
                # Counts as a failure, not a note. Runtime discards the whole
                # map silently, so every per-event-type category is missing —
                # and a check that still exits 0 says that is fine.
                malformed_map = True
                self.stdout.write(
                    self.style.ERROR(
                        "\nECHO_LU_CATEGORY_MAP is not valid JSON — it is being "
                        "ignored at runtime, so per-event-type categories are "
                        "not reaching echo.lu."
                    )
                )
                mapping = {}
            for values in (mapping or {}).values():
                if isinstance(values, str):
                    values = [values]
                configured["categories"].extend(values or [])

        problems = 0
        unchecked = []
        self.stdout.write(self.style.SUCCESS("\nConfigured slug check:"))
        for kind, values in configured.items():
            if kind not in available:
                # Either the fetch failed or this run never asked for it
                # (--kind narrows the set, --json skips parsing). Passing over
                # it silently is how "all configured slugs exist" ends up
                # printed for a facet nobody looked at — and one bad slug
                # rejects the whole experience, so a check that cannot see a
                # facet has not passed, it has not run.
                if values:
                    unchecked.append(kind)
                continue
            for value in dict.fromkeys(values):
                if value in available[kind]:
                    self.stdout.write(f"  ✓ {kind}: {value}")
                else:
                    problems += 1
                    self.stdout.write(
                        self.style.ERROR(f"  ✗ {kind}: {value} — not in echo.lu")
                    )

        if unchecked:
            self.stdout.write(
                self.style.ERROR(
                    f"\nNot checked: {', '.join(sorted(unchecked))}. These have "
                    "configured slugs but echo.lu's vocabulary was not read — "
                    "the request failed, or --kind/--json skipped it. Re-run "
                    "`echo_taxonomy --check` on its own before enabling sync."
                )
            )

        if problems:
            self.stdout.write(
                self.style.ERROR(
                    f"\n{problems} unknown slug(s). echo.lu rejects the whole "
                    "experience on any one of them, so every sync will fail "
                    "until these are corrected or removed."
                )
            )

        if problems or unchecked or malformed_map:
            # Non-zero exit: this command's whole job is to be the gate before
            # ECHO_LU_SYNC_ENABLED goes true, and a gate that exits 0 on an
            # incomplete check is one an operator reads as a green light.
            detail = [f"{problems} unknown", f"{len(unchecked)} facet(s) unverified"]
            if malformed_map:
                detail.append("ECHO_LU_CATEGORY_MAP unparseable")
            raise CommandError(f"Slug check did not pass: {', '.join(detail)}.")

        self.stdout.write(self.style.SUCCESS("  all configured slugs exist"))


def _split(raw):
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _extract_slugs(response):
    """Normalise a vocabulary response into {slug: label}.

    The vocabulary endpoints are not documented in the same detail as
    /experiences, and the sandbox and production versions differ, so accept the
    shapes that actually turn up — a bare list of strings, a list of objects, or
    either of those wrapped in a `data`/`items` envelope — rather than assuming
    one and printing nothing useful when it is the other.
    """
    if isinstance(response, dict):
        for key in ("data", "items", "results"):
            if isinstance(response.get(key), list):
                response = response[key]
                break
        else:
            # A plain {slug: label} mapping.
            return {str(k): str(v) for k, v in response.items()}

    slugs = {}
    for entry in response or []:
        if isinstance(entry, str):
            slugs[entry] = ""
            continue
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug") or entry.get("id") or entry.get("code") or ""
        label = entry.get("name") or entry.get("label") or entry.get("title") or ""
        if isinstance(label, dict):
            # Vocabulary labels are translated; show whichever we can read.
            label = label.get("en") or next(iter(label.values()), "")
        if slug:
            slugs[str(slug)] = str(label)
    return slugs
