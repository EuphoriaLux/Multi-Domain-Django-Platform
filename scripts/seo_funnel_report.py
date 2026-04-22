"""
Combined GSC + GA4 Funnel Report for Crush.lu

Joins Google Search Console page data with GA4 landing-page data on URL path
to expose the end-to-end funnel: impressions -> clicks -> sessions -> engagement.

Also surfaces:
  - CTR underperformers (high impressions, low CTR — title/snippet rewrite candidates)
  - Bounce leakers (search brings clicks but landing page doesn't hold them)
  - Orphans (in GA4 but not GSC, or vice versa)

Prereqs: run gsc_report.py and ga4_report.py at least once so both
         gsc_token.json and ga4_token.json exist.

Usage:
    .venv/Scripts/python.exe scripts/seo_funnel_report.py
    .venv/Scripts/python.exe scripts/seo_funnel_report.py --days 30
    .venv/Scripts/python.exe scripts/seo_funnel_report.py --csv
    .venv/Scripts/python.exe scripts/seo_funnel_report.py --json
    .venv/Scripts/python.exe scripts/seo_funnel_report.py --collapse-language
    .venv/Scripts/python.exe scripts/seo_funnel_report.py --queries
"""

import argparse
import json
import re
import statistics
from datetime import datetime, timedelta
from urllib.parse import urlparse

from _analytics_common import load_oauth_credentials, print_table, write_csv

GSC_SITE_URL = "sc-domain:crush.lu"
GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
GA4_PROPERTY_ID = "516337382"
GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

LANG_PREFIX_RE = re.compile(r"^/(en|de|fr)(/|$)")


def normalize_path(url_or_path, collapse_language=False):
    """Return a path-only join key. Accepts full URLs or paths."""
    if url_or_path.startswith(("http://", "https://")):
        path = urlparse(url_or_path).path or "/"
    else:
        path = url_or_path.split("?", 1)[0] or "/"

    if not path.startswith("/"):
        path = "/" + path

    if collapse_language:
        path = LANG_PREFIX_RE.sub("/", path)
        if not path:
            path = "/"

    return path


def fetch_gsc_pages(start_date, end_date):
    """Pull GSC page-level metrics for the period."""
    from googleapiclient.discovery import build

    creds = load_oauth_credentials("gsc_token.json", GSC_SCOPES)
    service = build("searchconsole", "v1", credentials=creds)

    response = service.searchanalytics().query(
        siteUrl=GSC_SITE_URL,
        body={
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["page"],
            "rowLimit": 25000,
        },
    ).execute()

    return response.get("rows", [])


def fetch_gsc_query_page(start_date, end_date):
    """Pull GSC query+page pairs (optional, larger response)."""
    from googleapiclient.discovery import build

    creds = load_oauth_credentials("gsc_token.json", GSC_SCOPES)
    service = build("searchconsole", "v1", credentials=creds)

    response = service.searchanalytics().query(
        siteUrl=GSC_SITE_URL,
        body={
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["query", "page"],
            "rowLimit": 25000,
        },
    ).execute()

    return response.get("rows", [])


def fetch_ga4_landing_pages(start_date, end_date):
    """Pull GA4 landing-page metrics for the period."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )

    creds = load_oauth_credentials("ga4_token.json", GA4_SCOPES)
    client = BetaAnalyticsDataClient(credentials=creds)

    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        dimensions=[Dimension(name="landingPagePlusQueryString")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="bounceRate"),
            Metric(name="engagementRate"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=100000,
    )
    response = client.run_report(request)

    rows = []
    for row in response.rows:
        rows.append({
            "landingPage": row.dimension_values[0].value,
            "sessions": int(row.metric_values[0].value or 0),
            "users": int(row.metric_values[1].value or 0),
            "bounceRate": float(row.metric_values[2].value or 0),
            "engagementRate": float(row.metric_values[3].value or 0),
        })
    return rows


def aggregate_by_path(rows, path_extractor, metric_accumulator, collapse_language):
    """Generic aggregator: path_extractor(row) -> raw path, metric_accumulator(acc, row) -> None."""
    buckets = {}
    for row in rows:
        raw = path_extractor(row)
        if not raw:
            continue
        path = normalize_path(raw, collapse_language)
        if path not in buckets:
            buckets[path] = {}
        metric_accumulator(buckets[path], row)
    return buckets


def build_funnel(gsc_rows, ga4_rows, collapse_language):
    """Join GSC page metrics with GA4 landing-page metrics on normalized path."""
    def gsc_accum(acc, row):
        acc["impressions"] = acc.get("impressions", 0) + int(row["impressions"])
        acc["clicks"] = acc.get("clicks", 0) + int(row["clicks"])
        # GSC returns pre-aggregated CTR/position per row; for collapsing we
        # re-derive CTR from clicks/impressions and take a click-weighted
        # position so collapsed rows stay meaningful.
        acc["_position_weighted"] = (
            acc.get("_position_weighted", 0.0) + row["position"] * int(row["clicks"])
        )
        acc["_position_rows"] = acc.get("_position_rows", 0) + 1
        acc["_position_sum"] = acc.get("_position_sum", 0.0) + row["position"]

    gsc_by_path = aggregate_by_path(
        gsc_rows, lambda r: r["keys"][0], gsc_accum, collapse_language
    )
    for path, m in gsc_by_path.items():
        m["ctr"] = (m["clicks"] / m["impressions"]) if m["impressions"] else 0.0
        if m["clicks"] > 0:
            m["position"] = m["_position_weighted"] / m["clicks"]
        else:
            m["position"] = m["_position_sum"] / m["_position_rows"]
        for k in ("_position_weighted", "_position_rows", "_position_sum"):
            m.pop(k, None)

    def ga4_accum(acc, row):
        acc["sessions"] = acc.get("sessions", 0) + row["sessions"]
        acc["users"] = acc.get("users", 0) + row["users"]
        # Rate metrics need to be session-weighted when we collapse.
        acc["_bounce_weighted"] = (
            acc.get("_bounce_weighted", 0.0) + row["bounceRate"] * row["sessions"]
        )
        acc["_engage_weighted"] = (
            acc.get("_engage_weighted", 0.0) + row["engagementRate"] * row["sessions"]
        )

    ga4_by_path = aggregate_by_path(
        ga4_rows, lambda r: r["landingPage"], ga4_accum, collapse_language
    )
    for path, m in ga4_by_path.items():
        s = m["sessions"] or 1
        m["bounceRate"] = m["_bounce_weighted"] / s
        m["engagementRate"] = m["_engage_weighted"] / s
        for k in ("_bounce_weighted", "_engage_weighted"):
            m.pop(k, None)

    all_paths = set(gsc_by_path) | set(ga4_by_path)
    funnel = []
    for path in all_paths:
        g = gsc_by_path.get(path, {})
        a = ga4_by_path.get(path, {})
        impressions = g.get("impressions", 0)
        clicks = g.get("clicks", 0)
        sessions = a.get("sessions", 0)
        funnel.append({
            "path": path,
            "impressions": impressions,
            "clicks": clicks,
            "ctr": g.get("ctr", 0.0),
            "position": g.get("position", 0.0),
            "sessions": sessions,
            "users": a.get("users", 0),
            "bounceRate": a.get("bounceRate", 0.0),
            "engagementRate": a.get("engagementRate", 0.0),
            "click_to_session": (clicks / sessions) if sessions else None,
            "_in_gsc": path in gsc_by_path,
            "_in_ga4": path in ga4_by_path,
        })

    funnel.sort(key=lambda r: r["impressions"], reverse=True)
    return funnel


def derive_views(funnel):
    """Compute underperformers, bounce leakers, and orphans."""
    with_impressions = [r for r in funnel if r["impressions"] >= 50]
    median_ctr = (
        statistics.median([r["ctr"] for r in with_impressions])
        if with_impressions else 0.0
    )

    underperformers = []
    for r in with_impressions:
        gap = median_ctr - r["ctr"]
        if r["ctr"] < 0.02 and gap > 0:
            score = r["impressions"] * gap
            underperformers.append({**r, "ctr_gap": gap, "leverage": score})
    underperformers.sort(key=lambda r: r["leverage"], reverse=True)

    bounce_leakers = [
        r for r in funnel
        if r["clicks"] >= 5 and r["bounceRate"] >= 0.70 and r["_in_ga4"]
    ]
    bounce_leakers.sort(key=lambda r: (r["clicks"], r["bounceRate"]), reverse=True)

    orphans_no_search = [
        r for r in funnel if r["_in_ga4"] and not r["_in_gsc"] and r["sessions"] >= 5
    ]
    orphans_no_search.sort(key=lambda r: r["sessions"], reverse=True)

    orphans_no_analytics = [
        r for r in funnel if r["_in_gsc"] and not r["_in_ga4"] and r["impressions"] >= 100
    ]
    orphans_no_analytics.sort(key=lambda r: r["impressions"], reverse=True)

    return {
        "median_ctr": median_ctr,
        "underperformers": underperformers,
        "bounce_leakers": bounce_leakers,
        "orphans_no_search": orphans_no_search,
        "orphans_no_analytics": orphans_no_analytics,
    }


def top_queries_per_page(query_page_rows, collapse_language, top_n=3):
    """For each normalized path, return the top-N queries by clicks."""
    by_path = {}
    for row in query_page_rows:
        query, page = row["keys"][0], row["keys"][1]
        path = normalize_path(page, collapse_language)
        by_path.setdefault(path, []).append({
            "query": query,
            "clicks": int(row["clicks"]),
            "impressions": int(row["impressions"]),
            "ctr": row["ctr"],
            "position": row["position"],
        })
    for path, queries in by_path.items():
        queries.sort(key=lambda q: q["clicks"], reverse=True)
        by_path[path] = queries[:top_n]
    return by_path


def fmt_pct(v):
    return f"{v * 100:.1f}%" if v else "0.0%"


def fmt_row_for_display(row):
    """Format a funnel row for terminal / CSV display."""
    return {
        "path": row["path"],
        "impressions": row["impressions"],
        "clicks": row["clicks"],
        "ctr": fmt_pct(row["ctr"]),
        "position": f"{row['position']:.1f}" if row["position"] else "-",
        "sessions": row["sessions"],
        "users": row["users"],
        "bounceRate": fmt_pct(row["bounceRate"]),
        "engagementRate": fmt_pct(row["engagementRate"]),
        "clk/sess": (
            f"{row['click_to_session']:.2f}" if row["click_to_session"] is not None else "-"
        ),
        "in_gsc": "y" if row["_in_gsc"] else "-",
        "in_ga4": "y" if row["_in_ga4"] else "-",
    }


def main():
    parser = argparse.ArgumentParser(description="GSC + GA4 funnel report for crush.lu")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=90, help="Days to look back (default: 90)")
    parser.add_argument("--csv", action="store_true", help="Write CSV files to scripts/exports/")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of tables")
    parser.add_argument("--collapse-language", action="store_true",
                        help="Collapse /en, /de, /fr prefixes into a single path")
    parser.add_argument("--queries", action="store_true",
                        help="Include top-queries-per-page section (larger API call)")
    parser.add_argument("--top", type=int, default=30,
                        help="Rows to show per table in terminal mode (default: 30)")
    args = parser.parse_args()

    # GSC data lags 2-3 days; pin end date to match for apples-to-apples with GA4.
    end_date = datetime.now() - timedelta(days=3)
    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        start_date = end_date - timedelta(days=args.days)

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    print(f"Fetching GSC pages for {start_str} to {end_str}...")
    gsc_rows = fetch_gsc_pages(start_str, end_str)
    print(f"  {len(gsc_rows)} GSC page rows")

    print(f"Fetching GA4 landing pages for {start_str} to {end_str}...")
    ga4_rows = fetch_ga4_landing_pages(start_str, end_str)
    print(f"  {len(ga4_rows)} GA4 landing-page rows")

    funnel = build_funnel(gsc_rows, ga4_rows, args.collapse_language)
    views = derive_views(funnel)

    query_pairs = {}
    if args.queries:
        print(f"Fetching GSC query x page pairs...")
        qp_rows = fetch_gsc_query_page(start_str, end_str)
        print(f"  {len(qp_rows)} query/page rows")
        query_pairs = top_queries_per_page(qp_rows, args.collapse_language)

    totals = {
        "period": f"{start_str} to {end_str}",
        "paths_total": len(funnel),
        "paths_in_both": sum(1 for r in funnel if r["_in_gsc"] and r["_in_ga4"]),
        "paths_gsc_only": sum(1 for r in funnel if r["_in_gsc"] and not r["_in_ga4"]),
        "paths_ga4_only": sum(1 for r in funnel if r["_in_ga4"] and not r["_in_gsc"]),
        "total_impressions": sum(r["impressions"] for r in funnel),
        "total_clicks": sum(r["clicks"] for r in funnel),
        "total_sessions": sum(r["sessions"] for r in funnel),
        "median_ctr_for_scoring": fmt_pct(views["median_ctr"]),
        "collapse_language": args.collapse_language,
    }

    if args.json:
        out = {
            "summary": totals,
            "funnel": [fmt_row_for_display(r) for r in funnel],
            "underperformers": [fmt_row_for_display(r) for r in views["underperformers"]],
            "bounce_leakers": [fmt_row_for_display(r) for r in views["bounce_leakers"]],
            "orphans_no_search": [fmt_row_for_display(r) for r in views["orphans_no_search"]],
            "orphans_no_analytics": [fmt_row_for_display(r) for r in views["orphans_no_analytics"]],
        }
        if query_pairs:
            out["top_queries_per_page"] = query_pairs
        print(json.dumps(out, indent=2))
        return

    if args.csv:
        print("\nWriting CSV files...")
        funnel_rows = [fmt_row_for_display(r) for r in funnel]
        headers = ["path", "impressions", "clicks", "ctr", "position", "sessions",
                   "users", "bounceRate", "engagementRate", "clk/sess", "in_gsc", "in_ga4"]
        write_csv("seo_funnel.csv", headers, funnel_rows)
        write_csv("seo_funnel_underperformers.csv", headers,
                  [fmt_row_for_display(r) for r in views["underperformers"]])
        write_csv("seo_funnel_bounce_leakers.csv", headers,
                  [fmt_row_for_display(r) for r in views["bounce_leakers"]])
        write_csv("seo_funnel_orphans_no_search.csv", headers,
                  [fmt_row_for_display(r) for r in views["orphans_no_search"]])
        write_csv("seo_funnel_orphans_no_analytics.csv", headers,
                  [fmt_row_for_display(r) for r in views["orphans_no_analytics"]])
        if query_pairs:
            qp_flat = []
            for path, queries in query_pairs.items():
                for q in queries:
                    qp_flat.append({
                        "path": path,
                        "query": q["query"],
                        "clicks": q["clicks"],
                        "impressions": q["impressions"],
                        "ctr": fmt_pct(q["ctr"]),
                        "position": f"{q['position']:.1f}",
                    })
            write_csv("seo_funnel_top_queries.csv",
                      ["path", "query", "clicks", "impressions", "ctr", "position"],
                      qp_flat)
        print("Done.")
        return

    # Pretty print (terminal)
    print(f"\n=== SUMMARY ({totals['period']}) ===")
    for k, v in totals.items():
        print(f"  {k}: {v}")

    headers = ["path", "impressions", "clicks", "ctr", "position", "sessions",
               "bounceRate", "engagementRate", "in_gsc", "in_ga4"]

    print_table(
        f"FUNNEL (top {args.top} by impressions)",
        headers,
        [fmt_row_for_display(r) for r in funnel[:args.top]],
    )

    print_table(
        f"CTR UNDERPERFORMERS (impressions >= 50, ctr < 2%, median={totals['median_ctr_for_scoring']})",
        headers,
        [fmt_row_for_display(r) for r in views["underperformers"][:args.top]],
    )

    print_table(
        "BOUNCE LEAKERS (clicks >= 5, bounceRate >= 70%)",
        headers,
        [fmt_row_for_display(r) for r in views["bounce_leakers"][:args.top]],
    )

    print_table(
        "ORPHANS - in GA4 but not GSC (sessions >= 5)",
        headers,
        [fmt_row_for_display(r) for r in views["orphans_no_search"][:args.top]],
    )

    print_table(
        "ORPHANS - in GSC but not GA4 (impressions >= 100)",
        headers,
        [fmt_row_for_display(r) for r in views["orphans_no_analytics"][:args.top]],
    )

    if query_pairs:
        print("\n=== TOP QUERIES PER PAGE (top 10 pages by impressions) ===")
        top_paths = [r["path"] for r in funnel[:10] if r["_in_gsc"]]
        for path in top_paths:
            queries = query_pairs.get(path, [])
            if not queries:
                continue
            print(f"\n  {path}")
            for q in queries:
                print(f"    {q['clicks']:>4} clicks  {q['impressions']:>6} imp  "
                      f"{fmt_pct(q['ctr']):>6}  pos {q['position']:.1f}  -  {q['query']}")

    print("\n=== DONE ===\n")


if __name__ == "__main__":
    main()
