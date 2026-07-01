# Production slot-swap runbook

How to promote the `staging` slot to production for
`django-app-ajfffwjb5ie3s-app-service` (all 9 domains). Written for the
2026-07-03 swap; the procedure is reusable for every future swap.

## How releases work here (context)

- CI (`.github/workflows/deploy-azure-app-service-optimized.yml`) deploys
  `main` to the **staging slot only**. Production promotion is always a
  **manual slot swap** in the Azure Portal.
- `startup.sh` runs `manage.py migrate --no-input` on container start, so
  **pending migrations apply to the production database during swap warm-up**
  (while the old code is still serving). Migrations must therefore stay
  additive/backward-compatible — they have been so far.
- Slot-sticky settings (verified 2026-07-02 via `slotConfigNames`) pin the
  important config to each slot: `AZURE_POSTGRESQL_CONNECTIONSTRING`
  (staging uses its own DB `pythonapp_staging`), `STAGING_MODE`,
  `HYBRID_COACH_SYSTEM_ENABLED`, `PRE_SCREENING_ENABLED`,
  `CRUSH_CONNECT_LAUNCHED`, `ADMIN_API_KEY`, `ALLOWED_HOSTS_ENV`, analytics
  and WhatsApp/Wallet credentials. These **do not move** during a swap.
  Non-sticky settings exchange between slots — they are value-identical on
  both slots, so the exchange is a no-op.
- Custom domains never swap: `test.<domain>` always points at the staging
  slot, production domains always at production.

## Pre-swap checklist

- [ ] Confirm the staging slot runs the commit you intend to ship
      (Portal → Deployment Center on the slot, or the latest
      `deploy-azure-app-service-optimized` run in GitHub Actions).
- [ ] Smoke-test `test.crush.lu`: login, dashboard, one Crush Connect page.
- [ ] Pick a quiet hour. The plan is a single 4 GB P0v3 instance already at
      ~80% average memory; during the swap **both containers run
      concurrently**. Optional headroom: stop the three function apps
      (`crush-hybrid-maintenance`, `crush-contact-sync`, `finops-daily-sync`)
      for the swap window — timer jobs just skip a cycle. Restart them after.

## The swap

1. Portal → `django-app-ajfffwjb5ie3s-app-service` → **Deployment slots** →
   **Swap** (source: `staging`, target: `production`).
2. Expect warm-up to take a few minutes: the incoming container starts with
   production config and runs pending migrations against the prod DB
   (`WEBSITES_CONTAINER_START_TIME_LIMIT=600` covers this). Don't cancel a
   slow warm-up.
3. Ignore failed `crush-hybrid-maintenance` invocations (401s) during the
   window — while the slot warms with production's sticky `ADMIN_API_KEY`,
   the function's staging-keyed calls fail. Self-heals when the swap
   completes.
4. If using **Swap with preview**: during preview, `test.crush.lu` serves the
   NEW code against the PRODUCTION database with real email enabled. Keep the
   preview short and don't trigger user-facing emails from it.

## Post-swap verification

- [ ] `https://crush.lu/healthz/` returns 200; spot-check one or two other
      domains (`entreprinder.lu`, `portal.powerup.lu`).
- [ ] Login + dashboard smoke on `crush.lu`.
- [ ] Crush Connect is still **teaser-gated** for non-staff on production
      (deliberate: `CRUSH_CONNECT_LAUNCHED` is unset on prod, sticky). Staff
      accounts can reach the real Connect pages.
- [ ] `WEEKLY_KPI_RECIPIENTS` is still set on production (it is non-sticky
      but value-identical on both slots, so the swap exchange is harmless).
- [ ] Watch App Insights failures + plan memory for ~15 minutes.

## Post-swap config tasks — `crush-hybrid-maintenance` function app

The function currently drives the **staging slot** (`test.crush.lu` = staging
DB + console email), which is why the weekly KPI digest has never been
delivered. After the swap, fix its app settings (Portal → function app →
Environment variables, or via MCP/CLI):

- [ ] `DJANGO_WEEKLY_KPIS_URL` → `https://crush.lu/api/admin/weekly-kpis/`
- [ ] Add `DJANGO_ROTATE_CONNECT_QUESTIONS_URL` →
      `https://crush.lu/api/admin/rotate-connect-questions/`
      (the timer exists since PR #550 but silently skips without this var;
      harmless meanwhile — `active_week_questions()` lazily creates the week)
- [ ] `ADMIN_API_KEY` → replace with the **production** web app's value.
      The function currently holds the staging slot's key; calls to
      `crush.lu` would 401 without this.
- [ ] Optional: repoint `DJANGO_PRE_SCREENING_INVITES_URL` and
      `DJANGO_HYBRID_SLA_SWEEP_URL` to `https://crush.lu/...` too. They
      become clean no-ops there (`HYBRID_COACH_SYSTEM_ENABLED` and
      `PRE_SCREENING_ENABLED` are off in prod since the shift away from
      mandatory coach verification), and they light up automatically if a
      coach offering ever returns.
- [ ] Trigger one manual KPI run to get the first real digest immediately
      instead of waiting for Monday:
      `POST https://crush.lu/api/admin/weekly-kpis/` with
      `Authorization: Bearer <prod ADMIN_API_KEY>`.

Note on the first digest: it covers the last completed ISO week from
production data; daily-activity rollups only start accumulating organically
after the swap (migration `0172` backfills history), so some week-1 numbers
may look sparse. From the following Monday it is fully organic.

## Rollback

Swap again (production ↔ staging) — the old code returns to production and
all sticky settings stay pinned. Migrations are additive, so the old code
runs fine against the newer schema. No data is lost either way: the two
slots use separate databases.
