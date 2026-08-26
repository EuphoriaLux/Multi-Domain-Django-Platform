# Playwright Coverage Audit & Regression Matrix

Status: audit snapshot as of 2026-08-26, commit `965b0c41` (`origin/main`).
Owner: QA (crush-qa). Source task: Kanban `t_a661c3b2`.

This document inventories the Playwright coverage that exists today in this
repository, records what actually runs and passes right now, and defines a
prioritized regression matrix to guide the follow-on implementation tasks
(`t_16203e29` smoke tier, `t_2eb7f76b` regression tier, `t_bee62d16` CI wiring,
`t_f409fca5` validation). Every number below was produced by running the
commands shown, not estimated.

## 1. How Playwright is wired into this repo today

- Framework: `pytest-playwright==0.9.0` + `playwright==1.62.0` on top of
  `pytest-django`. There is **no standalone `playwright.config.*`** and **no
  JS/TS Playwright test runner** — all E2E tests are Python pytest modules
  under `crush_lu/tests/` and `power_up/finops/tests/`, gated by the custom
  `@pytest.mark.playwright` marker declared in `pytest.ini`.
- `pytest.ini` `addopts` defaults to `-m "not playwright"` — **Playwright
  tests never run unless `-m playwright` is passed explicitly.** This is also
  why `.github/workflows/test-and-validate.yml` never executes them (its one
  test step is `pytest -m "not playwright" --tb=short -q -x -n auto`).
- Browser matrix: only Chromium is configured. `conftest.py`'s
  `browser_context_args` fixture fixes viewport to 1280x720 and
  `ignore_https_errors: True`; there is no `--browser firefox/webkit` usage
  anywhere in CI config, scripts, or docs. Individual test files call
  `page.set_viewport_size(...)` ad hoc for mobile checks (390x844 iPhone 12,
  375x667, 768x1024 tablet, 1440x900 desktop) — this is the only viewport
  coverage that exists; there is no configured "projects" list.
- Browsers are **not installed by default** in this environment; `playwright
  install chromium` had to be run before any Playwright test could execute
  (see §4). CI does not install Playwright browsers at all today, consistent
  with Playwright tests never running there.
- Required env vars for the app itself (from `.env.example`): `SECRET_KEY` is
  the only one pytest's `conftest.py` `pytest_configure()` doesn't already
  default; it sets safe fallbacks for `AZURE_ACCOUNT_NAME/KEY`,
  `GRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET`, `VAPID_*`,
  `DJANGO_TASKS_BACKEND=immediate`, forces `EMAIL_BACKEND=locmem`, and forces
  `PASSWORD_HASHERS=[MD5PasswordHasher]` for speed. No external services
  (Postgres, Redis, Azure Storage, SumUp, Graph/Email) are required to run
  the suite — `pytest-django`'s `live_server` + SQLite + the mocks above are
  sufficient.
- Deterministic test data: every Playwright module creates its own users via
  Django fixtures at test time (no fixture JSON/YAML, no seeded prod-like
  data). `crush_lu/tests/conftest.py` has a `_restore_migration_seeded_rows`
  hook that snapshots and replays the `crush_lu` migration-seeded catalogues
  (Interest, Trait, SparkPrompt, ConnectQuestion, …) around any module that
  flushes the DB with `django_db(transaction=True)`, which is required
  because `TransactionTestCase`-style teardown truncates those tables.
  Session-scoped `Site` objects (`localhost`, `127.0.0.1`, `testserver`) are
  seeded once via an overridden `django_db_setup` fixture in the root
  `conftest.py`.
- The `crush-qa-agent` skill documents a persistent manual QA account
  (`verifyghost@example.com` / `verify-pass-123`) for exploratory browser
  verification against a running dev server — **this account does not exist
  in the automated Playwright fixtures** (searched; zero references in
  `crush_lu/tests/`). Automated tests instead create throwaway users
  per-test/per-fixture (`test_user`, `coach_user`, `linkedin_signup_user`,
  etc. in `conftest.py` and the individual test modules). Do not conflate the
  two: the manual account is for interactive/manual QA sessions only.

## 2. Inventory: what exists today

Discovery command used (see §4 for full transcript):
```
pytest --collect-only -m playwright -q
```

| File | Tests | What it exercises |
|---|---:|---|
| `crush_lu/tests/test_coach_review_profile_playwright.py` | 36 | Coach review workflow: tab navigation (Profile/Screening/Decision), account-metadata summary card, screening form, decision submission, responsive layout. Uses a coach login flow (`authenticated_coach_page` fixture). |
| `crush_lu/tests/test_journey_gift_e2e.py` | 26 | Wonderland Journey gift system: create gift (auth required), gift landing page (unauthenticated + expired/claimed states), signup-from-gift and existing-user claim flows, post-claim journey map access, double-claim prevention. |
| `crush_lu/tests/test_photo_puzzle_e2e.py` | 9 | Photo-reveal puzzle: page load, 16-piece grid, click loading state (prevents double-click), points deduction, progress bar, unlock notification, already-unlocked piece guard. |
| `crush_lu/tests/test_profile_registration_e2e.py` | 7 | 4-step registration wizard: Enter-key-does-not-submit guard, console-error/CSP-violation capture across the full wizard, Alpine.js component init. |
| `crush_lu/tests/test_visual_regression.py` | 13 | Responsive layout smoke (desktop/tablet/mobile viewports on home + events), Alpine.js mobile nav + alert dismissal, HTMX/Alpine "is it loaded" checks, no-Bootstrap-JS-errors check, Tailwind forms styling. |
| `crush_lu/tests/test_mobile_ui.py` | 3 | Mobile-viewport (390x844) phone-number field layout and language switcher in the mobile nav menu. |
| `crush_lu/tests/test_language_switcher_final.py` | 1 | Mobile language switcher, consolidated single-test version. |
| `crush_lu/tests/test_membership_screenshots.py` | 1 | Membership page light/dark mode visual screenshot check. |
| `crush_lu/tests/test_mobile_screenshots.py` | 2 | Ad hoc mobile screenshots (phone input, language switcher on home). |
| `crush_lu/tests/test_phone_detail.py` | 1 (skipped) | Detailed phone-input screenshot — `@pytest.mark.skip`, selector stale (`#div_id_phone` → `#phone_number`). |
| `crush_lu/tests/test_phone_input_manual.py` | 2 (skipped) | Same stale-selector skip as above. |
| `power_up/finops/tests/test_dashboard_filtering.py` | 14 | Power-Up FinOps dashboard: charge-type/period filters, filter persistence across navigation, clear-filters, accessibility labels, empty-data state, perf with max filters. |
| `power_up/finops/tests/test_dashboard_manual.py` | 1 | Explicitly documented as manual-only ("Requires a running dev server + manual login — never meant for CI"); marked `playwright` but not CI-suitable. |
| **Total** | **116** | across 13 files, all Chromium-only, single 1280x720 default viewport plus ad hoc mobile/tablet overrides. |

Non-Playwright (Django unit/integration) suite for context: **~3,000+ tests**
across ~190 files under `crush_lu/tests/`, `hub/tests/`, `power_up/finops/`,
`power_up/atmos/`, `azure-functions/finops-daily-sync/tests/` (see
`pytest.ini` `testpaths`). This is the tier CI actually runs today
(`pytest -m "not playwright"`), and it is what "fast/CI" currently means in
this repo. Playwright is the **only** browser-level tier and currently has
**zero CI execution**.

## 3. Known coverage gaps (observed, not inferred)

- **No CI gate at all for Playwright.** `README.md` §Testing and
  `.github/workflows/test-and-validate.yml` both confirm CI's only test step
  excludes `-m playwright`. There is no smoke tier, no scheduled/nightly
  regression run, nothing. This is the primary gap `t_bee62d16` needs to
  close.
- **~78 of 116 Playwright tests fail today on a clean checkout** (see §4 for
  the reproducible run). This is a large *pre-existing* failure surface, not
  something introduced by this audit. Root causes observed directly:
  - A teardown-time regression in `crush_lu/tests/conftest.py`'s
    `_restore_migration_seeded_rows` fixture: replaying the migration-seeded
    catalogue snapshot re-triggers `crush_lu/signals.py:manage_coach_staff_status`
    (a `post_save` receiver on `CrushCoach`) with `raw=True`, and that
    receiver does `instance.user` on a row whose FK target hasn't been
    replayed yet in this deferred-constraint transaction, raising
    `User.DoesNotExist` / `KeyError: 'user'` at teardown. This cascades into
    `ERROR at teardown of ...` on **every** test in
    `test_coach_review_profile_playwright.py` that touches `CrushCoach`
    (module-level `django_db(transaction=True)`), independent of whether the
    test itself passed.
  - Separately (not just teardown), many of the actual test bodies in that
    same file fail with stale selectors —
    `locator("[x-show=\"isProfileTab\"]")` etc. time out — indicating the
    coach-review template markup has since diverged from what the tests
    expect (matches the historical pattern already documented in
    `crush_lu/tests/PLAYWRIGHT_TEST_FIXES.md`, which itself only covers an
    earlier round of the same class of drift).
  - `power_up/finops/tests/test_dashboard_filtering.py`: consent-gate
    interception — tests expect the FinOps dashboard title but land on
    "Confirm Your Consent" instead, meaning either the test user fixture
    doesn't pre-confirm GDPR consent or the consent middleware gate was
    added/changed after these tests were written.
  - `test_journey_gift_e2e.py`, `test_photo_puzzle_e2e.py`,
    `test_profile_registration_e2e.py`: assorted 404/selector/timeout
    failures consistent with template or route drift since these tests were
    authored; not yet root-caused per-test (out of scope for an audit-only
    task — logged here as reproducible evidence for the implementation
    tasks).
  - 2 tests are `@pytest.mark.skip`'d with a documented, still-valid reason
    (`test_phone_detail.py`, `test_phone_input_manual.py` — stale
    `#div_id_phone` → `#phone_number` selector).
- **No retry/flake-quarantine mechanism** (`pytest-rerunfailures` is not
  installed; no `--reruns` anywhere) — every failure above is a hard fail,
  not a flake, which is good for signal but means today's ~67% Playwright
  failure rate would block any gate that took "all Playwright tests" as
  its bar. The smoke tier (`t_16203e29`) must hand-pick a small, currently
  passing/repairable subset rather than assume the existing suite is
  gate-ready.
- **No cross-browser or explicit multi-viewport project matrix.** Firefox
  and WebKit are supported by the installed `playwright` package but never
  exercised; nothing in config declares them as targets. Do not invent a
  Firefox/WebKit requirement — there is no product signal (browser support
  policy, analytics, ticket) in this repo indicating it's needed. Flagging
  as an open gap for a product decision, not assuming it into the matrix.
  Note also that `playwright install` did not actually have any browser
  binaries cached in this environment prior to this audit — that is itself
  an environment-readiness gap for whoever runs the next tier (needs
  `python -m playwright install chromium` at minimum, one-time, ~115 MB
  download).
  Note: `@playwright/mcp` in `package.json` devDependencies is an editor
  MCP server for driving a browser interactively, unrelated to the
  `pytest-playwright` test runner — do not confuse the two when working the
  follow-on tasks.
- **No auth/role variant systematically covered in Playwright**, even though
  three real Django-level roles exist (anonymous, member, `CrushCoach`
  staff, superuser/admin — confirmed via `crush_lu/decorators.py` and
  `is_staff`/`is_superuser` checks across `admin_views.py`, `views_coach.py`,
  etc.). Only the coach-review file exercises the coach role explicitly;
  no Playwright test exercises the superuser/admin surface at all.
- **No API/integration-boundary Playwright checks** beyond what's incidental
  to a page load (e.g. lobby "signal"/"confirm" AJAX endpoints under
  `views_event_lobby.py`, phone-verification API, profile step-save APIs are
  untouched by Playwright — they may have Django-test-level coverage in the
  non-playwright tier, which is out of scope to verify here but should be
  cross-checked by whoever implements `t_2eb7f76b`).

## 4. Discovery & validation commands run (reproducible)

Environment: `C:/GitHub/Multi-Domain-Django-Platform/.venv` (Python 3.14.6,
pytest 9.1.1, playwright 1.62.0). This worktree
(`C:/GitHub/worktrees/t_a661c3b2`, branch
`qa/t_a661c3b2-playwright-coverage-matrix`) does not carry its own venv;
audit commands were run against the existing `.venv` at the primary checkout
since the codebase (branch `main` @ `965b0c41`) is identical at audit time —
implementers should create/reuse a venv inside their own worktree per
repo convention (`README.md` §Setup).

```bash
# 1. Activate the existing project venv
source .venv/Scripts/activate

# 2. One-time browser install (was NOT present in this environment)
python -m playwright install chromium

# 3. Discovery — confirms marker wiring and per-file counts (§2 table)
pytest --collect-only -m playwright -q
# => 116 tests collected across 13 files (see table above)

# 4. Non-playwright collection, for contrast (CI's current tier)
pytest --collect-only -q
# => testpaths across crush_lu/tests, hub/tests, power_up/finops/tests,
#    power_up/atmos/tests, azure-functions/finops-daily-sync/tests

# 5. Single-test sanity check (passed cleanly)
pytest crush_lu/tests/test_visual_regression.py::TestHTMXInteractions::test_htmx_loaded -m playwright -v
# => 1 passed in ~22s

# 6. Full Playwright tier run (reproduces the failure surface in §3)
pytest -q -n auto --dist worksteal -m playwright
# => 36 passed/skipped, ~78 failed+errored, out of 116 collected
#    (raw progress line: 36 '.'/'s' vs 78 'F'/4 'E' markers in this run)
```

Full failure list and tracebacks were captured during this audit and are
available in the task's run history; representative root causes are
summarized in §3 rather than repeated verbatim here to keep this artifact
focused. Anyone repairing tests should re-run the exact commands above on
their own worktree rather than trust this snapshot as gospel — template and
selector drift is clearly ongoing.

## 5. Regression matrix

Legend: **Tier** — `smoke` (CI-fast, PR gate candidate, must be green and
fast), `regression` (broader, non-blocking-for-merge / scheduled), `manual`
(explicitly not automatable in CI per existing test author notes),
`blocked` (cannot be made green without a product/eng decision or a real
bug fix first — listed with the specific blocker).

| # | Flow | Current test(s) | Tier | Status today | Notes for implementers |
|---|---|---|---|---|---|
| 1 | Home page loads, nav/main render, no JS errors | `test_visual_regression.py::TestResponsiveLayouts::test_home_page_desktop`, `test_no_bootstrap_js_errors`, `test_htmx_loaded`, `test_alpine_loaded` | **smoke** | Verified passing (test_htmx_loaded run individually) | Cheapest, most representative "is the app up" checks. Good CI-smoke anchor. |
| 2 | Responsive layout across viewports (mobile/tablet/desktop) | `test_visual_regression.py::TestResponsiveLayouts::*`, `test_mobile_ui.py` | regression | Untested in this pass; visual/screenshot-only, no hard assertions beyond element presence | Screenshot-based, not true regression (no baseline diffing configured) — currently just a smoke-render check under multiple viewports. Fine to keep as regression tier, not a P0 gate. |
| 3 | Member signup / 4-step registration wizard, no console errors | `test_profile_registration_e2e.py::TestProfileRegistrationE2E::*` | **smoke** (once fixed) | 6 of 7 failing today (console-error capture assertions) | Highest-value member-facing flow with zero passing smoke coverage today — prioritize fixing over the coach-review file for the smoke tier, since signup is the true top-of-funnel critical path. |
| 4 | Coach review workflow (tab nav, screening, decision) | `test_coach_review_profile_playwright.py` (36 tests) | regression (currently) | ~30 of 36 failing/erroring — both a teardown-fixture bug (`manage_coach_staff_status` signal + migration-replay interaction) and stale selectors | Do not promote to smoke until the teardown bug in `_restore_migration_seeded_rows`/`manage_coach_staff_status` is fixed — it currently makes even passing test bodies report as teardown errors. This is the single highest-leverage bug to fix first: it is shared infrastructure affecting the whole file. |
| 5 | Wonderland Journey gift creation, claim, journey access | `test_journey_gift_e2e.py` (26 tests) | regression | Mixed pass/fail; several `TestGiftCreationFlow` tests fail (form-render/QR/link assertions) | Represents a real integration boundary (gift → signup → journey unlock) — good `t_2eb7f76b` candidate once individual failures are triaged; do not treat as smoke given current fail rate. |
| 6 | Photo-reveal puzzle unlock flow (points, progress, notification) | `test_photo_puzzle_e2e.py` (9 tests) | regression | Majority failing (page load, notification, progress bar) | Needs root-cause pass before it can gate anything; keep as regression/non-blocking until fixed. |
| 7 | FinOps dashboard filtering (Power-Up, staff-only) | `power_up/finops/tests/test_dashboard_filtering.py` (14 tests) | regression | All observed failures caused by a GDPR consent-confirmation interstitial the test fixture doesn't pre-confirm | Fixable at the fixture level (confirm consent for the `finops_admin`-style test user before navigating) rather than a product bug — flag to `t_2eb7f76b` implementer as a likely one-line fixture fix, not a deep investigation. |
| 8 | FinOps dashboard manual smoke | `test_dashboard_manual.py` | **manual** | Author-documented as "never meant for CI" | Leave as-is; do not force into an automated tier — respects the existing author's explicit intent. |
| 9 | Mobile language switcher | `test_mobile_ui.py::TestMobileLanguageSwitcher`, `test_language_switcher_final.py` | regression | Not individually isolated in this pass | Duplicate coverage across two files for the same flow — consolidation opportunity for `t_2eb7f76b`, not a gap. |
| 10 | Membership page light/dark mode | `test_membership_screenshots.py` | regression | Not isolated in this pass | Visual-only; low priority. |
| 11 | Legacy phone-input screenshots | `test_phone_detail.py`, `test_phone_input_manual.py` | **blocked** | Explicitly `@pytest.mark.skip`'d, reason documented in-file (stale `#div_id_phone` selector) | Genuine, already-diagnosed blocker — fix is a one-line selector update (`#phone_number`) whenever someone picks this up; not re-diagnosing here per the skip's own accurate note. |
| 12 | Negative / auth-required cases (gift create requires auth, invalid gift code → 404, double-claim prevented) | `test_journey_gift_e2e.py::TestGiftCreationFlow::test_gift_create_page_requires_auth`, `TestGiftEdgeCases::*` | regression | Not isolated in this pass; part of the 26-test file's mixed results | Genuine negative/recovery-path coverage already exists — verify pass/fail per-test when triaging file #5 above rather than re-writing from scratch. |
| 13 | Coach/staff role separation at the Django level (non-Playwright) | N/A — out of Playwright's current scope | N/A | Not evaluated (non-playwright tier untouched by this audit) | Flagging as a matrix gap only: no Playwright test verifies that a plain member is denied `/coach/review/<id>/` or `/dev/connect-card/<id>/`; if such authorization is already covered by the Django unit-test tier that's sufficient and no new Playwright test is needed — confirm before adding one (`t_2eb7f76b` scope, don't assume). |
| 14 | Cross-browser (Firefox/WebKit) and non-default viewport regression | None | **blocked** | No config, no product requirement found | Do not implement without an explicit browser-support decision — logging as an open question rather than inventing a requirement, per this task's instructions. |

### Recommended CI-fast (smoke) set once repaired

Rows 1 and 3 above are the two flows worth gating PRs on once green:
home-page render/JS-health (already green, ~22s single test) and the
registration wizard smoke path (needs the console-error assertions fixed
first). That keeps the smoke tier small, fast, and deterministic — everything
else in this table is regression-tier at best until its specific failure is
triaged, per the acceptance criteria that quarantine is not a substitute for
fixing newly introduced flaky/broken behavior.

### Recommended regression (broader) set

Rows 2, 4 (post teardown-bug-fix), 5, 6, 7 (post consent-fixture-fix), 9, 10,
12, 13 — run on a schedule or on-demand once the underlying fixture/selector
bugs are fixed, not gating merges by default (repository has no documented
policy requiring it, consistent with "do not invent an expensive cadence
when repository policy is unclear").

## 6. Actionable inputs for the implementation tasks

- **`t_16203e29` (smoke tier)**: start from row 1 (already green) and row 3
  (registration wizard — fix the 6 failing console-error assertions; likely
  a `page.on("console", ...)` timing/URL issue given the pattern in
  `test_visual_regression.py::test_no_bootstrap_js_errors`, which does work).
  Add a dedicated marker/tag (e.g. `@pytest.mark.smoke` alongside the
  existing `@pytest.mark.playwright`) so `pytest -m "playwright and smoke"`
  is the fast-CI command, keeping the existing `-m playwright` semantics
  intact for the broader tier.
- **`t_2eb7f76b` (regression tier)**: prioritize fixing, in order of
  leverage: (a) the `manage_coach_staff_status`/migration-replay teardown
  bug (unblocks all 36 coach-review tests at once), (b) the FinOps
  consent-fixture gap (likely unblocks all 14 dashboard-filtering tests with
  one fixture change), (c) triage the gift/puzzle files individually. Reuse
  the existing per-file fixtures; do not introduce new test-data
  conventions.
- **`t_bee62d16` (CI wiring)**: add the smoke marker as a required PR check
  (fast, ~image/browser install cached), and add the full `-m playwright`
  run as a scheduled or manual `workflow_dispatch` job — mirroring how
  `test-and-validate.yml` already separates required (`test`, `validate`)
  from `continue-on-error` jobs. Playwright HTML report / trace-on-failure
  is not currently configured (`pytest-playwright` supports
  `--tracing=retain-on-failure`, `--screenshot=only-on-failure`,
  `--video=retain-on-failure` as CLI flags) — none are set anywhere today,
  so this is a genuine gap to fill, not a regression to preserve.
- **`t_f409fca5` (validation)**: re-run the exact commands in §4 against
  the actual smoke/regression tier that gets implemented, and confirm the
  failure count only decreases (no new failures introduced by tier-splitting
  work itself).
