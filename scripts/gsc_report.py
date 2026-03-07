"""
Google Search Console - Indexing & Performance Report
=====================================================
Pulls indexing status and search analytics from Google Search Console API.

Prerequisites:
1. Go to https://console.cloud.google.com/
2. Enable "Google Search Console API" (searchconsole.googleapis.com)
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download the JSON file and save as scripts/gsc_credentials.json

Usage:
    .venv/Scripts/python.exe scripts/gsc_report.py

First run will open a browser for Google login. Token is cached in scripts/gsc_token.json.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCRIPT_DIR = Path(__file__).parent
CREDENTIALS_FILE = SCRIPT_DIR / "gsc_credentials.json"
TOKEN_FILE = SCRIPT_DIR / "gsc_token.json"
OUTPUT_FILE = SCRIPT_DIR / "gsc_report.json"

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
SITE_URL = "sc-domain:crush.lu"  # Domain property format


def authenticate():
    """Authenticate via OAuth2 (browser-based)."""
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"\nERROR: {CREDENTIALS_FILE} not found!")
                print("\nSetup steps:")
                print("1. Go to https://console.cloud.google.com/apis/credentials")
                print("2. Click '+ CREATE CREDENTIALS' > 'OAuth client ID'")
                print("3. Application type: 'Desktop app'")
                print("4. Download JSON and save as: scripts/gsc_credentials.json")
                print("5. Also enable the Search Console API:")
                print("   https://console.cloud.google.com/apis/library/searchconsole.googleapis.com")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())
        print(f"Token saved to {TOKEN_FILE}")

    return creds


def get_sites(service):
    """List all verified sites."""
    result = service.sites().list().execute()
    return result.get("siteEntry", [])


def get_sitemaps(service, site_url):
    """Get sitemap status."""
    result = service.sitemaps().list(siteUrl=site_url).execute()
    return result.get("sitemap", [])


def get_search_analytics(service, site_url, days=28):
    """Get search performance data (queries, pages, clicks, impressions)."""
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)

    # Top queries
    queries_response = (
        service.searchanalytics()
        .query(
            siteUrl=site_url,
            body={
                "startDate": str(start_date),
                "endDate": str(end_date),
                "dimensions": ["query"],
                "rowLimit": 50,
                "dataState": "final",
            },
        )
        .execute()
    )

    # Top pages
    pages_response = (
        service.searchanalytics()
        .query(
            siteUrl=site_url,
            body={
                "startDate": str(start_date),
                "endDate": str(end_date),
                "dimensions": ["page"],
                "rowLimit": 100,
                "dataState": "final",
            },
        )
        .execute()
    )

    # By country
    country_response = (
        service.searchanalytics()
        .query(
            siteUrl=site_url,
            body={
                "startDate": str(start_date),
                "endDate": str(end_date),
                "dimensions": ["country"],
                "rowLimit": 20,
                "dataState": "final",
            },
        )
        .execute()
    )

    # By device
    device_response = (
        service.searchanalytics()
        .query(
            siteUrl=site_url,
            body={
                "startDate": str(start_date),
                "endDate": str(end_date),
                "dimensions": ["device"],
                "rowLimit": 10,
                "dataState": "final",
            },
        )
        .execute()
    )

    return {
        "period": f"{start_date} to {end_date}",
        "top_queries": queries_response.get("rows", []),
        "top_pages": pages_response.get("rows", []),
        "by_country": country_response.get("rows", []),
        "by_device": device_response.get("rows", []),
    }


def inspect_urls(service, site_url, urls):
    """Inspect indexing status for specific URLs."""
    results = []
    for url in urls:
        try:
            response = (
                service.urlInspection()
                .index()
                .inspect(
                    body={
                        "inspectionUrl": url,
                        "siteUrl": site_url,
                    }
                )
                .execute()
            )
            inspection = response.get("inspectionResult", {})
            index_status = inspection.get("indexStatusResult", {})
            results.append(
                {
                    "url": url,
                    "verdict": index_status.get("verdict", "UNKNOWN"),
                    "coverage_state": index_status.get("coverageState", "UNKNOWN"),
                    "indexing_state": index_status.get("indexingState", "UNKNOWN"),
                    "last_crawl_time": index_status.get("lastCrawlTime"),
                    "page_fetch_state": index_status.get("pageFetchState"),
                    "robots_txt_state": index_status.get("robotsTxtState"),
                    "crawled_as": index_status.get("crawledAs"),
                    "referring_urls": index_status.get("referringUrls", []),
                }
            )
            print(f"  Inspected: {url} -> {index_status.get('verdict', '?')}")
        except Exception as e:
            results.append({"url": url, "error": str(e)})
            print(f"  Error inspecting {url}: {e}")
    return results


def main():
    print("=== Google Search Console Report for crush.lu ===\n")

    # Authenticate
    print("Authenticating...")
    creds = authenticate()
    service = build("searchconsole", "v1", credentials=creds)

    report = {}

    # 1. List verified sites
    print("\n1. Checking verified sites...")
    sites = get_sites(service)
    report["sites"] = [
        {"url": s.get("siteUrl"), "permission": s.get("permissionLevel")}
        for s in sites
    ]
    for s in sites:
        print(f"   {s.get('siteUrl')} ({s.get('permissionLevel')})")

    # Check if crush.lu is verified
    site_urls = [s.get("siteUrl") for s in sites]
    actual_site_url = None
    for candidate in [SITE_URL, "https://crush.lu/", "http://crush.lu/"]:
        if candidate in site_urls:
            actual_site_url = candidate
            break

    if not actual_site_url:
        print(f"\n  WARNING: crush.lu not found in verified sites!")
        print(f"  Available sites: {site_urls}")
        print(f"  Using {SITE_URL} anyway (may fail)...")
        actual_site_url = SITE_URL

    report["site_url_used"] = actual_site_url

    # 2. Sitemaps
    print("\n2. Checking sitemaps...")
    try:
        sitemaps = get_sitemaps(service, actual_site_url)
        report["sitemaps"] = []
        for sm in sitemaps:
            sm_info = {
                "path": sm.get("path"),
                "type": sm.get("type"),
                "submitted": sm.get("lastSubmitted"),
                "last_downloaded": sm.get("lastDownloaded"),
                "is_pending": sm.get("isPending"),
                "warnings": sm.get("warnings"),
                "errors": sm.get("errors"),
                "contents": sm.get("contents", []),
            }
            report["sitemaps"].append(sm_info)
            print(f"   {sm.get('path')} - warnings: {sm.get('warnings')}, errors: {sm.get('errors')}")
    except Exception as e:
        print(f"   Error: {e}")
        report["sitemaps_error"] = str(e)

    # 3. Search analytics
    print("\n3. Pulling search analytics (last 28 days)...")
    try:
        analytics = get_search_analytics(service, actual_site_url)
        report["search_analytics"] = analytics
        print(f"   Top queries: {len(analytics['top_queries'])}")
        print(f"   Pages with data: {len(analytics['top_pages'])}")
        for q in analytics["top_queries"][:10]:
            keys = q.get("keys", ["?"])
            print(
                f"     '{keys[0]}' - clicks: {q.get('clicks', 0)}, "
                f"impressions: {q.get('impressions', 0)}, "
                f"ctr: {q.get('ctr', 0):.1%}, "
                f"position: {q.get('position', 0):.1f}"
            )
    except Exception as e:
        print(f"   Error: {e}")
        report["search_analytics_error"] = str(e)

    # 4. URL inspection for key pages
    print("\n4. Inspecting key URLs...")
    key_urls = [
        "https://crush.lu/",
        "https://crush.lu/en/",
        "https://crush.lu/de/",
        "https://crush.lu/fr/",
        "https://crush.lu/en/events/",
        "https://crush.lu/en/about/",
        "https://crush.lu/sitemap.xml",
    ]
    try:
        url_inspections = inspect_urls(service, actual_site_url, key_urls)
        report["url_inspections"] = url_inspections
    except Exception as e:
        print(f"   Error: {e}")
        report["url_inspections_error"] = str(e)

    # Save report
    OUTPUT_FILE.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n=== Report saved to {OUTPUT_FILE} ===")
    print("Share this file with Claude Code for analysis.")


if __name__ == "__main__":
    main()
