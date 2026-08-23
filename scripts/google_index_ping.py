"""
Google Search Indexing API Ping & Notification Tool for Crush.lu

Publishes instant indexing notifications (URL_UPDATED or URL_DELETED) to Googlebot.

Usage:
    .venv/Scripts/python.exe scripts/google_index_ping.py
    .venv/Scripts/python.exe scripts/google_index_ping.py --url https://crush.lu/en/events/15/
    .venv/Scripts/python.exe scripts/google_index_ping.py --status https://crush.lu/en/events/
"""

import argparse
import json
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/indexing"]
SCRIPT_DIR = Path(__file__).resolve().parent
KEY_FILE = SCRIPT_DIR / "google_indexing_key.json"
DEFAULT_URL = "https://crush.lu/en/events/"


def get_service():
    """Build the Indexing API service client using the Service Account key."""
    if not KEY_FILE.exists():
        print(f"❌ ERROR: Service account key not found at {KEY_FILE}")
        sys.exit(1)

    credentials = service_account.Credentials.from_service_account_file(
        str(KEY_FILE), scopes=SCOPES
    )
    return build("indexing", "v3", credentials=credentials)


def publish_notification(url: str, action: str = "URL_UPDATED"):
    """Notify Googlebot that a URL has been updated or deleted."""
    service = get_service()
    body = {
        "url": url,
        "type": action,
    }
    print(f"\n🚀 Sending Indexing Ping to Googlebot...")
    print(f"   Target URL: {url}")
    print(f"   Action:     {action}")

    try:
        response = service.urlNotifications().publish(body=body).execute()
        print("\n✅ Googlebot Received Notification Successfully!")
        print(json.dumps(response, indent=2))
        return response
    except Exception as e:
        print(f"\n❌ Error publishing indexing notification: {e}")
        sys.exit(1)


def get_notification_status(url: str):
    """Query the latest metadata/status recorded by Google for a given URL."""
    service = get_service()
    print(f"\n🔍 Querying Indexing Status from Google...")
    print(f"   Target URL: {url}")

    try:
        response = service.urlNotifications().getMetadata(url=url).execute()
        print("\n📊 Latest Google Indexing Status:")
        print(json.dumps(response, indent=2))
        return response
    except Exception as e:
        if "404" in str(e):
            print("\n⏳ Status: Notification accepted by Googlebot (crawl queued / pending).")
        else:
            print(f"\n❌ Error querying indexing status: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Google Indexing API for Crush.lu")
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="URL to notify Googlebot about (default: https://crush.lu/en/events/)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Send URL_DELETED notification instead of URL_UPDATED",
    )
    parser.add_argument(
        "--status",
        help="Query the indexing metadata status for a given URL",
    )

    args = parser.parse_args()

    print("=" * 60)
    print(" Google Search Indexing API — Crush.lu")
    print("=" * 60)

    if args.status:
        get_notification_status(args.status)
    else:
        action = "URL_DELETED" if args.delete else "URL_UPDATED"
        publish_notification(args.url, action=action)
        print("\n" + "=" * 60)
        get_notification_status(args.url)
        print("=" * 60)


if __name__ == "__main__":
    main()
