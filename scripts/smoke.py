#!/usr/bin/env python3
"""
Post-deploy / post-swap smoke test. Stdlib-only — runs anywhere.

Probes the live site the way a user (and the Azure slot-swap warm-up) hits
it: liveness, deep readiness, home pages, sitemap, and the anonymous
dashboard->login redirect on crush.lu.

Usage:
    python scripts/smoke.py --env staging          # test.* hosts
    python scripts/smoke.py --env production       # live hosts (run after every swap)
    python scripts/smoke.py https://test.crush.lu  # explicit base URLs

Exit code 0 = all checks passed, 1 = at least one failure (failures are
listed; the script never stops at the first one).
"""

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request

DOMAINS = ["crush.lu", "entreprinder.lu", "powerup.lu", "vinsdelux.com"]
TIMEOUT = 30
USER_AGENT = "crush-smoke/1.0 (+scripts/smoke.py)"

failures = []
passes = 0


def fetch(url):
    """GET following redirects. Returns (status, final_url, body_bytes)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as resp:
        return resp.status, resp.geturl(), resp.read()


def check(name, url, expect_status=200, contains=None, final_url_contains=None):
    global passes
    try:
        status, final_url, body = fetch(url)
    except urllib.error.HTTPError as exc:
        status, final_url, body = exc.code, url, exc.read()
    except Exception as exc:  # noqa: BLE001 - report, don't crash the run
        failures.append(f"{name}: {url} -> {type(exc).__name__}: {exc}")
        print(f"  FAIL {name}: {type(exc).__name__}")
        return
    problems = []
    if status != expect_status:
        problems.append(f"status {status} != {expect_status}")
    if contains is not None and contains.encode() not in body:
        problems.append(f"body missing {contains!r}")
    if final_url_contains is not None and final_url_contains not in final_url:
        problems.append(f"final URL {final_url} missing {final_url_contains!r}")
    if problems:
        failures.append(f"{name}: {url} -> " + "; ".join(problems))
        print(f"  FAIL {name}: " + "; ".join(problems))
    else:
        passes += 1
        print(f"  ok   {name}")


def check_readyz(base):
    """Deep readiness: 200 AND every individual check reporting ok."""
    global passes
    name = "readyz"
    url = f"{base}/readyz/"
    try:
        status, _, body = fetch(url)
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{name}: {url} -> {type(exc).__name__}: {exc}")
        print(f"  FAIL {name}: {type(exc).__name__}")
        return
    try:
        payload = json.loads(body)
    except ValueError:
        payload = {}
    if status != 200 or payload.get("status") != "ok":
        failures.append(
            f"{name}: {url} -> status {status}, checks {payload.get('checks')}"
        )
        print(f"  FAIL {name}: status {status}, checks {payload.get('checks')}")
    else:
        passes += 1
        print(f"  ok   {name} {payload['checks']}")


def smoke_domain(base, deep):
    print(f"\n{base}")
    check("healthz", f"{base}/healthz/", contains="OK")
    check_readyz(base)
    check("home", f"{base}/")
    if deep:
        check("sitemap", f"{base}/sitemap.xml", contains="<urlset")
        check("robots", f"{base}/robots.txt", contains="Disallow")
        check("login page", f"{base}/accounts/login/")
        check(
            "anonymous dashboard redirects to login",
            f"{base}/en/dashboard/",
            final_url_contains="login",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bases", nargs="*", help="explicit base URLs (override --env)")
    parser.add_argument("--env", choices=["staging", "production"])
    args = parser.parse_args()

    if args.bases:
        bases = [b.rstrip("/") for b in args.bases]
    elif args.env:
        prefix = "test." if args.env == "staging" else ""
        bases = [f"https://{prefix}{d}" for d in DOMAINS]
    else:
        parser.error("pass base URLs or --env staging|production")

    for base in bases:
        smoke_domain(base, deep="crush" in base)

    print(f"\n{passes} passed, {len(failures)} failed")
    if failures:
        print("\nFailures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
