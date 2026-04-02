"""
GA4 Traffic Report for Crush.lu

Pulls traffic acquisition, landing pages, geography, device, and monthly
trend data from Google Analytics 4 Data API.

Usage:
    .venv/Scripts/python.exe scripts/ga4_report.py
    .venv/Scripts/python.exe scripts/ga4_report.py --csv
    .venv/Scripts/python.exe scripts/ga4_report.py --csv --start 2025-10-01
    .venv/Scripts/python.exe scripts/ga4_report.py --json
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    OrderBy,
    RunReportRequest,
)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

PROPERTY_ID = "516337382"
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
SCRIPT_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = SCRIPT_DIR / "gsc_credentials.json"
TOKEN_FILE = SCRIPT_DIR / "ga4_token.json"
EXPORT_DIR = SCRIPT_DIR / "exports"


def get_credentials():
    """Load or create OAuth2 credentials for GA4."""
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request

            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"ERROR: {CREDENTIALS_FILE} not found.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def run_report(
    client, dimensions, metrics, start_date, end_date, limit=20, order_by=None
):
    """Run a GA4 report and return rows as list of dicts."""
    request = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=limit,
    )
    if order_by:
        request.order_bys = order_by

    response = client.run_report(request)

    rows = []
    for row in response.rows:
        entry = {}
        for i, dim in enumerate(dimensions):
            entry[dim] = row.dimension_values[i].value
        for i, met in enumerate(metrics):
            entry[met] = row.metric_values[i].value
        rows.append(entry)
    return rows


def write_csv(filename, headers, rows):
    """Write rows (list of dicts) to a CSV file, only including specified headers."""
    EXPORT_DIR.mkdir(exist_ok=True)
    filepath = EXPORT_DIR / filename
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  -> {filepath}")


def print_table(title, headers, rows, col_widths=None):
    """Print a formatted table."""
    if not rows:
        print(f"\n=== {title} ===")
        print("  (no data)")
        return

    if not col_widths:
        col_widths = [
            max(len(h), max((len(str(r.get(h, ""))) for r in rows), default=5)) + 2
            for h in headers
        ]

    print(f"\n=== {title} ===")
    header_line = "".join(
        h.ljust(w) if i == 0 else h.rjust(w)
        for i, (h, w) in enumerate(zip(headers, col_widths))
    )
    print(header_line)
    print("-" * sum(col_widths))
    for row in rows:
        vals = [str(row.get(h, "")) for h in headers]
        line = "".join(
            v.ljust(w) if i == 0 else v.rjust(w)
            for i, (v, w) in enumerate(zip(vals, col_widths))
        )
        print(line)


def format_bounce_rate(rows):
    """Convert bounceRate from decimal to percentage string."""
    for row in rows:
        try:
            row["bounceRate"] = f"{float(row['bounceRate']) * 100:.1f}%"
        except (ValueError, KeyError):
            pass


def main():
    parser = argparse.ArgumentParser(description="GA4 Traffic Report for Crush.lu")
    parser.add_argument(
        "--start",
        default="2026-01-01",
        help="Start date YYYY-MM-DD (default: 2026-01-01)",
    )
    parser.add_argument(
        "--days", type=int, help="Days to look back (overrides --start)"
    )
    parser.add_argument(
        "--csv", action="store_true", help="Export to CSV files in scripts/exports/"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    end_str = datetime.now().strftime("%Y-%m-%d")
    if args.days:
        from datetime import timedelta

        start_str = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    else:
        start_str = args.start

    row_limit = 100 if args.csv else 20

    creds = get_credentials()
    client = BetaAnalyticsDataClient(credentials=creds)

    data = {}

    # 1. Summary totals
    totals = run_report(
        client,
        dimensions=[],
        metrics=[
            "totalUsers",
            "sessions",
            "newUsers",
            "averageSessionDuration",
            "screenPageViews",
            "engagedSessions",
            "engagementRate",
        ],
        start_date=start_str,
        end_date=end_str,
    )
    if totals:
        summary = totals[0]
        try:
            secs = float(summary.get("averageSessionDuration", 0))
            summary["avgSessionDuration_formatted"] = (
                f"{int(secs // 60)}m {int(secs % 60)}s"
            )
        except ValueError:
            pass
        try:
            summary["engagementRate"] = f"{float(summary['engagementRate']) * 100:.1f}%"
        except (ValueError, KeyError):
            pass
        data["summary"] = summary

    # 2. Monthly Trend
    monthly = run_report(
        client,
        dimensions=["yearMonth"],
        metrics=[
            "totalUsers",
            "sessions",
            "newUsers",
            "screenPageViews",
            "averageSessionDuration",
            "engagementRate",
        ],
        start_date=start_str,
        end_date=end_str,
        limit=24,
        order_by=[
            OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="yearMonth"))
        ],
    )
    for row in monthly:
        ym = row["yearMonth"]
        row["month"] = f"{ym[:4]}-{ym[4:]}"
        try:
            secs = float(row.get("averageSessionDuration", 0))
            row["avgSessionDuration_formatted"] = (
                f"{int(secs // 60)}m {int(secs % 60)}s"
            )
        except ValueError:
            pass
        try:
            row["engagementRate"] = f"{float(row['engagementRate']) * 100:.1f}%"
        except (ValueError, KeyError):
            pass
    data["monthly_trend"] = monthly

    # 3. Traffic Acquisition (source/medium)
    acquisition = run_report(
        client,
        dimensions=["sessionSource", "sessionMedium"],
        metrics=["sessions", "totalUsers", "newUsers", "bounceRate", "engagementRate"],
        start_date=start_str,
        end_date=end_str,
        limit=row_limit,
    )
    format_bounce_rate(acquisition)
    for row in acquisition:
        try:
            row["engagementRate"] = f"{float(row['engagementRate']) * 100:.1f}%"
        except (ValueError, KeyError):
            pass
    data["traffic_acquisition"] = acquisition

    # 4. Top Landing Pages
    landing_pages = run_report(
        client,
        dimensions=["landingPagePlusQueryString"],
        metrics=["sessions", "totalUsers", "bounceRate", "engagementRate"],
        start_date=start_str,
        end_date=end_str,
        limit=row_limit,
    )
    format_bounce_rate(landing_pages)
    for row in landing_pages:
        try:
            row["engagementRate"] = f"{float(row['engagementRate']) * 100:.1f}%"
        except (ValueError, KeyError):
            pass
    data["landing_pages"] = landing_pages

    # 5. Geographic Breakdown (country)
    countries = run_report(
        client,
        dimensions=["country"],
        metrics=["totalUsers", "sessions", "newUsers"],
        start_date=start_str,
        end_date=end_str,
        limit=row_limit,
    )
    data["countries"] = countries

    # 6. Device Category
    devices = run_report(
        client,
        dimensions=["deviceCategory"],
        metrics=["totalUsers", "sessions"],
        start_date=start_str,
        end_date=end_str,
        limit=10,
    )
    data["devices"] = devices

    # --- Output ---
    if args.json:
        print(json.dumps(data, indent=2))
        return

    if args.csv:
        print(f"\nExporting GA4 data ({start_str} to {end_str}) to CSV...")

        if data.get("summary"):
            write_csv(
                "ga4_summary.csv", list(data["summary"].keys()), [data["summary"]]
            )

        if monthly:
            write_csv(
                "ga4_monthly_trend.csv",
                [
                    "month",
                    "totalUsers",
                    "sessions",
                    "newUsers",
                    "screenPageViews",
                    "avgSessionDuration_formatted",
                    "engagementRate",
                ],
                monthly,
            )

        if acquisition:
            write_csv(
                "ga4_traffic_sources.csv",
                [
                    "sessionSource",
                    "sessionMedium",
                    "sessions",
                    "totalUsers",
                    "newUsers",
                    "bounceRate",
                    "engagementRate",
                ],
                acquisition,
            )

        if landing_pages:
            write_csv(
                "ga4_landing_pages.csv",
                [
                    "landingPagePlusQueryString",
                    "sessions",
                    "totalUsers",
                    "bounceRate",
                    "engagementRate",
                ],
                landing_pages,
            )

        if countries:
            write_csv(
                "ga4_countries.csv",
                ["country", "totalUsers", "sessions", "newUsers"],
                countries,
            )

        if devices:
            write_csv(
                "ga4_devices.csv", ["deviceCategory", "totalUsers", "sessions"], devices
            )

        print("\nDone! CSV files saved to scripts/exports/")
        return

    # Pretty print (terminal)
    print(
        f"\nGA4 Report for crush.lu  |  {start_str} to {end_str}  |  Property {PROPERTY_ID}"
    )

    if data.get("summary"):
        s = data["summary"]
        print("\n--- Summary ---")
        print(f"  Total Users:      {s.get('totalUsers', 'N/A')}")
        print(f"  Sessions:         {s.get('sessions', 'N/A')}")
        print(f"  New Users:        {s.get('newUsers', 'N/A')}")
        print(f"  Page Views:       {s.get('screenPageViews', 'N/A')}")
        print(f"  Avg Session:      {s.get('avgSessionDuration_formatted', 'N/A')}")
        print(f"  Engagement Rate:  {s.get('engagementRate', 'N/A')}")

    if monthly:
        print_table(
            "MONTHLY TREND",
            [
                "month",
                "totalUsers",
                "sessions",
                "newUsers",
                "screenPageViews",
                "engagementRate",
            ],
            monthly,
        )

    if acquisition:
        print_table(
            "TRAFFIC ACQUISITION (source / medium)",
            [
                "sessionSource",
                "sessionMedium",
                "sessions",
                "totalUsers",
                "newUsers",
                "bounceRate",
            ],
            acquisition,
        )

    if landing_pages:
        print_table(
            "TOP LANDING PAGES",
            ["landingPagePlusQueryString", "sessions", "totalUsers", "bounceRate"],
            landing_pages,
        )

    if countries:
        print_table(
            "GEOGRAPHIC BREAKDOWN",
            ["country", "totalUsers", "sessions", "newUsers"],
            countries,
        )

    if devices:
        print_table(
            "DEVICE CATEGORY",
            ["deviceCategory", "totalUsers", "sessions"],
            devices,
        )

    print("\n=== DONE ===\n")


if __name__ == "__main__":
    main()
