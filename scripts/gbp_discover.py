"""
Google Business Profile (GBP) Discovery & Setup Script for Crush.lu

Discovers GBP Account ID and Location IDs for Crush.lu using Google Business Profile APIs.

Usage:
    .venv/Scripts/python.exe scripts/gbp_discover.py
"""

import os
import sys
from pathlib import Path
import requests

# Ensure unbuffered stdout on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/business.manage",
]
SCRIPT_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = SCRIPT_DIR / "gsc_credentials.json"
TOKEN_FILE = SCRIPT_DIR / "gbp_token.json"


def get_credentials():
    """Load or create OAuth2 credentials for Google Business Profile."""
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"ERROR: {CREDENTIALS_FILE} not found.", flush=True)
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            # Run local server on port 8085
            print("Starting local OAuth server on http://localhost:8085...", flush=True)
            creds = flow.run_local_server(
                port=8085,
                prompt="consent",
                authorization_prompt_message="\n👉 Click or paste this URL into your browser to authorize:\n\n{url}\n\n",
                success_message="Authentication successful! You can close this tab and return to the terminal.",
                open_browser=False,
            )

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print(f"\n✅ OAuth token saved to {TOKEN_FILE.name}\n", flush=True)

    return creds


def main():
    print("=" * 60, flush=True)
    print(" Google Business Profile Discovery (Crush.lu)", flush=True)
    print("=" * 60, flush=True)

    creds = get_credentials()
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }

    # 1. Fetch Accounts
    print("\n🔍 Querying Google Business Profile Accounts...", flush=True)
    accounts_url = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
    resp = requests.get(accounts_url, headers=headers)

    if resp.status_code != 200:
        print(f"❌ Error fetching accounts (HTTP {resp.status_code}):", flush=True)
        print(resp.text, flush=True)
        if resp.status_code == 403:
            print("\n💡 Tip: Make sure 'My Business Account Management API' is enabled in Google Cloud Console.", flush=True)
        return

    accounts_data = resp.json()
    accounts = accounts_data.get("accounts", [])

    if not accounts:
        print("⚠️ No Google Business Profile accounts found for this Google user.", flush=True)
        return

    print(f"✅ Found {len(accounts)} account(s):\n", flush=True)

    for acc in accounts:
        acc_name = acc.get("name")
        acc_title = acc.get("accountName", "Unnamed Account")
        acc_type = acc.get("type", "PERSONAL")
        print(f"🏢 Account: {acc_title}", flush=True)
        print(f"   Resource Name: {acc_name}", flush=True)
        print(f"   Type:          {acc_type}", flush=True)

        # 2. Fetch Locations for this Account
        print(f"\n   📍 Fetching locations for {acc_title}...", flush=True)
        locations_url = f"https://mybusinessbusinessinformation.googleapis.com/v1/{acc_name}/locations"
        params = {
            "readMask": "name,title,storefrontAddress,websiteUri,phoneNumbers,categories,metadata"
        }
        loc_resp = requests.get(locations_url, headers=headers, params=params)

        if loc_resp.status_code != 200:
            print(f"   ❌ Error fetching locations (HTTP {loc_resp.status_code}):", flush=True)
            print(f"   {loc_resp.text}", flush=True)
            if loc_resp.status_code == 403:
                print("   💡 Tip: Make sure 'My Business Business Information API' is enabled in Google Cloud Console.", flush=True)
            continue

        loc_data = loc_resp.json()
        locations = loc_data.get("locations", [])

        if not locations:
            print("   ⚠️ No verified locations found under this account.", flush=True)
            continue

        print(f"   ✅ Found {len(locations)} location(s):\n", flush=True)
        for loc in locations:
            loc_name = loc.get("name")
            loc_title = loc.get("title", "Untitled")
            website = loc.get("websiteUri", "N/A")
            addr = loc.get("storefrontAddress", {})
            address_lines = ", ".join(addr.get("addressLines", []))
            locality = addr.get("locality", "")
            postal_code = addr.get("postalCode", "")
            country = addr.get("regionCode", "")

            full_addr = f"{address_lines}, {postal_code} {locality} ({country})".strip(", ")

            print(f"      📍 Name:        {loc_title}", flush=True)
            print(f"         Location ID: {loc_name}", flush=True)
            print(f"         Address:     {full_addr}", flush=True)
            print(f"         Website:     {website}", flush=True)
            print(flush=True)

    print("=" * 60, flush=True)
    print(" Discovery Complete!", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
