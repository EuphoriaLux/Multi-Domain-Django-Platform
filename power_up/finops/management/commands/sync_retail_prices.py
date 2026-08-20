"""Capture an immutable daily snapshot of public cloud retail VM prices."""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from power_up.finops.retail_prices import (
    SnapshotAlreadyExists,
    sync_retail_prices,
)
from power_up.finops.retail_prices.connectors.azure import (
    DEFAULT_EUROPEAN_REGIONS,
)


class Command(BaseCommand):
    help = (
        "Archive and normalize Azure VM retail prices for selected European "
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
        parser.add_argument("--timeout", type=int, default=45)

    def handle(self, *args, **options):
        snapshot_date = None
        if options["snapshot_date"]:
            try:
                snapshot_date = date.fromisoformat(options["snapshot_date"])
            except ValueError as exc:
                raise CommandError("--snapshot-date must use YYYY-MM-DD.") from exc

        from power_up.finops.retail_prices.connectors.azure import (
            AzureRetailPricesConnector,
        )

        try:
            run = sync_retail_prices(
                snapshot_date=snapshot_date,
                currency=options["currency"],
                regions=options["regions"],
                connector=AzureRetailPricesConnector(timeout=options["timeout"]),
            )
        except SnapshotAlreadyExists as exc:
            self.stdout.write(self.style.WARNING(str(exc)))
            return
        except Exception as exc:
            raise CommandError(f"Retail price sync failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Captured {run.normalized_item_count} normalized prices from "
                f"{run.raw_item_count} raw items across {run.page_count} pages "
                f"for {run.snapshot_date} ({run.currency})."
            )
        )
