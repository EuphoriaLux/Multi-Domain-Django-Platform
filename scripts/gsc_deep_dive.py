"""
Google Search Console - Deep Dive Analysis for crush.lu
========================================================
Comprehensive SEO analysis pulling multi-dimensional data from GSC API.

Analyses:
1. Query + Page matrix (cannibalization detection)
2. Weekly trends (position/clicks/impressions over time)
3. CTR opportunity analysis (high impressions, low CTR)
4. Query clustering by language and intent
5. Event page performance breakdown
6. Striking distance keywords (positions 4-20)
7. Actionable recommendations

Prerequisites: Same as gsc_report.py (OAuth credentials in scripts/gsc_credentials.json)

Usage:
    .venv/Scripts/python.exe scripts/gsc_deep_dive.py
    .venv/Scripts/python.exe scripts/gsc_deep_dive.py --days 90
    .venv/Scripts/python.exe scripts/gsc_deep_dive.py --json
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCRIPT_DIR = Path(__file__).parent
CREDENTIALS_FILE = SCRIPT_DIR / "gsc_credentials.json"
TOKEN_FILE = SCRIPT_DIR / "gsc_token.json"
OUTPUT_FILE = SCRIPT_DIR / "gsc_deep_dive.json"

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
SITE_URL = "sc-domain:crush.lu"

# Expected CTR by position (industry benchmarks for branded + non-branded mix)
CTR_BENCHMARKS = {
    1: 0.30, 2: 0.15, 3: 0.10, 4: 0.07, 5: 0.05,
    6: 0.04, 7: 0.03, 8: 0.025, 9: 0.02, 10: 0.015,
}

# High-value target keywords to track closely
TARGET_KEYWORDS = [
    "speed dating luxembourg",
    "speeddating luxembourg",
    "dating luxembourg",
    "dating luxemburg",
    "singles in luxemburg",
    "singles in luxembourg",
    "single party luxembourg",
    "rencontre luxembourg",
    "rencontres luxembourg",
    "site rencontre luxembourg",
    "soiree celibataire luxembourg",
    "singletreff luxemburg",
]

# Language detection patterns
LANG_PATTERNS = {
    "fr": re.compile(
        r"\b(rencontre|célibataire|soirée|site de|gratuit|amour)\b", re.IGNORECASE
    ),
    "de": re.compile(
        r"\b(luxemburg|singletreff|dating in der|treffen|kennenlernen)\b",
        re.IGNORECASE,
    ),
    "en": re.compile(
        r"\b(dating|singles|speed dating|meetup|single party)\b", re.IGNORECASE
    ),
}

# Intent classification
INTENT_PATTERNS = {
    "brand": re.compile(r"\bcrush[\s.]*(lu|luxembourg|dating|login|app)?\b", re.IGNORECASE),
    "event": re.compile(
        r"\b(speed dating|speeddating|single party|soirée|singletreff|meetup|event)\b",
        re.IGNORECASE,
    ),
    "dating_general": re.compile(
        r"\b(dating|rencontre|singles?|célibataire)\b", re.IGNORECASE
    ),
    "location": re.compile(
        r"\b(luxemb|near me|in der nähe)\b", re.IGNORECASE
    ),
}


def authenticate():
    """Authenticate via OAuth2 (reuses token from gsc_report.py)."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"ERROR: {CREDENTIALS_FILE} not found. Run gsc_report.py first.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def query_gsc(service, dimensions, days=28, row_limit=1000, filters=None):
    """Generic GSC query helper."""
    end_date = datetime.now().date() - timedelta(days=3)  # GSC data has ~3 day lag
    start_date = end_date - timedelta(days=days)

    body = {
        "startDate": str(start_date),
        "endDate": str(end_date),
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "dataState": "final",
    }
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    response = (
        service.searchanalytics()
        .query(siteUrl=SITE_URL, body=body)
        .execute()
    )
    return response.get("rows", []), str(start_date), str(end_date)


def detect_language(query):
    """Detect query language."""
    for lang, pattern in LANG_PATTERNS.items():
        if pattern.search(query):
            return lang
    return "unknown"


def detect_intent(query):
    """Classify query intent."""
    intents = []
    for intent, pattern in INTENT_PATTERNS.items():
        if pattern.search(query):
            intents.append(intent)
    return intents or ["other"]


def extract_page_type(url):
    """Classify a URL into a page type."""
    if "/events/" in url:
        # Check if it's a specific event or the list
        parts = url.rstrip("/").split("/")
        if parts[-1].isdigit():
            return "event_detail"
        return "event_list"
    if "/about/" in url:
        return "about"
    if "/how-it-works/" in url:
        return "how_it_works"
    if "/membership/" in url:
        return "membership"
    if "/login/" in url:
        return "login"
    if "/signup/" in url:
        return "signup"
    if "/privacy-policy/" in url:
        return "legal"
    if "/terms-of-service/" in url:
        return "legal"
    if "/data-deletion/" in url:
        return "legal"
    # Homepage variants
    path = url.replace("https://crush.lu", "").replace("http://crush.lu", "")
    if path in ("/", "/en/", "/de/", "/fr/"):
        return "homepage"
    return "other"


def extract_lang_from_url(url):
    """Extract language from URL path."""
    path = url.replace("https://crush.lu", "").replace("http://crush.lu", "")
    if path.startswith("/en/"):
        return "en"
    if path.startswith("/de/"):
        return "de"
    if path.startswith("/fr/"):
        return "fr"
    return "none"


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def analyze_query_page_matrix(service, days):
    """Find which queries map to which pages (cannibalization detection)."""
    print("\n1. QUERY + PAGE CANNIBALIZATION ANALYSIS")
    print("=" * 60)

    rows, start, end = query_gsc(service, ["query", "page"], days, row_limit=2000)

    # Group by query -> list of pages
    query_pages = defaultdict(list)
    for row in rows:
        query = row["keys"][0]
        page = row["keys"][1]
        query_pages[query].append({
            "page": page,
            "clicks": row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "ctr": row.get("ctr", 0),
            "position": row.get("position", 0),
        })

    # Find cannibalized queries (same query, multiple pages)
    cannibalized = {}
    for query, pages in query_pages.items():
        if len(pages) > 1 and sum(p["impressions"] for p in pages) >= 5:
            pages.sort(key=lambda x: x["clicks"], reverse=True)
            cannibalized[query] = pages

    if cannibalized:
        # Sort by total impressions
        sorted_cannibal = sorted(
            cannibalized.items(),
            key=lambda x: sum(p["impressions"] for p in x[1]),
            reverse=True,
        )
        print(f"\nFound {len(sorted_cannibal)} cannibalized queries (multiple pages ranking):\n")
        for query, pages in sorted_cannibal[:15]:
            total_imp = sum(p["impressions"] for p in pages)
            total_clicks = sum(p["clicks"] for p in pages)
            print(f'  "{query}" ({total_imp} imp, {total_clicks} clicks, {len(pages)} pages)')
            for p in pages[:4]:
                short_page = p["page"].replace("https://crush.lu", "")
                print(
                    f"    {short_page:40s} pos={p['position']:5.1f}  "
                    f"imp={p['impressions']:4d}  clicks={p['clicks']:3d}  "
                    f"ctr={p['ctr']:.1%}"
                )
    else:
        print("\n  No significant cannibalization detected.")

    return {"cannibalized_queries": cannibalized, "total_query_page_combos": len(rows)}


def analyze_weekly_trends(service, days):
    """Track weekly performance trends."""
    print("\n\n2. WEEKLY PERFORMANCE TRENDS")
    print("=" * 60)

    rows, start, end = query_gsc(service, ["date"], days, row_limit=500)

    if not rows:
        print("  No trend data available.")
        return {}

    # Group by week
    weeks = defaultdict(lambda: {"clicks": 0, "impressions": 0, "ctr_sum": 0, "pos_sum": 0, "count": 0})
    for row in rows:
        date_str = row["keys"][0]
        date = datetime.strptime(date_str, "%Y-%m-%d")
        week_start = date - timedelta(days=date.weekday())
        week_key = str(week_start.date())

        weeks[week_key]["clicks"] += row.get("clicks", 0)
        weeks[week_key]["impressions"] += row.get("impressions", 0)
        weeks[week_key]["ctr_sum"] += row.get("ctr", 0)
        weeks[week_key]["pos_sum"] += row.get("position", 0)
        weeks[week_key]["count"] += 1

    print(f"\n  {'Week':>12s}  {'Clicks':>7s}  {'Impress':>8s}  {'CTR':>7s}  {'Avg Pos':>8s}")
    print(f"  {'-'*12}  {'-'*7}  {'-'*8}  {'-'*7}  {'-'*8}")

    sorted_weeks = sorted(weeks.items())
    trend_data = []
    for week, data in sorted_weeks:
        avg_ctr = data["ctr_sum"] / data["count"] if data["count"] else 0
        avg_pos = data["pos_sum"] / data["count"] if data["count"] else 0
        print(
            f"  {week:>12s}  {data['clicks']:>7d}  {data['impressions']:>8d}  "
            f"{avg_ctr:>6.1%}  {avg_pos:>8.1f}"
        )
        trend_data.append({
            "week": week,
            "clicks": data["clicks"],
            "impressions": data["impressions"],
            "avg_ctr": round(avg_ctr, 4),
            "avg_position": round(avg_pos, 1),
        })

    # Week-over-week change
    if len(sorted_weeks) >= 2:
        prev = sorted_weeks[-2][1]
        curr = sorted_weeks[-1][1]
        click_change = curr["clicks"] - prev["clicks"]
        imp_change = curr["impressions"] - prev["impressions"]
        direction = "+" if click_change >= 0 else ""
        print(f"\n  Week-over-week: {direction}{click_change} clicks, {direction}{imp_change} impressions")

    return {"weekly_trends": trend_data}


def analyze_ctr_opportunities(service, days):
    """Find high-impression queries with below-benchmark CTR."""
    print("\n\n3. CTR IMPROVEMENT OPPORTUNITIES")
    print("=" * 60)
    print("  (Queries where your CTR is below position benchmark)\n")

    rows, start, end = query_gsc(service, ["query"], days, row_limit=500)

    opportunities = []
    for row in rows:
        query = row["keys"][0]
        impressions = row.get("impressions", 0)
        clicks = row.get("clicks", 0)
        ctr = row.get("ctr", 0)
        position = row.get("position", 0)

        if impressions < 5:
            continue

        # Get benchmark CTR for this position
        pos_bucket = min(max(1, round(position)), 10)
        benchmark = CTR_BENCHMARKS.get(pos_bucket, 0.01)

        if ctr < benchmark:
            potential_clicks = int(impressions * benchmark) - clicks
            if potential_clicks > 0:
                opportunities.append({
                    "query": query,
                    "impressions": impressions,
                    "clicks": clicks,
                    "ctr": ctr,
                    "position": position,
                    "benchmark_ctr": benchmark,
                    "potential_extra_clicks": potential_clicks,
                })

    opportunities.sort(key=lambda x: x["potential_extra_clicks"], reverse=True)

    print(f"  {'Query':40s}  {'Pos':>5s}  {'Imp':>5s}  {'CTR':>6s}  {'Bench':>6s}  {'Extra Clicks':>12s}")
    print(f"  {'-'*40}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*12}")

    for opp in opportunities[:20]:
        print(
            f"  {opp['query'][:40]:40s}  {opp['position']:5.1f}  {opp['impressions']:5d}  "
            f"{opp['ctr']:5.1%}  {opp['benchmark_ctr']:5.1%}  "
            f"+{opp['potential_extra_clicks']:>11d}"
        )

    total_potential = sum(o["potential_extra_clicks"] for o in opportunities)
    print(f"\n  Total potential extra clicks: +{total_potential}")

    return {"ctr_opportunities": opportunities[:30], "total_potential_clicks": total_potential}


def analyze_query_clusters(service, days):
    """Cluster queries by language and intent."""
    print("\n\n4. QUERY CLUSTERS (Language + Intent)")
    print("=" * 60)

    rows, start, end = query_gsc(service, ["query"], days, row_limit=500)

    lang_stats = defaultdict(lambda: {"clicks": 0, "impressions": 0, "queries": []})
    intent_stats = defaultdict(lambda: {"clicks": 0, "impressions": 0, "queries": []})

    for row in rows:
        query = row["keys"][0]
        clicks = row.get("clicks", 0)
        impressions = row.get("impressions", 0)

        lang = detect_language(query)
        lang_stats[lang]["clicks"] += clicks
        lang_stats[lang]["impressions"] += impressions
        lang_stats[lang]["queries"].append(query)

        for intent in detect_intent(query):
            intent_stats[intent]["clicks"] += clicks
            intent_stats[intent]["impressions"] += impressions
            intent_stats[intent]["queries"].append(query)

    print("\n  By Language:")
    for lang, data in sorted(lang_stats.items(), key=lambda x: x[1]["impressions"], reverse=True):
        print(
            f"    {lang:10s}  {data['impressions']:>6d} imp  {data['clicks']:>5d} clicks  "
            f"({len(data['queries'])} queries)"
        )

    print("\n  By Intent:")
    for intent, data in sorted(intent_stats.items(), key=lambda x: x[1]["impressions"], reverse=True):
        print(
            f"    {intent:20s}  {data['impressions']:>6d} imp  {data['clicks']:>5d} clicks  "
            f"({len(data['queries'])} queries)"
        )

    return {
        "by_language": {k: {"clicks": v["clicks"], "impressions": v["impressions"], "query_count": len(v["queries"])} for k, v in lang_stats.items()},
        "by_intent": {k: {"clicks": v["clicks"], "impressions": v["impressions"], "query_count": len(v["queries"])} for k, v in intent_stats.items()},
    }


def analyze_page_performance(service, days):
    """Detailed page performance by type and language."""
    print("\n\n5. PAGE PERFORMANCE BY TYPE")
    print("=" * 60)

    rows, start, end = query_gsc(service, ["page"], days, row_limit=500)

    type_stats = defaultdict(lambda: {"clicks": 0, "impressions": 0, "pages": []})
    lang_page_stats = defaultdict(lambda: {"clicks": 0, "impressions": 0})

    for row in rows:
        page = row["keys"][0]
        clicks = row.get("clicks", 0)
        impressions = row.get("impressions", 0)
        position = row.get("position", 0)

        page_type = extract_page_type(page)
        type_stats[page_type]["clicks"] += clicks
        type_stats[page_type]["impressions"] += impressions
        type_stats[page_type]["pages"].append({
            "url": page, "clicks": clicks, "impressions": impressions, "position": position,
        })

        # Language breakdown
        lang = extract_lang_from_url(page)
        lang_page_stats[f"{page_type}_{lang}"]["clicks"] += clicks
        lang_page_stats[f"{page_type}_{lang}"]["impressions"] += impressions

    print(f"\n  {'Page Type':20s}  {'Clicks':>7s}  {'Impress':>8s}  {'Pages':>6s}")
    print(f"  {'-'*20}  {'-'*7}  {'-'*8}  {'-'*6}")

    for ptype, data in sorted(type_stats.items(), key=lambda x: x[1]["clicks"], reverse=True):
        print(f"  {ptype:20s}  {data['clicks']:>7d}  {data['impressions']:>8d}  {len(data['pages']):>6d}")

    # Event pages detail
    if "event_list" in type_stats or "event_detail" in type_stats:
        print("\n  Event Pages Detail:")
        all_event_pages = type_stats.get("event_list", {}).get("pages", []) + \
                          type_stats.get("event_detail", {}).get("pages", [])
        all_event_pages.sort(key=lambda x: x["clicks"], reverse=True)
        for p in all_event_pages[:10]:
            short = p["url"].replace("https://crush.lu", "")
            print(f"    {short:45s}  clicks={p['clicks']:>4d}  imp={p['impressions']:>5d}  pos={p['position']:.1f}")

    # Wasted crawl budget (login/signup/legal pages)
    wasted = 0
    wasted_pages = []
    for ptype in ("login", "signup", "legal"):
        if ptype in type_stats:
            wasted += type_stats[ptype]["impressions"]
            wasted_pages.extend(type_stats[ptype]["pages"])

    if wasted:
        print(f"\n  Wasted impressions (login/signup/legal): {wasted}")
        print(f"  (The noindex fix we deployed should reduce this over ~2-4 weeks)")

    return {
        "by_page_type": {k: {"clicks": v["clicks"], "impressions": v["impressions"], "page_count": len(v["pages"])} for k, v in type_stats.items()},
        "wasted_impressions": wasted,
    }


def analyze_striking_distance(service, days):
    """Keywords ranking positions 4-20 with high impressions (easy wins)."""
    print("\n\n6. STRIKING DISTANCE KEYWORDS (Position 4-20)")
    print("=" * 60)
    print("  (High-impression keywords close to page 1 — best ROI for content work)\n")

    rows, start, end = query_gsc(service, ["query"], days, row_limit=500)

    striking = []
    for row in rows:
        query = row["keys"][0]
        impressions = row.get("impressions", 0)
        clicks = row.get("clicks", 0)
        position = row.get("position", 0)
        ctr = row.get("ctr", 0)

        if 4 <= position <= 20 and impressions >= 5:
            striking.append({
                "query": query,
                "impressions": impressions,
                "clicks": clicks,
                "ctr": ctr,
                "position": position,
                "language": detect_language(query),
                "intents": detect_intent(query),
            })

    striking.sort(key=lambda x: x["impressions"], reverse=True)

    print(f"  {'Query':40s}  {'Pos':>5s}  {'Imp':>5s}  {'Clicks':>6s}  {'Lang':>5s}")
    print(f"  {'-'*40}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*5}")

    for kw in striking[:25]:
        print(
            f"  {kw['query'][:40]:40s}  {kw['position']:5.1f}  {kw['impressions']:5d}  "
            f"{kw['clicks']:>6d}  {kw['language']:>5s}"
        )

    return {"striking_distance": striking[:30]}


def analyze_target_keywords(service, days):
    """Track specific high-value target keywords over time."""
    print("\n\n7. TARGET KEYWORD TRACKING")
    print("=" * 60)

    rows, start, end = query_gsc(service, ["query"], days, row_limit=1000)

    # Build lookup
    query_data = {}
    for row in rows:
        query_data[row["keys"][0].lower()] = {
            "clicks": row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "ctr": row.get("ctr", 0),
            "position": row.get("position", 0),
        }

    print(f"\n  {'Keyword':40s}  {'Pos':>5s}  {'Imp':>5s}  {'Clicks':>6s}  {'CTR':>6s}  {'Status'}")
    print(f"  {'-'*40}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*15}")

    tracked = []
    for kw in TARGET_KEYWORDS:
        data = query_data.get(kw.lower())
        if data:
            if data["position"] <= 3:
                status = "TOP 3"
            elif data["position"] <= 10:
                status = "Page 1"
            elif data["position"] <= 20:
                status = "Striking dist."
            else:
                status = "Low"

            print(
                f"  {kw:40s}  {data['position']:5.1f}  {data['impressions']:5d}  "
                f"{data['clicks']:>6d}  {data['ctr']:5.1%}  {status}"
            )
            tracked.append({"keyword": kw, **data, "status": status})
        else:
            print(f"  {kw:40s}  {'---':>5s}  {'---':>5s}  {'---':>6s}  {'---':>6s}  Not ranking")
            tracked.append({"keyword": kw, "status": "Not ranking"})

    return {"target_keywords": tracked}


def analyze_search_appearance(service, days):
    """Check search appearance types (rich results, etc.)."""
    print("\n\n8. SEARCH APPEARANCE")
    print("=" * 60)

    try:
        rows, start, end = query_gsc(service, ["searchAppearance"], days, row_limit=50)
        if rows:
            for row in rows:
                appearance = row["keys"][0]
                print(
                    f"  {appearance:30s}  clicks={row.get('clicks', 0):>5d}  "
                    f"impressions={row.get('impressions', 0):>6d}"
                )
        else:
            print("  No special search appearances detected.")
            print("  Consider adding structured data (Event, Organization) for rich results.")
        return {"search_appearances": rows}
    except Exception as e:
        print(f"  Could not fetch search appearance data: {e}")
        return {"search_appearances_error": str(e)}


def print_recommendations(report):
    """Print actionable recommendations based on the analysis."""
    print("\n\n" + "=" * 60)
    print("ACTIONABLE RECOMMENDATIONS")
    print("=" * 60)

    recommendations = []

    # 1. CTR opportunities
    ctr_opps = report.get("ctr_opportunities", {}).get("ctr_opportunities", [])
    if ctr_opps:
        top = ctr_opps[0]
        rec = (
            f"TITLE/META: Improve titles for top CTR opportunities. "
            f'"{top["query"]}" has {top["impressions"]} impressions at position '
            f'{top["position"]:.1f} but only {top["ctr"]:.1%} CTR '
            f'(benchmark: {top["benchmark_ctr"]:.1%}). '
            f'Potential: +{top["potential_extra_clicks"]} clicks.'
        )
        recommendations.append(("HIGH", rec))

    # 2. Striking distance
    striking = report.get("striking_distance", {}).get("striking_distance", [])
    non_brand_striking = [s for s in striking if "brand" not in s.get("intents", [])]
    if non_brand_striking:
        top3 = non_brand_striking[:3]
        kw_list = ", ".join(f'"{s["query"]}"' for s in top3)
        rec = (
            f"CONTENT: Focus on striking-distance keywords: {kw_list}. "
            f"These rank positions 4-20 with good impression volume. "
            f"Strengthen on-page content and internal linking for these terms."
        )
        recommendations.append(("HIGH", rec))

    # 3. Cannibalization
    cannibal = report.get("query_page_matrix", {}).get("cannibalized_queries", {})
    high_imp_cannibal = {
        q: pages for q, pages in cannibal.items()
        if sum(p["impressions"] for p in pages) > 20
        and "brand" not in " ".join(detect_intent(q))
    }
    if high_imp_cannibal:
        example = list(high_imp_cannibal.keys())[0]
        rec = (
            f"CANNIBALIZATION: {len(high_imp_cannibal)} non-brand queries have multiple pages competing. "
            f'Example: "{example}" ranks on {len(high_imp_cannibal[example])} different pages. '
            f"Consolidate content or add canonical hints."
        )
        recommendations.append(("MEDIUM", rec))

    # 4. Language gaps
    clusters = report.get("query_clusters", {})
    lang_data = clusters.get("by_language", {})
    if lang_data:
        fr_imp = lang_data.get("fr", {}).get("impressions", 0)
        de_imp = lang_data.get("de", {}).get("impressions", 0)
        en_imp = lang_data.get("en", {}).get("impressions", 0)
        total = fr_imp + de_imp + en_imp
        if total > 0:
            fr_pct = fr_imp / total * 100 if total else 0
            de_pct = de_imp / total * 100 if total else 0
            rec = (
                f"i18n: French queries = {fr_pct:.0f}% of impressions, "
                f"German = {de_pct:.0f}%. "
                f"Ensure /fr/ and /de/ page content is fully localized (not just translated) "
                f"with local search terms in titles and meta descriptions."
            )
            recommendations.append(("MEDIUM", rec))

    # 5. Wasted budget
    wasted = report.get("page_performance", {}).get("wasted_impressions", 0)
    if wasted > 100:
        rec = (
            f"CRAWL BUDGET: {wasted} impressions wasted on login/signup/legal pages. "
            f"The noindex + robots.txt fix just deployed should reduce this. "
            f"Re-run this report in 2-3 weeks to verify."
        )
        recommendations.append(("LOW", rec))

    # 6. Structured data
    appearances = report.get("search_appearance", {}).get("search_appearances", [])
    if not appearances:
        rec = (
            "STRUCTURED DATA: No rich results detected. Add Event schema to event pages "
            "and Organization schema to the homepage for enhanced SERP snippets."
        )
        recommendations.append(("MEDIUM", rec))

    # 7. Target keyword gaps
    targets = report.get("target_keywords", {}).get("target_keywords", [])
    not_ranking = [t for t in targets if t.get("status") == "Not ranking"]
    if not_ranking:
        kws = ", ".join(f'"{t["keyword"]}"' for t in not_ranking[:3])
        rec = f"GAPS: Not ranking for target keywords: {kws}. Create or optimize content targeting these terms."
        recommendations.append(("MEDIUM", rec))

    for priority, rec in recommendations:
        print(f"\n  [{priority}] {rec}")

    if not recommendations:
        print("\n  No specific recommendations at this time. Performance looks healthy!")

    return [{"priority": p, "recommendation": r} for p, r in recommendations]


def main():
    parser = argparse.ArgumentParser(description="GSC Deep Dive for crush.lu")
    parser.add_argument("--days", type=int, default=28, help="Analysis period in days (default: 28)")
    parser.add_argument("--json", action="store_true", help="Save full report to JSON")
    args = parser.parse_args()

    print(f"=== GSC Deep Dive Analysis for crush.lu ({args.days} days) ===")

    creds = authenticate()
    service = build("searchconsole", "v1", credentials=creds)
    print("Authenticated successfully.")

    report = {}

    report["query_page_matrix"] = analyze_query_page_matrix(service, args.days)
    report["weekly_trends"] = analyze_weekly_trends(service, args.days)
    report["ctr_opportunities"] = analyze_ctr_opportunities(service, args.days)
    report["query_clusters"] = analyze_query_clusters(service, args.days)
    report["page_performance"] = analyze_page_performance(service, args.days)
    report["striking_distance"] = analyze_striking_distance(service, args.days)
    report["target_keywords"] = analyze_target_keywords(service, args.days)
    report["search_appearance"] = analyze_search_appearance(service, args.days)

    report["recommendations"] = print_recommendations(report)

    report["metadata"] = {
        "generated_at": datetime.now().isoformat(),
        "days_analyzed": args.days,
        "site": SITE_URL,
    }

    if args.json:
        OUTPUT_FILE.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False))
        print(f"\n\nFull report saved to {OUTPUT_FILE}")

    print("\n\nDone. Run with --days 90 for longer trends or --json for full data export.")


if __name__ == "__main__":
    main()
