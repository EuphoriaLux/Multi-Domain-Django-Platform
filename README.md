# Entreprinder

[![Deploy](https://github.com/EuphoriaLux/Entreprinder/actions/workflows/deploy-azure-app-service-optimized.yml/badge.svg)](https://github.com/EuphoriaLux/Entreprinder/actions/workflows/deploy-azure-app-service-optimized.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django 5.1](https://img.shields.io/badge/django-5.1-green.svg)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Multi-domain Django application serving 6 distinct platforms from a single codebase, deployed on Azure App Service.

## Platforms

| Domain | Description | Type |
|--------|-------------|------|
| [crush.lu](https://crush.lu) | Privacy-first event-based dating for Luxembourg | Full-featured |
| [vinsdelux.com](https://vinsdelux.com) | Premium wine e-commerce with vineyard plot adoption | Full-featured |
| [entreprinder.lu](https://entreprinder.lu) | Entrepreneur networking with Tinder-style matching | Full-featured |
| [power-up.lu](https://power-up.lu) | Corporate/investor information site | Static |
| [tableau.lu](https://tableau.lu) | AI Art e-commerce platform | Static |
| [delegations.lu](https://delegations.lu) | Crush.lu delegation features | Full-featured |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Django 5.1, Python 3.10+ |
| Database | SQLite (dev), PostgreSQL (prod) |
| Frontend | Tailwind CSS, HTMX, Alpine.js (CSP build) |
| Authentication | Django Allauth, LinkedIn OAuth2, JWT |
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

#### Journey Command Arguments

```bash
python manage.py create_wonderland_journey \
    --first-name "Alice" \
    --last-name "Smith" \
    --date-met "2024-02-14" \
    --location-met "Luxembourg City"
```

### VinsDelux Platform

| Command | Description |
|---------|-------------|
| `create_sample_plots` | Creates 15+ sample vineyard plots with coordinates, soil types, grape varieties |
| `update_plot_descriptions` | Updates plot descriptions based on famous producers |

```bash
python manage.py create_sample_plots --count 20 --clear  # Clear existing and create 20 plots
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
pytest vinsdelux/tests/

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
- crush.lu, www.crush.lu
- vinsdelux.com, www.vinsdelux.com
- powerup.lu, www.powerup.lu
- power-up.lu, www.power-up.lu
- tableau.lu, www.tableau.lu

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
```

## Project Structure

```
entreprinder/
├── azureproject/          # Django config, middleware, domain routing
│   ├── domains.py         # Centralized domain configuration
│   ├── middleware.py      # Domain routing, health checks
│   ├── urls_crush.py      # Crush.lu URL config
│   ├── urls_vinsdelux.py  # VinsDelux URL config
│   └── production.py      # Production settings
├── crush_lu/              # Dating platform (full-featured)
│   ├── management/        # Management commands
│   ├── templates/         # Django templates
│   └── tests/             # pytest tests
├── vinsdelux/             # Wine e-commerce platform
├── entreprinder/          # Entrepreneur networking
├── power_up/              # Corporate site (static)
├── tableau/               # AI Art shop (static)
├── crush_delegation/      # Delegation features
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
| vinsdelux.com | `urls_vinsdelux.py` | - |
| entreprinder.lu | `urls_entreprinder.py` | - |
| power-up.lu, powerup.lu | `urls_power_up.py` | - |
| tableau.lu | `urls_tableau.py` | - |
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

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make changes and run tests: `pytest -m "not playwright"`
4. Format code: `black . && ruff check .`
5. Commit and push
6. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
