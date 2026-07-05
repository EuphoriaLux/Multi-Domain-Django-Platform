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
  (while the old code is still serving). Every release must be checked for
  backward-compatible migrations before the swap starts; this repo has shipped
  non-additive migrations before, so do not assume swap rollback is safe.
- Slot-sticky settings pin the DB connection and domain config to each slot:
  `AZURE_POSTGRESQL_CONNECTIONSTRING` (staging uses its own DB
  `pythonapp_staging`), `STAGING_MODE`, `ALLOWED_HOSTS_ENV`, `CUSTOM_DOMAINS`,
  Redis, analytics and App Insights settings. These **do not move** during a
  swap. Non-sticky settings exchange between slots — they are
  value-identical on both slots, so the exchange is a no-op **for those
  settings only**.
- **`HYBRID_COACH_SYSTEM_ENABLED`, `PRE_SCREENING_ENABLED`,
  `CRUSH_CONNECT_LAUNCHED`, and `ADMIN_API_KEY` are NOT in
  `slotConfigNames` in `infra/resources.bicep`** — only
  `AZURE_POSTGRESQL_CONNECTIONSTRING` and the settings listed above are
  pinned there. Unless the live Azure resource has been configured with
  additional sticky settings beyond what's in source-controlled IaC, these
  four **will swap** every time, meaning a swap can push staging's feature
  flags and API key into production. Before relying on the "swap can't leak
  config" guarantee, check Portal → App Service → Configuration → the
  "Deployment slot setting" checkbox for each of these four names on both
  slots, and reconcile `infra/resources.bicep` to match (or add them to
  `slotConfigNames` and redeploy the IaC) so the checklist below is
  accurate the next time this runs.
- Custom domains never swap: `test.<domain>` always points at the staging
  slot, production domains always at production.

## Pre-swap checklist

- [ ] Confirm the staging slot runs the commit you intend to ship
      (Portal → Deployment Center on the slot, or the latest
      `deploy-azure-app-service-optimized` run in GitHub Actions).
- [ ] Smoke-test `test.crush.lu`: login, dashboard, one Crush Connect page.
- [ ] Verify (don't assume) which app settings are actually pinned:
      Portal → App Service → Configuration → check the "Deployment slot
      setting" box next to `HYBRID_COACH_SYSTEM_ENABLED`,
      `PRE_SCREENING_ENABLED`, `CRUSH_CONNECT_LAUNCHED`, and `ADMIN_API_KEY`
      on both slots. `infra/resources.bicep`'s `slotConfigNames` does not
      list these, so if the live resource hasn't been configured to pin
      them independently, they will swap. If unpinned, either mark them
      sticky before swapping or treat their post-swap values as staging's.
- [ ] If `ADMIN_API_KEY`, `HYBRID_COACH_SYSTEM_ENABLED`, or
      `PRE_SCREENING_ENABLED` are not slot-sticky, do not leave the external
      `crush-hybrid-maintenance` timers pointed at `test.crush.lu` during the
      swap. Make the settings sticky, disable those timers, or retarget the
      function URLs/key before swapping so staging-hosted timer calls cannot
      succeed against production DB/email settings during warm-up.
- [ ] Verify `WEBSITES_CONTAINER_START_TIME_LIMIT` is set to `600` on the
      **production target and staging source slots** (Portal → Configuration →
      Application settings).
      It is not in `infra/resources.bicep`, and Azure's Linux App Service
      default is 230s — well short of a migration-heavy warm-up. Set it if
      missing.
- [ ] Verify App Service swap warm-up uses a real health signal before the
      swap: set `WEBSITE_SWAP_WARMUP_PING_PATH=/healthz/` and
      `WEBSITE_SWAP_WARMUP_PING_STATUSES=200` on the web app/slots. These are
      not in `infra/resources.bicep`; without them Azure can warm `/` and treat
      any response status as success.
- [ ] Diff the migrations shipped since the previous production commit before
      starting the swap:
      `git diff <prev-prod-sha>..HEAD -- '*/migrations/*.py'`
      and search the patch for `DeleteModel`, `RemoveField`, `RenameField`,
      `RenameModel`, `AlterField` type changes, `RunPython`, or `RunSQL`. If
      any are present, confirm old production code can run against the migrated
      schema or plan a forward-only rollback/schema restore path before
      swapping.
- [ ] Pick a quiet hour. The plan is a single 4 GB P0v3 instance already at
      ~80% average memory; during the swap **both containers run
      concurrently**. Optional headroom: stop or explicitly disable the three
      function apps/triggers
      (`crush-hybrid-maintenance`, `crush-contact-sync`, `finops-daily-sync`)
      for the swap window. **Do not leave them stopped across a scheduled
      occurrence and then restart during post-swap verification**: all three use
      `use_monitor=True`, so Azure can fire a `past_due` invocation right on
      restart. Either leave them running, restart before the next due time, or
      keep the triggers disabled until after verification and intentionally
      re-enable/run them.

## The swap

1. Portal → `django-app-ajfffwjb5ie3s-app-service` → **Deployment slots** →
   **Swap** (source: `staging`, target: `production`).
2. Expect warm-up to take a few minutes: the incoming container starts with
   production config and runs pending migrations against the prod DB. This
   is only safely covered if `WEBSITES_CONTAINER_START_TIME_LIMIT=600` was
   confirmed/set on the production target and staging source slots in the
   pre-swap checklist above — otherwise Azure's 230s default can abort/recycle
   the container during warm-up. The warm-up success signal is only meaningful
   if `/healthz/` with
   status `200` was configured in the pre-swap checklist; otherwise Azure may
   accept the default `/` path or a non-200 response. Don't cancel a slow
   warm-up.
3. Do not treat function auth/key drift as harmless during warm-up. If
   `ADMIN_API_KEY`, `HYBRID_COACH_SYSTEM_ENABLED`, and `PRE_SCREENING_ENABLED`
   were not confirmed sticky before the swap, the staging source can warm with
   production DB/email settings while the external `crush-hybrid-maintenance`
   app still posts its unchanged key to `test.crush.lu`. Before swapping, make
   those settings sticky or disable/retarget the affected timers; otherwise
   timer calls can succeed against production data from the staging host.
4. If using **Swap with preview**: during preview, `test.crush.lu` serves the
   NEW code against the PRODUCTION database with real email enabled. Keep the
   preview short and don't trigger user-facing emails from it.

## Post-swap verification

- [ ] `https://crush.lu/healthz/` returns 200; spot-check one or two other
      domains (`entreprinder.lu`, `portal.powerup.lu`).
- [ ] Login + dashboard smoke on `crush.lu`.
- [ ] Crush Connect is still **teaser-gated** for non-staff on production
      (deliberate: `CRUSH_CONNECT_LAUNCHED` should be unset on prod). If it
      isn't and `CRUSH_CONNECT_LAUNCHED` wasn't confirmed sticky in the
      pre-swap checklist, it likely just swapped in from staging — fix the
      setting on the production slot directly. Staff accounts can reach the
      real Connect pages regardless.
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
- [ ] **Required, not optional**: repoint `DJANGO_PRE_SCREENING_INVITES_URL`
      and `DJANGO_HYBRID_SLA_SWEEP_URL` to `https://crush.lu/...` as well.
      `function_app.py` reads the *same* `ADMIN_API_KEY` for every trigger
      and posts it to whichever URL each one is configured with — once
      `ADMIN_API_KEY` is rotated to the production value above, any of
      these two left pointing at `test.crush.lu` will call staging with the
      production key and get a 401 (`HYBRID_COACH_SYSTEM_ENABLED` /
      `PRE_SCREENING_ENABLED` being off in prod only makes the endpoint a
      no-op — it doesn't prevent the auth failure). If you don't want to
      repoint them yet, disable those two triggers instead of leaving them
      on the staging URL.
- [ ] Verify/set `HYBRID_MAINTENANCE_ENABLED=true` on the function app after
      the URL/key changes. The function checks this flag before every timer
      trigger, and the provisioning default is `false`; without this, weekly
      KPI delivery and question rotation stay silently disabled.
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
all sticky settings stay pinned. **This is only safe if every migration
shipped in this release is additive/backward-compatible.** The codebase has
already shipped non-additive migrations (e.g.
`crush_lu/migrations/0166_delete_speeddatingpair.py` does a `DeleteModel`,
`crush_lu/migrations/0141_connection_window_hours.py` does a `RenameField`);
if a swap includes one of these, the old code can reference a table/column
that migration removed or renamed, and swapping back will 500 instead of
recovering. Before treating rollback as the answer:

- [ ] Diff the migrations that shipped between the previous production
      commit and this one (`git diff <prev-prod-sha>..HEAD --
      '*/migrations/*.py'`) and check the patch itself for `DeleteModel`,
      `RemoveField`, `RenameField`, `RenameModel`, `AlterField` type changes,
      `RunPython`, or `RunSQL`. Do not use `git log --stat` for this check:
      it only shows file-level line counts, not migration operation names.
- [ ] If any are present, rollback is not safe as a pure swap — plan a
      forward fix instead, or restore the production DB schema/data to
      match what the old code expects before swapping back.

The separate slot databases prevent staging data from mixing into production,
but they do not undo destructive migrations already applied to the production
database during warm-up. If a release deletes/renames/rewrites schema or data,
a rollback may require an explicit production schema/data restore or a forward
fix before the old code can safely serve traffic again.
