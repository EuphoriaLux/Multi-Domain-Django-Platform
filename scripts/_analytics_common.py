"""
Shared helpers for Google analytics reporting scripts.

Used by seo_funnel_report.py. The older gsc_report.py and ga4_report.py
intentionally keep their own copies to avoid touching working code.
"""

import csv
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = SCRIPT_DIR / "gsc_credentials.json"
EXPORT_DIR = SCRIPT_DIR / "exports"


def load_oauth_credentials(token_filename, scopes):
    """Load or create OAuth2 user credentials for a given token file + scope set."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = SCRIPT_DIR / token_filename
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"ERROR: {CREDENTIALS_FILE} not found.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), scopes
            )
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


def write_csv(filename, headers, rows):
    """Write a list of dicts to scripts/exports/<filename>."""
    EXPORT_DIR.mkdir(exist_ok=True)
    filepath = EXPORT_DIR / filename
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  -> {filepath}")


def print_table(title, headers, rows, max_col_width=60):
    """Pretty-print a list-of-dicts table to the terminal."""
    if not rows:
        print(f"\n=== {title} ===")
        print("  (no data)")
        return

    col_widths = []
    for h in headers:
        values = [str(r.get(h, "")) for r in rows]
        longest = max((len(v) for v in values), default=5)
        col_widths.append(min(max(len(h), longest) + 2, max_col_width + 2))

    def trunc(v, w):
        s = str(v)
        return s if len(s) <= w - 2 else s[: w - 3] + "…"

    print(f"\n=== {title} ===")
    header_line = "".join(
        h.ljust(w) if i == 0 else h.rjust(w)
        for i, (h, w) in enumerate(zip(headers, col_widths))
    )
    print(header_line)
    print("-" * sum(col_widths))
    for row in rows:
        vals = [trunc(row.get(h, ""), w) for h, w in zip(headers, col_widths)]
        line = "".join(
            v.ljust(w) if i == 0 else v.rjust(w)
            for i, (v, w) in enumerate(zip(vals, col_widths))
        )
        print(line)
