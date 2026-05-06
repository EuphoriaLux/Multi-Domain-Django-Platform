# Entreprinder

[![Deploy](https://github.com/EuphoriaLux/Entreprinder/actions/workflows/deploy-azure-app-service-optimized.yml/badge.svg)](https://github.com/EuphoriaLux/Entreprinder/actions/workflows/deploy-azure-app-service-optimized.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django 5.1](https://img.shields.io/badge/django-5.1-green.svg)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Multi-domain Django application serving 9 distinct platforms from a single codebase, deployed on Azure App Service.

## Platforms

| Domain | Description | Type |
|--------|-------------|------|
| [crush.lu](https://crush.lu) | Privacy-first event-based dating for Luxembourg | Full-featured |
| [api.crush.lu](https://api.crush.lu) | REST API for the hub.crush.lu CRM SPA | API |
| [vinsdelux.com](https://vinsdelux.com) | Wine vineyard adoption concept explainer | Static |
| [entreprinder.lu](https://entreprinder.lu) | Entrepreneur networking with Tinder-style matching | Full-featured |
| [power-up.lu](https://power-up.lu) | Corporate/investor information site | Static |
| [portal.powerup.lu](https://portal.powerup.lu) | Power-Up CRM & customer portal | Full-featured |
| [tableau.lu](https://tableau.lu) | AI Art e-commerce platform | Static |
| [delegations.lu](https://delegations.lu) | Crush.lu delegation features | Full-featured |
| [arborist.lu](https://arborist.lu) | Tree services informational site (i18n: EN/DE/FR) | Static |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Django 5.1, Python 3.10+ |
| Database | SQLite (dev), PostgreSQL (prod) |
| Frontend | Tailwind CSS, HTMX, Alpine.js (CSP build) |
| Authentication | Django Allauth, LuxID OIDC (POST Luxembourg), LinkedIn OAuth2, JWT (SimpleJWT) |
| API | Django REST Framework, JWT auth, CORS (django-cors-headers) |
| Storage | Local filesystem (dev), Azure Blob Storage (prod) |
| Email | Console (dev), Microsoft Graph API (prod) |
| Deployment | Azure App Service, GitHub Actions CI/CD |

## Quick Start

```bash
# Clone and enter directory
git clone https://github.com/EuphoriaLux/Entreprinder.git
cd Entreprinder

# Create and activate virtual environment
python -m venv .venv
.venv/Scripts/Activate.ps1  # Windows PowerShell
# or: source .venv/bin/activate  # Linux/macOS

# Install dependencies and run
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Visit http://localhost:8000 (routes to Crush.lu by default).

## Development Setup

### Prerequisites

- Python 3.10 or higher
- Node.js 20+ (for Tailwind CSS)
- Git

### Virtual Environment (Critical)

**Always activate the virtual environment before running Python commands.**

```powershell
# PowerShell (Windows)
.venv/Scripts/Activate.ps1

# Command Prompt (Windows)
.venv/Scripts/activate.bat

# Linux/macOS
source .venv/bin/activate
```

Running `python` or `pytest` without activating will fail with `ModuleNotFoundError`.

### Environment Variables

Create a `.env` file in the project root (optional for development):

```bash
SECRET_KEY=your-development-secret-key
# Database defaults to SQLite - no configuration needed for local dev
```

See `.env.example` for all available options.

### Build Tailwind CSS

```bash
npm install
npm run build:css        # Build for Crush.lu
npm run build:css:all    # Build all variants
npm run watch:css        # Watch mode for development
```

### Testing Different Platforms Locally

Edit `azureproject/domains.py` and change `DEV_DEFAULT`:

```python
DEV_DEFAULT = 'crush.lu'      # Default
DEV_DEFAULT = 'vinsdelux.com' # Test VinsDelux
DEV_DEFAULT = 'powerup.lu'    # Test PowerUP
```

## Management Commands

Run all commands with the virtual environment activated: `python manage.py <command>`

### Crush.lu Platform

| Command | Description |
|---------|-------------|
| `create_crush_coaches` | Creates 3 sample coach profiles (Marie, Thomas, Sophie) with specializations |
| `create_sample_events` | Creates 6 diverse sample meetup events (speed dating, mixers, activities) |
| `create_wonderland_journey` | Creates multi-chapter interactive journey experience |
| `populate_global_activity_options` | Populates 6 standard event activity voting options |
| `send_event_reminders` | Sends email reminders for upcoming events |
| `send_profile_reminders` | Sends profile submission/review reminders |
| `setup_local_dev` | Sets up local development environment with sample data |
| `create_sample_crush_profiles` | Creates sample user profiles for testing |
| `cleanup_orphan_storage` | Cleans orphaned files in Azure Blob Storage |
| `reset_local_dev` | Resets local development environment |
| `send_pre_screening_invites` | Sends pre-screening invitations to pending profiles |
| `send_newsletter` | Sends newsletter emails to subscribed users |
| `send_wallet_notification` | Sends push notifications for Apple/Google Wallet passes |
| `create_advent_calendar` | Creates the advent calendar event series |
| `create_google_wallet_class` | Creates/updates the Google Wallet pass class |
| `generate_crush_quiz` | Generates quiz questions for Quiz Night events |
| `generate_patch_notes` | Generates patch note entries from git history |
| `populate_event_attendees` | Backfills attendee records for existing events |
| `recalculate_match_scores` | Recalculates compatibility scores for all profile pairs |
| `reprocess_photos` | Reprocesses uploaded profile photos (resize, optimize) |
| `update_journey_translations` | Updates translation strings for journey content |
| `verify_phone` | Manually triggers phone verification for a user |
| `validate_timeline_events` | Checks hub timeline events for data integrity |
| `sla_tick` | Processes SLA timers for open hub requests (run periodically) |
| `sync_contacts_to_outlook` | Syncs user contacts to Outlook/Exchange |
| `cleanup_outlook_contacts` | Removes stale contacts from Outlook/Exchange |
| `check_translations` | Reports missing or fuzzy translation strings |
| `configure_event_forms` | Sets up custom registration form fields for an event |
| `backfill_user_consent` | Backfills GDPR consent records for existing users |
| `check_missing_consent` | Reports users without a valid consent record |
| `check_push_subscription_health` | Reports stale or invalid push subscriptions |
| `check_pwa_push_activation` | Checks PWA push notification activation rates |
| `business_plan_metrics` | Exports business plan KPIs to a report |

#### Journey Command Arguments

```bash
python manage.py create_wonderland_journey \
    --first-name "Alice" \
    --last-name "Smith" \
    --date-met "2024-02-14" \
    --location-met "Luxembourg City"
```

### Entreprinder Platform

| Command | Description |
|---------|-------------|
| `create_test_profiles` | Creates test entrepreneur profiles with bios, skills, industries |

```bash
python manage.py create_test_profiles 50  # Create 50 test profiles
```

### System Commands

| Command | Description |
|---------|-------------|
| `setup_cookie_groups` | Creates GDPR-compliant cookie consent groups (essential, analytics, marketing) |

## Testing

This project uses pytest with pytest-django and pytest-playwright.

```bash
# Run all tests
pytest

# Run tests without slow browser tests (recommended for quick feedback)
pytest -m "not playwright"

# Run specific app tests
pytest crush_lu/tests/

# Run single test
pytest crush_lu/tests/test_models.py::TestCrushProfile::test_age_calculation

# Verbose output
pytest -v --tb=short
```

### Key Test Fixtures

- `test_user` - Basic authenticated user
- `test_user_with_profile` - User with approved CrushProfile
- `coach_user` - User with CrushCoach privileges
- `sample_event` - Published MeetupEvent 7 days in future
- `authenticated_page` - Playwright page with logged-in session

## Deployment

### GitHub Actions CI/CD

The project uses an optimized GitHub Actions workflow:

1. **Validate** (PR only): Python syntax check, Django system checks (~30s)
2. **Test** (PR only): pytest excluding Playwright tests
3. **Deploy** (main only): Build CSS, deploy to Azure App Service

Target deployment time: 2-3 minutes.

### Production Domains

All domains are served from a single Azure App Service instance:
- crush.lu, www.crush.lu, api.crush.lu
- vinsdelux.com, www.vinsdelux.com
- entreprinder.lu, www.entreprinder.lu
- power-up.lu, www.power-up.lu, powerup.lu, www.powerup.lu, portal.powerup.lu
- tableau.lu, www.tableau.lu
- delegations.lu, www.delegations.lu
- arborist.lu, www.arborist.lu

### Production Environment Variables

Set these in Azure App Service Configuration:

```bash
# Required
SECRET_KEY=<secure-random-key>
AZURE_POSTGRESQL_CONNECTIONSTRING=<connection-string>
AZURE_ACCOUNT_NAME=<storage-account>
AZURE_ACCOUNT_KEY=<storage-key>

# Email (Microsoft Graph)
GRAPH_TENANT_ID=<tenant-id>
GRAPH_CLIENT_ID=<client-id>
GRAPH_CLIENT_SECRET=<client-secret>

# LuxID OIDC (POST Luxembourg CIAM)
LUXID_CLIENT_ID=<client-id>
LUXID_CLIENT_SECRET=<client-secret>
```

## Project Structure

```
entreprinder/
├── azureproject/          # Django config, middleware, domain routing
│   ├── domains.py         # Centralized domain configuration
│   ├── middleware.py      # Domain routing, health checks
│   ├── urls_crush.py      # Crush.lu URL config
│   ├── urls_api.py        # api.crush.lu URL config (hub REST API)
│   ├── urls_arborist.py   # Arborist URL config
│   ├── urls_portal.py     # portal.powerup.lu URL config
│   ├── urls_vinsdelux.py  # VinsDelux URL config
│   ├── views_spa_auth.py  # Session→JWT bridge for hub.crush.lu SPA
│   └── production.py      # Production settings
├── crush_lu/              # Dating platform (full-featured)
│   ├── management/        # Management commands
│   ├── providers/luxid/   # LuxID OIDC provider (POST Luxembourg CIAM)
│   ├── templates/         # Django templates
│   └── tests/             # pytest tests
├── hub/                   # REST API for hub.crush.lu CRM SPA
├── arborist/              # Tree services informational site (i18n)
├── vinsdelux/             # Wine adoption concept explainer (static)
├── entreprinder/          # Entrepreneur networking
├── power_up/              # Corporate site + CRM portal
│   ├── crm/               # Agent CRM/ticketing module
│   └── onboarding/        # Customer onboarding module
├── tableau/               # AI Art shop (static)
├── delegations/           # Delegation features
├── locale/                # i18n translations (en, de, fr)
├── infra/                 # Azure Bicep templates
├── static/                # Tailwind CSS, Alpine.js components
├── .github/workflows/     # GitHub Actions CI/CD
└── requirements.txt       # Python dependencies
```

## URL Architecture

Domain-based routing is configured in `azureproject/domains.py`:

| Host | URL Configuration | Default Dev |
|------|-------------------|-------------|
| localhost, 127.0.0.1 | Uses `DEV_DEFAULT` | crush.lu |
| crush.lu | `urls_crush.py` | - |
| api.crush.lu | `urls_api.py` | api.localhost |
| vinsdelux.com | `urls_vinsdelux.py` | vinsdelux.localhost |
| entreprinder.lu | `urls_entreprinder.py` | - |
| power-up.lu, powerup.lu | `urls_power_up.py` | power-up.localhost |
| portal.powerup.lu | `urls_portal.py` | portal.localhost |
| tableau.lu | `urls_tableau.py` | tableau.localhost |
| delegations.lu | `urls_delegations.py` | delegation.localhost |
| arborist.lu | `urls_arborist.py` | arborist.localhost |
| *.azurewebsites.net | `urls_entreprinder.py` | - |

## Code Quality

```bash
# Format with Black
black .

# Lint with Ruff
ruff check .
ruff check . --fix
```

Configuration in `pyproject.toml` (88 character line length).

## Additional Resources

- **Detailed Architecture**: See [CLAUDE.md](CLAUDE.md) for comprehensive documentation including:
  - Model relationships and data flow
  - Alpine.js CSP compliance patterns
  - Storage architecture (public vs private)
  - Email backend configuration
  - Internationalization (i18n) system
  - Troubleshooting guides

## Recently Shipped Features

### Hub CRM SPA API (`api.crush.lu`)

A dedicated REST API subdomain that backs the `hub.crush.lu` CRM single-page application. Built with Django REST Framework and SimpleJWT.

- **Session → JWT bridge**: `POST /api/token/exchange-code/` lets the cross-origin SPA swap a short-lived one-time code (set in the crush.lu session) for an access/refresh token pair.
- **Models**: `HubProfile`, `HubRequest`, `HubResource`, `HubTimelineEvent` — covering the full CRM surface.
- **Endpoints**: `/api/hub/me`, `/api/hub/requests`, `/api/hub/resources`, `/api/hub/timeline`.
- **CORS** scoped to `https://hub.crush.lu` and PR-preview origins.

### LuxID OIDC Authentication

Crush.lu now supports login via **LuxID** (POST Luxembourg CIAM), the Luxembourg national digital identity provider. The integration uses a custom allauth provider (`crush_lu.providers.luxid`) with a dedicated `/accounts/luxid/` URL namespace so it coexists cleanly with the generic OIDC provider used by LinkedIn.

- Claims are merged from both the `id_token` and the `userinfo` endpoint.
- Existing accounts matched by email are linked automatically on first LuxID login.
- Phone verification is expanded to cross-border countries.

### Phone Verification & 7-Step Onboarding

- OTP phone verification modal with rate limiting on both sending and submission.
- `intl-tel-input` flag remains visible after verification completes.
- Full **7-step onboarding journey** for new Crush.lu users: phone verification, profile details, photo upload, coach verification, ideal-crush preferences, and final review.

### Auth UX Improvements

- Password visibility toggle on login and signup forms.
- Real-time password strength meter on signup.
- Sign-up reassurance copy to reduce drop-off.
- **Email verification is now mandatory** — accounts cannot access protected views until their email address is confirmed.

### Quiz Night

- Coach host controls (pause, resume, force-rotate) and rotation edge-case guards.
- Countdown timer, pause screen, and graceful `display_name` fallback.
- HTTP polling fallback when WebSocket state arrives without table data.
- Continuous polling on the waiting screen so check-ins appear live.
- Full i18n support with language-prefixed URLs (`/en/`, `/de/`, `/fr/`).

### Pre-Screening & Hybrid Coach Review

- Automated pre-screening invitations (`send_pre_screening_invites`).
- Hybrid review workflow: coaches complete a calibration screening call, then the system routes profiles to a standard approval or revision flow.

### Apple Wallet Tickets

Crush.lu events support Apple Wallet passes (`.pkpass`). iOS Safari compatibility was fixed by disabling gzip compression on pass responses.

### Changelog Page

A marketing-ready changelog is available at [crush.lu/changelog/](https://crush.lu/changelog/) and auto-generated from tagged releases.

### Internationalisation (i18n)

German (DE) and French (FR) translations now cover coach emails, booking confirmation flow, hybrid-review UX strings, and arborist.lu page content.

### Power-Up Portal (`portal.powerup.lu`)

A new subdomain hosts a two-tier portal:
- `/power-admin/` — Agent CRM & ticketing UI for internal teams.
- `/crm/` and `/onboarding/` — Customer-facing portal (Entra SSO, Phase 3).
- Full Django admin at `/admin/` for superusers.

### Arborist.lu

A new informational site for Luxembourg tree services, fully internationalised (EN/DE/FR) with sitemap and custom robots.txt.

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make changes and run tests: `pytest -m "not playwright"`
4. Format code: `black . && ruff check .`
5. Commit and push
6. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
