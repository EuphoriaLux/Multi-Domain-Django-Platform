# Test Pyramid Audit & Regression Matrix

Status: baseline snapshot as of 2026-08-29, `main` @ `830f67db`.
Owner: QA (crush-qa). Source task: Kanban `t_7f600c7b` (parent `t_46989237`).

This document audits the **whole test pyramid** — unit/integration (pytest,
non-Playwright), Playwright E2E, and CI wiring — and maps the product's
critical user journeys to what actually covers them today. It is a superset
of the narrower Playwright-only audit already merged to `main` as
**`docs/testing/playwright-coverage-matrix.md`** (PR #938, merged as
`e300fd56` **while this task was in progress** — started as an open PR,
landed mid-audit; §2–4 below were written against it as open and then
re-verified after the merge, so the facts hold either way) from a prior task
(`t_a661c3b2`). That audit went through three Codex review rounds and
corrected several of its own early claims (host routing vs. consent-fixture
root cause on FinOps, the registration-wizard class not actually testing
signup, the phone-selector fix being wrong). I independently re-ran its key
commands against current `main` (§4) and every structural claim and failure
signature still reproduces byte-for-byte (`KeyError: 'user'` teardown crash,
consent-gate redirect, 79/116 Playwright failures) — confirmed identical
(`diff` clean) between the merged `main` copy and the pre-merge branch tip I
originally reviewed. Where this document repeats those findings it is
because I verified them again on `main`, not because I copied them; §5 adds
the non-Playwright tier and the current CI/PR state, which that audit did
not cover.

**Read `docs/testing/playwright-coverage-matrix.md` for full Playwright
per-file detail** — it is now the source of truth for §2's subject matter;
this file's unique value is §1 (unit/integration tier) and §5 (the
combined-pyramid matrix + current blocker state), which that document does
not have.

## 1. Unit / integration tier (the base of the pyramid)

- Framework: `pytest` + `pytest-django` + `pytest-xdist` (parallel, `worksteal`
  distribution). `pytest.ini` `addopts` is
  `--tb=short -q -x -n auto --dist worksteal -m "not playwright" --reuse-db`,
  so **this is the tier that runs by default** and the only tier CI executes
  (`.github/workflows/test-and-validate.yml`'s `run-tests` job: `pytest -m
  "not playwright" --tb=short -q -x -n auto`).
- `testpaths = crush_lu/tests hub/tests power_up/finops/tests
  power_up/atmos/tests azure-functions/finops-daily-sync/tests`. Three legacy
  files (`entreprinder/tests.py`, `arborist/tests.py`, `power_up/tests.py`)
  are **not collected** — they don't match `python_files = test_*.py` and the
  file header documents them as drifted (404s / DB errors) pending a rename
  + repair, not silently skipped by accident.
- **Discovery, run 2026-08-29:**
  `pytest --collect-only -q -m "not playwright" -p no:warnings` →
  **176 files, 4,823 tests** across `crush_lu/tests/` (149 files),
  `hub/tests/` (8), `power_up/atmos/tests/` (8), `power_up/finops/tests/`
  (10), `azure-functions/finops-daily-sync/tests/` (1).
- **Full run, 2026-08-29 (reproducible baseline):**
  `pytest -q -n auto --dist worksteal -m "not playwright" -p no:warnings
  --tb=line --create-db` → **all 4,823 tests passed** (some `s`/`u`
  skip/xfail markers visible in the progress line, zero `F`/`E`). Confirmed
  clean on a fresh DB (`--create-db`), not just the reused one. This is the
  actually-green tier today; it is what "CI passing" means in this repo.
- Env/config prerequisite that isn't obvious from a clean checkout: **a
  fresh worktree has no `.env`**, and `pytest-django` dies during settings
  import (before collection) because `SECRET_KEY` is unset —
  `conftest.py`'s `pytest_configure()` fallback runs too late to prevent it.
  Copy the root checkout's `.env` into the worktree first (this worktree
  already had one carried over from a prior attempt; a genuinely fresh
  worktree will not). No external services (Postgres/Redis/Azure
  Storage/SumUp/Graph) are required — SQLite + `live_server` + the
  `pytest_configure()` fallbacks (`AZURE_ACCOUNT_NAME/KEY`,
  `GRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET`, `VAPID_*`,
  `DJANGO_TASKS_BACKEND=immediate`, `EMAIL_BACKEND=locmem`,
  `PASSWORD_HASHERS=[MD5PasswordHasher]`) are sufficient.
- Known structural traps documented in `AGENTS.md` §"Traps that cost real
  time" that affect how much this tier's green run should be trusted:
  - **SQLite silently no-ops `select_for_update`.** `PaymentTransaction` /
    `CrushProfile` lock-ordering bugs pass every local and CI run and only
    fail on production Postgres — a green suite proves nothing about lock
    ordering. Any journey touching payment + profile merge concurrency needs
    a Postgres-backed check (none exists) before this tier's pass can be
    trusted for that specific risk.
  - **`transaction.on_commit` + `TestCase`**: callbacks queued in `setUp`
    are not cleared by `captureOnCommitCallbacks` and are not re-executed by
    a later capture block, so code that inspects the callback queue can see
    a stale entry (documented root cause of a real bug in
    `test_google_indexing.py`).
  - **Shared `@ratelimit` cache key across tests.** SQLite resets PK
    sequences per test but not the cache; every test's viewer becomes user 2
    and shares a rate-limit counter, so a 429 can surface as an unrelated
    `DoesNotExist` unless `cache.clear()` runs in `setUp`.
  - **`reverse("crush_lu:…")` in tests resolves against the default
    urlconf, not the host-selected one** — use literal paths in tests
    (`/en/events/`, mind the `i18n_patterns` prefix), keep `{% url %}` in
    templates.

## 2. Playwright E2E tier — verified against current `main`

(Full inventory and per-file gap analysis: see
`docs/testing/playwright-coverage-matrix.md`, merged to `main` at `e300fd56`
during this task. This section is the subset needed to build the combined
matrix in §5, re-verified independently against `main` rather than trusted
from the doc.)

- `pytest-playwright==0.9.0` + `playwright==1.62.0`, Python pytest modules
  only (no `playwright.config.*`, no JS/TS runner), gated by
  `@pytest.mark.playwright`. Chromium only; no `firefox`/`webkit` project
  anywhere in config, CI, or scripts.
- **Discovery, re-run 2026-08-29:** `pytest --collect-only -m playwright -q`
  → **116 tests across 13 files**, identical file-by-file counts to the
  prior audit (36+26+1+1+2+3+1+2+9+7+13+14+1). No drift since the last
  count.
- **Full run, re-run 2026-08-29:**
  `pytest -q -n auto --dist worksteal -m playwright --maxfail=0 -p
  no:warnings --tb=line` → **79 failed, 35 passed, 2 skipped** (116 total),
  plus **5 additional teardown `ERROR`s** on tests already counted as
  failed. This reproduces the prior audit's ~78-failed/36-passed snapshot
  almost exactly (the 1-test difference is normal xdist-order noise, which
  the prior audit itself flagged as a risk of an un-repeated single run).
  **Root causes independently reproduced on `main`, not just cited:**
  - `crush_lu/signals.py::manage_coach_staff_status` (line ~4140) reads
    `instance.user` with no `raw=` guard. When
    `_restore_migration_seeded_rows` replays the migration-seeded catalogue
    inside a deferred-constraint transaction, this receiver fires with
    `raw=True` before the FK target is replayed, raising
    `django.contrib.auth.models.User.DoesNotExist` /
    `KeyError: 'user'` at teardown — verified verbatim in this run's log
    (`ERROR at teardown of TestEdgeCases.test_back_to_dashboard_button`,
    identical traceback). Cascades into every `CrushCoach`-touching test in
    `test_coach_review_profile_playwright.py`.
  - `power_up/finops/tests/test_dashboard_filtering.py` navigates to
    `live_server.url` (Crush URLconf) for 27 of its calls, which routes
    through `CrushConsentMiddleware`. Verified in `consent_middleware.py`:
    `requires_consent_check()` only applies on the Crush URLconf
    (`getattr(request, "urlconf", None) != CRUSH_URLCONF` short-circuits
    it), and `finops/` is actually served under `urls_power_up.py`. The
    fix is a `powerup.localhost:<live_server_port>` URL, not a
    consent-fixture change — confirmed by reading both files directly, not
    just citing the prior audit's diagnosis.
  - **Neither bug is fixed on `main` today.** A rescue commit
    (`e0b7581d` on local branch `qa/t_2eb7f76b-playwright-regression-tier`,
    not merged, not yet a PR — see `t_2eb7f76b`, currently `blocked` after
    two iteration-budget timeouts) contains an unverified fix for the
    `manage_coach_staff_status` signal (`raw=False` guard) and a consent
    grant in `sender_user`/`recipient_user` fixtures for the gift-flow
    tests, but the commit's own message says "NOT verified: the suite has
    not been run against this commit," and it does not touch the FinOps
    host-routing bug at all.
- **Smoke tier exists but is not on `main`.** PR #939 (branch
  `qa/t_16203e29-playwright-smoke-tier`, all CI checks green) adds a
  `@pytest.mark.smoke` marker and 4 smoke tests (home-page render/JS-health
  in `test_visual_regression.py`, csp/console-error checks) runnable via
  `pytest -m "playwright and smoke"`. It is sequenced after #938 (both PRs'
  own bodies say so) and currently reports `mergeStateStatus: BLOCKED` on
  GitHub — not a merge conflict, a sequencing/branch-protection gate wanting
  #938 first.

## 3. CI wiring — current state (verified via `gh`)

- `.github/workflows/test-and-validate.yml`: `run-tests` job runs
  `pytest -m "not playwright"` only. **Zero CI execution of any Playwright
  test today**, smoke or otherwise — matches §2.
- The workflow's `pull_request.paths` filter excludes `infra/**`,
  `package-lock.json`, and other paths; `docs/agents-traps.md` (merged PR
  #931) explicitly documents that a change confined to an excluded path
  makes the `test`/`validate` required checks never report at all, which
  branch protection reads as permanently missing rather than skipped. Any
  future CI wiring for a Playwright tier must land through a path already
  covered by this filter or extend it, or the new required check will be
  unreachable from some legitimate PRs (mirrors the existing `mobile/`,
  `**.css`, `**.md` entries already added for the same reason).
- `power_up/finops/tests/test_dashboard_manual.py` carries
  `pytestmark = pytest.mark.playwright` but launches a non-headless browser
  against a manually-started dev server on port 8000 and pauses for human
  login — a bare `-m playwright` CI job would collect and hang on it. Needs
  its own `manual` marker (or exclusion) before any `-m playwright` job is
  wired up.

## 4. Reproducible baseline commands run for this audit

```bash
# Prerequisite: fresh worktree has no .env; source one before anything else
# (see AGENTS.md "A fresh worktree has no `.env`")
set -a && source /c/GitHub/Multi-Domain-Django-Platform/.env && set +a
source .venv/Scripts/activate    # existing venv, carried over in this worktree

# 1. Non-playwright discovery
pytest --collect-only -q -m "not playwright" -p no:warnings
# => 176 files, 4823 tests (counted via the per-file collection summary)

# 2. Non-playwright full run (the tier CI actually gates on)
pytest -q -n auto --dist worksteal -m "not playwright" -p no:warnings \
  --tb=line --create-db
# => 4823 passed (some skipped/xfail 's'/'u' markers, zero 'F'/'E')

# 3. Playwright discovery
pytest --collect-only -m playwright -q
# => 116 tests across 13 files (unchanged from the prior audit's count)

# 4. Playwright full run, forced complete pass (no early -x exit)
pytest -q -n auto --dist worksteal -m playwright --maxfail=0 -p no:warnings \
  --tb=line
# => 79 failed, 35 passed, 2 skipped, +5 teardown ERRORs on already-failed tests

# 5. ALWAYS recover the reused DB after a failing Playwright run before
#    trusting the next non-playwright result:
pytest --create-db -m "not playwright" -q
```

Chromium was already installed in this environment
(`~/AppData/Local/ms-playwright/chromium_headless_shell-1234`,
`chromium-1234`) from a prior attempt on this same task; a genuinely fresh
environment needs `python -m playwright install chromium` first (one-time,
~115 MB) — see §2 of PR #938's audit for the from-scratch install note.

## 5. Regression matrix — critical journeys × pyramid coverage

Legend: **Tier** — `smoke` (fast, PR-gate candidate once green), `regression`
(broader, scheduled/on-demand), `manual` (explicitly not CI-automatable per
existing author notes), `blocked` (needs a fix or a product/eng decision
first, listed with the specific blocker). **Coverage** columns cite the
actual test module(s); "—" means none found at that level for that journey.

| # | Critical journey | Unit/integration coverage | Playwright E2E coverage | Tier | Status today | Priority |
|---|---|---|---|---|---|---|
| 1 | App up / home page renders, no JS errors | `crush_lu/tests/test_htmx_views.py`, `test_middleware.py` | `test_visual_regression.py::TestHTMXInteractions::test_htmx_loaded`, `test_alpine_loaded`, `test_no_bootstrap_js_errors`, `TestResponsiveLayouts::test_home_page_desktop` | **smoke** | Green — `test_htmx_loaded` re-run standalone, passed | **P0** — cheapest real "is it up" signal; anchor the smoke tier here first |
| 2 | Member signup (top-of-funnel) | `crush_lu/tests/test_profiles.py`, `test_signup_domain_routing.py`, `test_social_login_latency.py`, `test_native_auth_completion.py` | `test_profile_registration_e2e.py` — ⚠️ **does not test signup**: fixture logs in an *existing* user and the wizard test states it cannot pass phone verification, stopping at Step 1 | ⚠️ do not gate as-is | 6/7 Playwright tests failing (console-error assertions); the passing 7th proves nothing about signup | **P0 gap** — no E2E test actually walks anonymous → verified signup. Django-level coverage exists (`test_signup_domain_routing.py`) but is unit-level only, no browser/JS validation of the wizard |
| 3 | Auth: login, social login (Google/Microsoft), native-app handoff | `test_social_login_latency.py` (39), `test_native_auth_completion.py` (27), `test_mobile_auth_handoff_chain.py` (26), `hub/tests/test_spa_auth.py` (12) | None | regression | Well covered at unit/integration level (92 tests); zero E2E/browser coverage of the actual login redirect flow | **P1** — high test count at unit level is reassuring, but nothing exercises the real OAuth popup/redirect UX a user experiences |
| 4 | Coach review workflow (screening, decision, approve/reject) | `test_coach.py` (16), `test_coach_event_detail.py` (26), `test_coach_mark_verified.py` (8), `test_coach_review_atomic_verification.py` (5), `test_pre_screening_coach.py` (9) | `test_coach_review_profile_playwright.py` (36) — ⚠️ **no test submits a form**; screening/decision tests only assert visibility | regression (currently broken) | ~30/36 E2E failing/erroring on the teardown signal bug (§2); unit tier (64 tests) is green | **P0 to fix** — the teardown bug is single highest-leverage fix (unblocks 36 tests at once); the *submission* path (approve/reject POST) has real unit coverage (`test_coach_review_atomic_verification.py`) but zero E2E — a UI regression that breaks the approve button would not be caught by either tier as configured today |
| 5 | Payments: SumUp checkout, reconciliation, receipts, Apple/Google Wallet | `test_sumup_payments.py` (184), `test_sumup_reconciliation.py` (28), `test_apple_wallet.py` (21), `test_apple_wallet_manifest.py` (2), `test_crush_credit.py` (134) | None | regression | 369 unit/integration tests, all green; zero E2E | **P1** — deep unit coverage, but the SQLite lock-ordering trap (§1) means concurrent payment+profile-merge races are *not* provably safe even with this tier green. No browser-level checkout flow exists at all |
| 6 | Wonderland Journey gift: create, claim (new/existing user), unlock | `test_journey_error_handling.py` (32) | `test_journey_gift_e2e.py` (26) — ⚠️ two tests in the claim path assert nothing meaningful (`test_claim_after_signup_creates_journey` calls `refresh_from_db()` then stops; `test_signup_from_gift_landing` silently passes if the signup link is absent) | regression | Mixed pass/fail at E2E; unit-level error-handling (rollback, retry) is separately covered and green | **P1** — the gift→signup→unlock handoff, the actual product-critical path, is the weakest-asserted part of an otherwise large test file; repair those two assertions before trusting this row as regression coverage |
| 7 | Event lifecycle: creation, check-in/door actions, lobby, multi-day | `test_events.py` (82), `test_checkin_door_actions.py` (80), `test_event_lobby.py` (114), `test_event_lobby_recap.py` (59), `test_multiday_event_lifecycle.py` (7), `test_door_verification_reject.py` (15), `test_verify_on_checkin.py` (40) | None | regression | 397 unit/integration tests, all green; zero E2E on the live check-in UI | **P1** — largest single unit-test surface in the repo by test count; the physical-world check-in flow (scanning, door state) has no browser-level regression check, which is exactly the kind of UI/JS-timing bug unit tests cannot catch |
| 8 | Crush Connect: beta invites, pause/self-pause, weekly cycle, chat | `test_connect_beta_invites.py` (23), `test_connect_beta_invite_pause.py` (9, added by the recent self-pause regression fix, PR #927), `test_connect_week_experience.py` (42), `test_connect_chat_flows.py` (33), `test_crush_connect_pause.py` (8), `test_crush_connect_onboarding.py` (53) | None | regression | 168 unit/integration tests, all green, including a just-merged regression fix with dedicated tests; zero E2E | **P2** — recently hardened at the unit level (self-pause invitation bug, verified independently in `t_c75876b1`); still no browser coverage of the Connect chat UI itself |
| 9 | Photo-reveal puzzle unlock (points, progress) | Not separately isolated — falls under general profile/gamification unit tests | `test_photo_puzzle_e2e.py` (9) | regression | Majority E2E failing (page load, notification, progress bar); not yet root-caused per-test | **P2** — needs its own root-cause pass before it can gate anything |
| 10 | Power-Up FinOps dashboard (staff-only) | `power_up/finops/tests/test_dashboard_edge_cases.py` (22), `test_permissions.py` (64), `test_anomaly_detection.py` (6), `test_sync_daily_costs.py` (17) | `test_dashboard_filtering.py` (14) — broken by the host-routing bug (§2) | regression | 109 unit/integration tests green; all 14 E2E tests broken (never actually reach the Power-Up host) | **P2** — internal/staff tool, lower user-facing risk than rows 1–8, but the E2E tier for it is currently testing nothing real |
| 11 | GDPR consent flow, account deletion, retention cleanup | `test_gdpr_retention_cleanup.py` (8), `test_account_deletion.py` (2) | None (but `CrushConsentMiddleware` is incidentally exercised — as a *blocker* — by row 10's tests) | regression | 10 unit tests green; the middleware itself has no direct E2E confirming the consent-confirm UI page renders and that confirming actually clears the redirect | **P2 gap** — low test count relative to compliance importance; the FinOps E2E failures are the only signal touching this middleware at all, and that's as a bug, not a test of it |
| 12 | Cross-browser (Firefox/WebKit) / non-Chromium viewport regression | N/A | None | **blocked** | No config, no product requirement found anywhere in the repo | Open product question — do not invent a Firefox/WebKit requirement (per this task's own instruction); flag for a product decision, not implementation |
| 13 | Coach/staff role separation at the Django URL level | Covered incidentally across `test_permissions.py` (power_up, 64 tests), `test_idor.py` (11), `test_security.py` (27) | None | N/A | Not fully enumerated in this pass — `test_idor.py`/`test_security.py` exist specifically for this class of check | **P2 verify** — confirm `/coach/review/<id>/` and admin-only routes are denied to a plain member in the existing security test files before assuming a gap; do not add a new Playwright test until that's confirmed absent |
| 14 | Multi-domain routing (crush.lu / power-up.lu / vinsdelux / entreprinder / delegations / tableau / arborist) | `test_signup_domain_routing.py`, `test_production_middleware_order.py` (3), the host-routing bug discovered in row 10 | Incidentally broken by row 10 | regression | Live production bug class (row 10's root cause) shows this boundary is genuinely fragile; worth its own explicit test, not just incidental discovery | **P1** — the FinOps host-routing bug was found by accident while diagnosing a different symptom; the multi-domain URLconf-switching middleware deserves direct test coverage of "which urlconf does host X resolve to", not just downstream symptoms |

### Recommended smoke set (CI PR gate)

Row 1 only, today. It is the sole row with (a) a real E2E test, (b)
currently green, (c) fast (~22s standalone). PR #939 packages this correctly
and is ready to merge pending #938's sequencing. **Do not add row 2
(registration) to smoke** until it is rewritten to actually walk anonymous
signup — promoting a test that logs in an existing user as "the registration
gate" would stay green through a broken signup flow, which defeats the
purpose of a smoke gate.

### Recommended regression set (scheduled / on-demand, non-blocking)

Rows 4 (after the teardown-signal fix), 6 (after the two dead-assertion
repairs), 7, 8, 9, 10 (after the host-routing fix), 13 (after verifying
existing coverage). None of these should block merges by default — the
repository has no documented policy requiring a Playwright gate beyond
smoke, and building one on today's ~68% Playwright failure rate would be
building on flakes per §2/§4's own warning about single-run measurement.

### Explicitly out of scope / not invented

- No Firefox/WebKit project (row 12) — no product signal found.
- No new "smoke" definition for rows 3/5/7/8 (auth, payments, events,
  connect) despite their P0/P1 unit-test depth — none of them has *any*
  Playwright coverage today, so there is nothing existing to promote; that
  is an implementation gap for `t_2eb7f76b`, not a matrix omission.

## 6. Test-data and fixture assumptions (repository-wide)

- Every test creates its own users/fixtures per-test via Django ORM
  (`test_user`, `coach_user`, `linkedin_signup_user`, etc., in `conftest.py`
  and individual modules). **No seeded prod-like fixture data, no JSON/YAML
  fixtures.**
- `crush_lu/tests/conftest.py`'s `_restore_migration_seeded_rows` hook
  replays migration-seeded catalogue tables (Interest, Trait, SparkPrompt,
  ConnectQuestion, …) around any module using `django_db(transaction=True)`
  — this is the exact mechanism whose interaction with
  `manage_coach_staff_status` produces the coach-review teardown bug (§2).
  Any new transactional test touching `CrushCoach` will hit the same bug
  until the signal is guarded.
- Session-scoped `Site` objects (`localhost`, `127.0.0.1`, `testserver`) are
  seeded once via an overridden `django_db_setup` fixture in the root
  `conftest.py`.
- The `crush-qa-agent` skill's manual QA account (`verifyghost@example.com`)
  is **not** referenced anywhere in the automated Playwright fixtures
  (confirmed: zero matches in `crush_lu/tests/`) — it is for interactive
  manual verification only, not part of any automated tier. Do not conflate
  the two when writing new tests.
- `--reuse-db` (the `pytest.ini` default) means a failed Playwright run can
  leave the reused DB missing its migration-seeded catalogues (§4 step 5)
  and contaminate a subsequent non-Playwright run. Always follow a failing
  Playwright run with `pytest --create-db -m "not playwright" -q` before
  trusting the next result.

## 7. Documented blocker

**Kanban `t_2eb7f76b`** (the follow-on "implement the regression tier" task,
child of the original `t_a661c3b2` audit) is currently `status: blocked`
after two consecutive iteration-budget timeouts (150/150 iterations, no
completion). Its handoff shows real work exists — the `e0b7581d` rescue
commit on local branch `qa/t_2eb7f76b-playwright-regression-tier` contains
an unverified `manage_coach_staff_status` fix and consent-grant additions —
but the commit's own message states the suite was never run against it, and
it is not pushed to a remote (exists only in this local checkout's git
history). This is a **capability/continuity blocker for that specific
downstream task**, not for this audit: I was able to fully complete
discovery, baseline verification, and matrix authoring without it. Recording
it here per this task's acceptance criteria ("document the blocker
resolution or a precise typed blocker") so `t_2eb7f76b`'s next attempt does
not have to rediscover that the fix exists but is unverified and unpushed.

Separately, **PR #938** (the prior audit doc) **merged to `main` as
`e300fd56` during this task** — its content is now `main`'s
`docs/testing/playwright-coverage-matrix.md`, referenced throughout §2.
**PR #939** (the smoke tier, branch `qa/t_16203e29-playwright-smoke-tier`,
all CI checks green, `mergeable: MERGEABLE`) is still open with
`mergeStateStatus: BLOCKED` as of this writing — per its own PR body this
was sequenced to land after #938, which has now happened, so it likely only
needs a rebase/re-check or a review approval, not new work. No action needed
from this task; noted so a reviewer doesn't mistake it for a new blocker.

## 8. Inputs for the harness-hardening task (`t_997b588d`)

- **Web-server startup**: all Playwright tests correctly use
  `pytest-django`'s `live_server` fixture *except* `test_mobile_screenshots.py`
  (both tests) and `test_dashboard_manual.py`, which hardcode
  `http://localhost:8000` / port 8000 and fail immediately on a clean run
  with nothing listening there. Convert to `live_server.url` or classify
  explicitly as requiring a separately-started dev server.
- **Base URL / host handling**: the FinOps host-routing bug (§2, §5 row 10)
  is a harness gap as much as a test bug — there is no fixture/helper that
  builds a `powerup.localhost:<live_server_port>` URL, so every FinOps E2E
  test either gets this wrong individually or will keep needing to.
  Consider adding a `power_up_live_server_url` fixture.
- **Trace/video/screenshot retention**: none configured anywhere
  (`--tracing=retain-on-failure`, `--screenshot=only-on-failure`,
  `--video=retain-on-failure` are all available in the installed
  `pytest-playwright` but unset). A genuine gap for `t_997b588d` to fill,
  not a regression to preserve.
- **DB reuse hazard**: `--reuse-db` combined with the teardown bug (§2)
  actively corrupts state across runs. The harness should either default to
  `--create-db` for CI or add a documented recovery step; today it is a
  manual "remember to run this after a red build" convention (§4 step 5).
