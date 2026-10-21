"""
Google Search Console Performance Report for Crush.lu

Pulls top queries, pages, country breakdown, and monthly trends from GSC API.

Usage:
    .venv/Scripts/python.exe scripts/gsc_report.py
    .venv/Scripts/python.exe scripts/gsc_report.py --csv
    .venv/Scripts/python.exe scripts/gsc_report.py --csv --start 2025-10-01
    .venv/Scripts/python.exe scripts/gsc_report.py --json
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SITE_URL = "sc-domain:crush.lu"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
SCRIPT_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = SCRIPT_DIR / "gsc_credentials.json"
TOKEN_FILE = SCRIPT_DIR / "gsc_token.json"
EXPORT_DIR = SCRIPT_DIR / "exports"


def get_credentials():
    """Load or create OAuth2 credentials for GSC."""
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


def query_gsc(service, start_date, end_date, dimensions, row_limit=25000):
    """Run a GSC search analytics query."""
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
    }
    response = service.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()
    return response.get("rows", [])


def write_csv(filename, headers, rows):
    """Write rows (list of dicts) to a CSV file."""
    EXPORT_DIR.mkdir(exist_ok=True)
    filepath = EXPORT_DIR / filename
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
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
        col_widths = []
        for i, h in enumerate(headers):
            max_val = max((len(str(r[i])) for r in rows), default=5)
            col_widths.append(max(len(h), max_val) + 2)

    print(f"\n=== {title} ===")
    header_line = "".join(
        h.ljust(w) if i == 0 else h.rjust(w)
        for i, (h, w) in enumerate(zip(headers, col_widths))
    )
    print(header_line)
    print("-" * sum(col_widths))
    for row in rows:
        line = "".join(
            str(v).ljust(w) if i == 0 else str(v).rjust(w)
            for i, (v, w) in enumerate(zip(row, col_widths))
        )
        print(line)


def format_row(raw_row, dim_count=1):
    """Convert a raw GSC row to a formatted dict."""
    keys = raw_row["keys"]
    return {
        "keys": keys,
        "clicks": int(raw_row["clicks"]),
        "impressions": int(raw_row["impressions"]),
        "ctr": f"{raw_row['ctr'] * 100:.1f}%",
        "ctr_raw": raw_row["ctr"],
        "position": f"{raw_row['position']:.1f}",
        "position_raw": raw_row["position"],
    }


def main():
    parser = argparse.ArgumentParser(description="GSC Performance Report for Crush.lu")
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

    # GSC data has a 2-3 day lag
    end_date = datetime.now() - timedelta(days=3)
    if args.days:
        start_date = end_date - timedelta(days=args.days)
    else:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    row_limit = 1000 if args.csv else 25

    creds = get_credentials()
    service = build("searchconsole", "v1", credentials=creds)

    data = {}

    # 1. Summary totals
    total_rows = query_gsc(service, start_str, end_str, [], row_limit=1)
    if total_rows:
        row = total_rows[0]
        data["summary"] = {
            "period": f"{start_str} to {end_str}",
            "total_clicks": int(row["clicks"]),
            "total_impressions": int(row["impressions"]),
            "avg_ctr": f"{row['ctr'] * 100:.1f}%",
            "avg_position": f"{row['position']:.1f}",
        }

    # 2. Monthly Trend (query by date, aggregate by month)
    date_rows = query_gsc(service, start_str, end_str, ["date"], row_limit=25000)
    monthly_agg = defaultdict(
        lambda: {
            "clicks": 0,
            "impressions": 0,
            "ctr_sum": 0.0,
            "pos_sum": 0.0,
            "count": 0,
        }
    )
    for row in date_rows:
        d = row["keys"][0]  # YYYY-MM-DD
        month = d[:7]  # YYYY-MM
        monthly_agg[month]["clicks"] += int(row["clicks"])
        monthly_agg[month]["impressions"] += int(row["impressions"])
        monthly_agg[month]["ctr_sum"] += row["ctr"]
        monthly_agg[month]["pos_sum"] += row["position"]
        monthly_agg[month]["count"] += 1

    monthly_trend = []
    for month in sorted(monthly_agg.keys()):
        m = monthly_agg[month]
        avg_ctr = m["ctr_sum"] / m["count"] if m["count"] else 0
        avg_pos = m["pos_sum"] / m["count"] if m["count"] else 0
        monthly_trend.append(
            {
                "month": month,
                "clicks": m["clicks"],
                "impressions": m["impressions"],
                "avg_ctr": f"{avg_ctr * 100:.1f}%",
                "avg_position": f"{avg_pos:.1f}",
            }
        )
    data["monthly_trend"] = monthly_trend

    # 3. Top Queries
    query_rows = query_gsc(service, start_str, end_str, ["query"], row_limit=row_limit)
    queries = []
    for row in query_rows:
        queries.append(
            {
                "query": row["keys"][0],
                "clicks": int(row["clicks"]),
                "impressions": int(row["impressions"]),
                "ctr": f"{row['ctr'] * 100:.1f}%",
                "position": f"{row['position']:.1f}",
            }
        )
    data["top_queries"] = queries

    # 4. Top Pages
    page_rows = query_gsc(service, start_str, end_str, ["page"], row_limit=row_limit)
    pages = []
    for row in page_rows:
        pages.append(
            {
                "page": row["keys"][0],
                "clicks": int(row["clicks"]),
                "impressions": int(row["impressions"]),
                "ctr": f"{row['ctr'] * 100:.1f}%",
                "position": f"{row['position']:.1f}",
            }
        )
    data["top_pages"] = pages

    # 5. Country Breakdown
    country_rows = query_gsc(
        service, start_str, end_str, ["country"], row_limit=row_limit
    )
    countries = []
    for row in country_rows:
        countries.append(
            {
                "country": row["keys"][0],
                "clicks": int(row["clicks"]),
                "impressions": int(row["impressions"]),
                "ctr": f"{row['ctr'] * 100:.1f}%",
                "position": f"{row['position']:.1f}",
            }
        )
    data["countries"] = countries

    # --- Output ---
    if args.json:
        print(json.dumps(data, indent=2))
        return

    if args.csv:
        print(f"\nExporting GSC data ({start_str} to {end_str}) to CSV...")

        if data.get("summary"):
            write_csv(
                "gsc_summary.csv", list(data["summary"].keys()), [data["summary"]]
            )

        if monthly_trend:
            write_csv(
                "gsc_monthly_trend.csv",
                ["month", "clicks", "impressions", "avg_ctr", "avg_position"],
                monthly_trend,
            )

        if queries:
            write_csv(
                "gsc_top_queries.csv",
                ["query", "clicks", "impressions", "ctr", "position"],
                queries,
            )

        if pages:
            write_csv(
                "gsc_top_pages.csv",
                ["page", "clicks", "impressions", "ctr", "position"],
                pages,
            )

        if countries:
            write_csv(
                "gsc_countries.csv",
                ["country", "clicks", "impressions", "ctr", "position"],
                countries,
            )

        print("\nDone! CSV files saved to scripts/exports/")
        return

    # Pretty print (terminal)
    print(f"\nGSC Report for crush.lu  |  {start_str} to {end_str}")

    if data.get("summary"):
        s = data["summary"]
        print("\n--- Summary ---")
        print(f"  Total Clicks:      {s['total_clicks']}")
        print(f"  Total Impressions: {s['total_impressions']}")
        print(f"  Avg CTR:           {s['avg_ctr']}")
        print(f"  Avg Position:      {s['avg_position']}")

    if monthly_trend:
        table_rows = [
            [m["month"], m["clicks"], m["impressions"], m["avg_ctr"], m["avg_position"]]
            for m in monthly_trend
        ]
        print_table(
            "MONTHLY TREND",
            ["Month", "Clicks", "Impressions", "Avg CTR", "Avg Position"],
            table_rows,
        )

    if queries:
        table_rows = [
            [q["query"], q["clicks"], q["impressions"], q["ctr"], q["position"]]
            for q in queries[:25]
        ]
        print_table(
            "TOP SEARCH QUERIES",
            ["Query", "Clicks", "Impressions", "CTR", "Position"],
            table_rows,
        )

    if pages:
        table_rows = [
            [p["page"], p["clicks"], p["impressions"], p["ctr"], p["position"]]
            for p in pages[:15]
        ]
        print_table(
            "TOP PAGES",
            ["Page", "Clicks", "Impressions", "CTR", "Position"],
            table_rows,
        )

    if countries:
        table_rows = [
            [c["country"], c["clicks"], c["impressions"], c["ctr"], c["position"]]
            for c in countries[:15]
        ]
        print_table(
            "COUNTRY BREAKDOWN",
            ["Country", "Clicks", "Impressions", "CTR", "Position"],
            table_rows,
        )

    print("\n=== DONE ===\n")


if __name__ == "__main__":
    main()
