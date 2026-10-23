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
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

# Every site from azureproject/domains.py with its staging alias. Keep in
# sync with DOMAIN_MAPPINGS there. api.crush.lu is intentionally absent:
# it is mapped in domains.py but has no DNS record on either environment
# (checked 2026-07-06).
SITES = [
    {"prod": "crush.lu", "staging": "test.crush.lu", "deep": True},
    {"prod": "entreprinder.lu", "staging": "test.entreprinder.lu"},
    {"prod": "power-up.lu", "staging": "test.power-up.lu"},
    {"prod": "powerup.lu", "staging": "test.powerup.lu"},
    # portal has no root URL by design — probe the CRM entry point (an
    # anonymous request lands on the admin login page).
    {
        "prod": "portal.powerup.lu",
        "staging": "test-portal.powerup.lu",
        "home_path": "/crm/",
    },
    {"prod": "vinsdelux.com", "staging": "test.vinsdelux.com"},
    {"prod": "tableau.lu", "staging": "test.tableau.lu"},
    {"prod": "arborist.lu", "staging": "test.arborist.lu"},
    {"prod": "delegations.lu", "staging": "test.delegations.lu"},
]
TIMEOUT = 30
# How long the readyz check keeps retrying a request that got no answer, and
# how often (see check_readyz). Each attempt can itself use the full TIMEOUT,
# so 120/10 means attempts at about 0, 40 and 80 seconds: three tries, and a
# host that never answers costs ~110 s here instead of 30.
READYZ_RETRY_SECONDS = 120
READYZ_RETRY_INTERVAL = 10
# How long the stylesheet check keeps retrying a failed fetch, and how often
# (see check_generated_assets).
STYLESHEET_RETRY_SECONDS = 120
STYLESHEET_RETRY_INTERVAL = 10
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
    # A request that gets no answer is retried for a bounded time. The deploy
    # gate has already seen this build report ready, so a timeout here is one
    # stalled request, not a missing process: the staging deploys on
    # 2026-10-21 (test.power-up.lu) and 2026-10-23 (test.crush.lu) each went
    # red on a single /readyz/ TimeoutError while every other host passed,
    # and the host answered in under a second minutes later. An answer is
    # never retried: after the gate, a 503 or a failed check means a
    # dependency went away, and waiting would only hide that.
    deadline = time.monotonic() + READYZ_RETRY_SECONDS
    while True:
        try:
            status, _, body = fetch(url)
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read()
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            if time.monotonic() + READYZ_RETRY_INTERVAL < deadline:
                print(f"  wait {name}: {url} -> {type(exc).__name__}; retrying")
                time.sleep(READYZ_RETRY_INTERVAL)
                continue
            failures.append(f"{name}: {url} -> {type(exc).__name__}: {exc}")
            print(f"  FAIL {name}: {type(exc).__name__}")
            return
        break
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


def check_generated_assets(base):
    """The generated (not committed) assets shipped: DE/FR render and the
    compiled stylesheet the home page links is served and non-trivial."""
    global passes
    for lang in ("de", "fr"):
        check(
            f"{lang} home renders",
            f"{base}/{lang}/",
            contains=f'lang="{lang}"',
        )
    name = "tailwind.css is served"
    # A failed fetch is retried for a bounded time, re-reading the page each
    # attempt. Until the new container takes over, the slot can still answer
    # from the previous process. Its HTML links the previous build's hashed
    # stylesheet, which the `--clean` zip deploy has already deleted, so that
    # fetch 500s (FileNotFoundError in WhiteNoise). The deploy gate now waits
    # for the new process (readiness.build_info), so this is only a backstop.
    # A stylesheet still unreachable when the budget runs out fails the run,
    # and a missing link or a truncated file fails at once: waiting cannot
    # fix either of those.
    deadline = time.monotonic() + STYLESHEET_RETRY_SECONDS
    while True:
        url = f"{base}/en/"
        try:
            _, _, html = fetch(url)
            match = re.search(rb'href="([^"]*crush_lu/css/tailwind[^"]*\.css)"', html)
            if not match:
                raise ValueError("home page links no crush_lu tailwind stylesheet")
            href = match.group(1).decode()
            url = href if href.startswith("http") else f"{base}{href}"
            _, _, css = fetch(url)
            if len(css) < 50_000:
                raise ValueError(f"only {len(css)} bytes")
        except ValueError as exc:
            error = exc
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            error = exc
            if time.monotonic() + STYLESHEET_RETRY_INTERVAL < deadline:
                print(f"  wait {name}: {url} -> {exc}; retrying")
                time.sleep(STYLESHEET_RETRY_INTERVAL)
                continue
        else:
            error = None
        break
    if error is not None:
        failures.append(f"{name}: {url} -> {type(error).__name__}: {error}")
        print(f"  FAIL {name}: {url} -> {error}")
    else:
        passes += 1
        print(f"  ok   {name}")


def smoke_domain(base, deep, home_path="/"):
    print(f"\n{base}")
    check("healthz", f"{base}/healthz/", contains="OK")
    check_readyz(base)
    check("home", f"{base}{home_path}")
    if deep:
        check("sitemap", f"{base}/sitemap.xml", contains="<urlset")
        check("robots", f"{base}/robots.txt", contains="Disallow")
        check("login page", f"{base}/accounts/login/")
        check_generated_assets(base)
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
        targets = [(b.rstrip("/"), "crush" in b, "/") for b in args.bases]
    elif args.env:
        host_key = "staging" if args.env == "staging" else "prod"
        targets = [
            (
                f"https://{site[host_key]}",
                site.get("deep", False),
                site.get("home_path", "/"),
            )
            for site in SITES
        ]
    else:
        parser.error("pass base URLs or --env staging|production")

    for base, deep, home_path in targets:
        smoke_domain(base, deep, home_path)

    print(f"\n{passes} passed, {len(failures)} failed")
    if failures:
        print("\nFailures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
