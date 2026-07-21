# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this is

A single Django 6.0 codebase that serves **9 distinct websites** from one Azure App Service instance, routed by HTTP host. One database, one settings module, one deploy — the domain you're on is decided per-request by middleware. See `azureproject/domains.py` for the authoritative domain → app → URLconf mapping. The README has a platform table and feature changelog; this file covers the cross-cutting architecture you can't learn from a single file.

## Essential commands

**Always activate the venv first** — `python`/`pytest` without it fail with `ModuleNotFoundError`:

```powershell
.venv/Scripts/Activate.ps1   # PowerShell (this is a Windows dev box)
```

```bash
python manage.py runserver          # serves crush.lu by default (DEV_DEFAULT)
python manage.py migrate
pytest                              # Playwright excluded, stops on first failure (see below)
pytest crush_lu/tests/test_models.py::TestCrushProfile::test_age_calculation  # single test
pytest -m playwright                # run ONLY the browser tests (slow)
black . && ruff check .             # format + lint (88-col, config in pyproject.toml)
npm run build:css                   # Tailwind for crush.lu; build:css:all for every platform
```

`pytest.ini` sets `addopts = -x -n auto --dist worksteal -m "not playwright" --reuse-db`: tests stop at the **first failure**, run in **parallel**, **reuse the test DB**, and **skip Playwright** unless you ask for it. Locally the test DB is file-based (`test_db.sqlite3_gw*`), so `--reuse-db` really skips the migration replay — run `pytest --create-db` once after pulling new migrations or after a `-m playwright` run. Tests hash passwords with MD5 (set in `conftest.py`) — never assert on hash algorithm or timing. `testpaths` is only `crush_lu/tests`, `hub/tests` and `power_up/finops/tests` — other apps' tests aren't collected by default (`entreprinder/tests.py`, `arborist/tests.py` and `power_up/tests.py` exist but don't match `python_files = test_*.py`, and all three have drifted/failing tests — repair before wiring them in).

### Switching which site you see locally

Edit `DEV_DEFAULT` in `azureproject/domains.py` (e.g. `'vinsdelux.com'`), **or** visit the `*.localhost` alias (`crush.localhost:8000`, `portal.localhost:8000`, `api.localhost:8000`, …) — these are mapped in `DEV_DOMAIN_MAPPINGS` and avoid HSTS issues with the real `.lu` domains.

## Architecture

### Multi-domain routing (the core mechanic)

`DomainURLRoutingMiddleware` reads the host and sets `request.urlconf` to the per-domain module (`urls_crush`, `urls_api`, `urls_power_up`, …) before URL resolution. There is **no single root URLconf** in practice — `ROOT_URLCONF = azureproject.urls` is only the fallback. To add or change a route, edit the right `azureproject/urls_<domain>.py`, not a global one.

`azureproject/domains.py` is the single source of truth: it drives routing, `ALLOWED_HOSTS` (`get_all_hosts()`), the Sites framework, and dev mappings. Add a new platform here first.

### Middleware order is load-bearing

In `settings.py`, `MIDDLEWARE` ordering is commented per-line because it matters:
- `HealthCheckMiddleware` **must be first** — short-circuits `/healthz/` before anything else (Azure probes).
- `CorsMiddleware` before `CommonMiddleware`; CORS is scoped to `^/(hub|api)/.*$` only (`CORS_URLS_REGEX`).
- `DomainURLRoutingMiddleware` **before** `LocaleMiddleware` (locale depends on the resolved urlconf).
- `SafeCurrentSiteMiddleware` replaces Django's `CurrentSiteMiddleware`: it auto-creates/repairs `Site` rows and **never raises `Site.DoesNotExist`** (which would 500 health checks). `SITE_ID` is deliberately **unset** so each domain resolves its own Site.

### Settings split

- `azureproject/settings.py` — used by local dev **and pytest** (plain HTTP, SQLite fallback, console email).
- `azureproject/production.py` — `from .settings import *` plus SSL redirect, HSTS, Azure Blob storage, and a `validate_host` monkey-patch for Azure internal IPs (`169.254.*`) and `test.*`/`test-*` staging slots.
- `manage.py` picks production automatically when `WEBSITE_HOSTNAME` is set (i.e. on Azure), else settings; it also loads `.env` locally.

### ASGI / WebSockets

`manage.py runserver` **auto-switches to Uvicorn (ASGI)** when `REDIS_URL` is set, enabling Django Channels WebSockets (used by Quiz Night, `crush_lu/consumers.py`). Without `REDIS_URL` you get the normal WSGI dev server and an in-memory channel layer. Cache and channel layers both upgrade to Redis when `REDIS_URL` is present.

### Storage: platform-namespaced, public vs private

`STORAGES` defines named backends per platform (`crush_media`, `crush_private`, `entreprinder_media`, `powerup_media`, `powerup_finops`, `shared_media`). Models pass an explicit `storage=` — **don't rely on `default`**. Three-tier selection: Azurite emulator (`USE_AZURITE=true`) → Azure Blob (`AZURE_ACCOUNT_NAME` set) → local filesystem. `crush_private` (profile photos) is a separate container from public media.

### Auth (django-allauth, multi-domain)

- Login is **email-only**, **email verification mandatory**, social auto-linking by verified email.
- Custom adapters `azureproject.adapters.MultiDomain{Account,SocialAccount}Adapter` make signup forms/behaviour domain-aware (e.g. crush_lu templates override entreprinder's — note the **`INSTALLED_APPS` order**: `crush_lu` before `entreprinder` so its `account/` templates win).
- **LuxID** (POST Luxembourg national CIAM) is a *custom* allauth provider at `crush_lu/providers/luxid/`, mounted at `/accounts/luxid/` so it coexists with the generic OIDC provider (LinkedIn). Other social providers: Google, Facebook, Microsoft, Apple.
- `OAuthCallbackProtectionMiddleware` dedupes replayed OAuth callbacks (Android PWA quirk) using a DB-backed `OAuthState`, not the session.

### The `hub` app / `api.crush.lu`

DRF + SimpleJWT JSON API backing the external `hub.crush.lu` CRM SPA. JWT Bearer auth (no cookies → `CORS_ALLOW_CREDENTIALS = False`). Refresh tokens rotate + blacklist. A **session→JWT bridge** (`azureproject/views_spa_auth.py`) lets the cross-origin SPA swap a one-time code for tokens; allowed return URLs are exact-matched in `SPA_CALLBACK_ALLOWED_RETURN_URLS`.

### Background tasks

Uses Django 6.0's native `TASKS` framework. Default `ImmediateBackend` runs inline (safe for dev). Production sets `DJANGO_TASKS_BACKEND` to the DB backend and runs `manage.py db_worker`. **conftest.py forces `ImmediateBackend` in tests regardless of env.**

### Multi-channel campaigns (crush_lu Coach Panel)

`/crush-admin/campaigns/` runs unified outreach campaigns across **email / WhatsApp / web push**: compose once, pick a segment (`get_segment_definitions()`), send now or schedule. Spec + runbook: `docs/specs/campaign-dashboard.md`. Key mechanics you must not break:

- **One `Campaign`, per-channel engines**: the email leg is a campaign-linked `Newsletter` (nullable `Newsletter.campaign` OneToOne — standalone newsletters must stay byte-identical); WhatsApp/push state lives in `CampaignRecipient` (`WhatsAppMessage.user` is the *sending admin*, recipient is a phone string — never treat it as the recipient FK). Channel adapters + dispatcher: `crush_lu/services/campaigns.py`; shared Meta send: `hub/whatsapp_service.py` (also backs the hub CRM views — response shapes are contract).
- **No inline sends**: production has no task worker, so sending is driven by the `CampaignDispatch` Azure Function timer (every 5 min, `hybrid-maintenance` app) → `POST /api/admin/campaigns/dispatch/` (`crush_lu/api_admin_campaigns.py`, Bearer `ADMIN_API_KEY`, outside `i18n_patterns`) → bounded resumable tick (email 25 = one Graph batch / WhatsApp 30 / push 150, ~80s wall budget under the 120s gunicorn timeout; heartbeat claim prevents double-sends). Gated by `CAMPAIGN_DISPATCH_ENABLED` (default OFF). Manual: `manage.py dispatch_campaigns [--dry-run]`.
- **Consent per channel**: email = newsletter opt-in; WhatsApp = explicit `whatsapp_opt_in` + verified phone + not `not_on_whatsapp`; push = enabled `PushSubscription`; banned (`crushlu_banned`) always excluded. Failed recipients are terminal for dispatch (paid WhatsApp templates / bouncing addresses are not auto-retried).
- **Click tracking**: `/c/<token>/` (language-neutral) records `CampaignClick` (no IP/UA — deliberate GDPR data minimization) and 302s to the UTM-tagged destination; recipient attribution via signed `?r=`. Campaign email links are rewritten at send time — **unsubscribe links must stay direct**.
- WhatsApp templates follow the OTP convention: one Meta template name, per-language variants (en/de/fr) selected by recipient language; body params `{{1}}`–`{{5}}` support `{first_name}`/`{last_name}`/`{email}` merge tokens.

### i18n

EN/DE/FR via `i18n_patterns` (language-prefixed URLs like `/fr/…`) **plus** `django-modeltranslation` for translatable model fields (admin gets per-language tabs — `modeltranslation` must be first in `INSTALLED_APPS`). Admin panels are forced to English and live *outside* `i18n_patterns`; `AdminLanguagePrefixRedirectMiddleware` strips accidental `/fr/admin/` prefixes. `manage.py check_translations` reports missing/fuzzy strings.

## App layout

`crush_lu` is by far the largest app and uses **split-by-feature files**: `models/` and `admin/` are packages, and views/api/forms are sharded (`views_<feature>.py`, `api_<feature>.py`). When adding a feature, follow this convention rather than growing a monolithic `views.py`. Other apps (`vinsdelux`, `arborist`, `tableau` are mostly static; `entreprinder`, `power_up`, `delegations`, `hub` are full apps). `power_up` contains submodules `crm/`, `onboarding/`, `finops/` registered as separate apps.

## Crush.lu conventions (scoped to crush_lu only)

`crush_lu/STYLE.md` is the **canonical visual + component reference** — read it before touching any `crush_lu/templates/crush_lu/` page. It defines the four canonical button variants, design tokens (brand colors live in **four** places that must stay in sync), shared component partials, and the Alpine.js mixin pattern.

**Alpine.js runs under a CSP build** (no inline expressions with args): compose shared behaviour with the `mixin(target, source)` helper in `alpine-components.js` — **never `Object.assign`/spread**, which silently kills getters. Register named components via `Alpine.data`.

A **pre-commit design-token linter** (`crush_lu/scripts/lint_design_tokens.py`, wired in `.pre-commit-config.yaml`) flags hardcoded brand hexes and deprecated button classes in changed `crush_lu/templates/crush_lu/*.html` files. Install with `pip install pre-commit && pre-commit install`.

## Deployment

GitHub Actions (`.github/workflows/`): `test-and-validate.yml` runs on PRs (Django checks + pytest minus Playwright); `deploy-azure-app-service-optimized.yml` builds CSS and deploys to Azure on `main`. Several Azure Functions (`azure-functions/`) deploy via their own workflows. Infra is Bicep under `infra/`. Production env vars (SECRET_KEY, `AZURE_POSTGRESQL_CONNECTIONSTRING`, Graph email, LuxID, Wallet certs, …) are set in App Service configuration — see README "Production Environment Variables" and `.env.example`.
