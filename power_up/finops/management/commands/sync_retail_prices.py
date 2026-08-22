"""Capture an immutable daily snapshot of public Azure retail prices."""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from power_up.finops.retail_prices import (
    DEFAULT_MAX_SECONDS,
    SnapshotAlreadyExists,
    sync_retail_prices,
)
from power_up.finops.retail_prices.connectors.azure import (
    AzureRetailPricesConnector,
    DEFAULT_EUROPEAN_REGIONS,
)


class Command(BaseCommand):
    help = (
        "Archive and normalize the Azure retail-price catalogue for selected European "
        "regions without overwriting daily history."
    )

    def add_arguments(self, parser):
        parser.add_argument("--currency", default="EUR")
        parser.add_argument(
            "--regions",
            nargs="+",
            default=DEFAULT_EUROPEAN_REGIONS,
            help="Azure ARM region codes (default: selected European regions).",
        )
        parser.add_argument(
            "--snapshot-date",
            help="Observation date in YYYY-MM-DD format (default: today).",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=AzureRetailPricesConnector.DEFAULT_TIMEOUT,
            help="Read timeout for a single page request, in seconds.",
        )
        parser.add_argument(
            "--max-seconds",
            type=float,
            default=DEFAULT_MAX_SECONDS,
            help=(
                "Budget for one region before it gives up. Keeps a web worker "
                "from being held past the caller's own timeout. Use 0 for no "
                "limit when running this by hand off the request path."
            ),
        )

    def handle(self, *args, **options):
        snapshot_date = None
        if options["snapshot_date"]:
            try:
                snapshot_date = date.fromisoformat(options["snapshot_date"])
            except ValueError as exc:
                raise CommandError("--snapshot-date must use YYYY-MM-DD.") from exc

        connector = AzureRetailPricesConnector(timeout=options["timeout"])
        regions = [region.lower() for region in options["regions"]]

        # One run per region. Each is small enough to finish inside a single
        # App Service request; the whole catalogue is not. A region that fails
        # is reported at the end without discarding the regions that worked.
        captured = skipped = 0
        failures: list[str] = []
        for region in regions:
            try:
                run = sync_retail_prices(
                    snapshot_date=snapshot_date,
                    currency=options["currency"],
                    region=region,
                    max_seconds=options["max_seconds"] or None,
                    connector=connector,
                )
            except SnapshotAlreadyExists as exc:
                skipped += 1
                self.stdout.write(self.style.WARNING(str(exc)))
                continue
            except Exception as exc:
                failures.append(f"{region}: {exc}")
                self.stderr.write(
                    self.style.ERROR(f"Retail price sync failed for {region}: {exc}")
                )
                continue

            captured += run.normalized_item_count
            self.stdout.write(
                self.style.SUCCESS(
                    f"Captured {run.normalized_item_count} normalized prices from "
                    f"{run.raw_item_count} raw items across {run.page_count} pages "
                    f"for {region} on {run.snapshot_date} ({run.currency})."
                )
            )

        synced = len(regions) - skipped - len(failures)
        self.stdout.write(
            f"Retail price sync finished: {synced} region(s) captured "
            f"({captured} normalized prices), {skipped} already present, "
            f"{len(failures)} failed."
        )

        if failures:
            raise CommandError(
                "Retail price sync failed for "
                f"{len(failures)} of {len(regions)} region(s): "
                + "; ".join(failures)
            )
