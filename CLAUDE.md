# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Entreprinder is a multi-domain Django application serving three distinct platforms:
1. **Entreprinder** (`entreprinder.app`) - Entrepreneur networking with Tinder-style matching
2. **PowerUP** (`powerup.lu`) - Business platform variant for Luxembourg
3. **VinsDelux** (`vinsdelux.com`) - Premium wine e-commerce with vineyard plot adoption

## Architecture

- **Framework**: Django 5.1 with Python 3.10+
- **Database**: SQLite (dev), PostgreSQL (prod)
- **Authentication**: Django Allauth with LinkedIn OAuth2
- **Frontend**: Bootstrap 5, Crispy Forms, custom JavaScript for interactive features
- **Storage**: Local filesystem (dev), Azure Blob Storage (prod)
- **API**: Django REST Framework with JWT authentication
- **Deployment**: Azure App Service with WhiteNoise for static files

## Development Commands

### Essential Commands
```bash
# Run development server
python manage.py runserver

# Run tests for specific app
python manage.py test entreprinder
python manage.py test matching
python manage.py test vinsdelux

# Run all tests
python manage.py test

# Check for issues with flake8
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
```

### Database Management
```bash
# Create and apply migrations
python manage.py makemigrations
python manage.py migrate

# Create superuser for admin access
python manage.py createsuperuser
```

### VinsDelux Management Commands
```bash
# Create Luxembourg wine producers
python manage.py create_luxembourg_producers

# Create sample vineyard plots
python manage.py create_sample_plots

# Update plot descriptions
python manage.py update_plot_descriptions

# Populate vineyard data
python manage.py populate_vineyard_data

# Create default images for products
python manage.py create_default_images

# Assign adoption plan images
python manage.py assign_adoption_images
```

### Entreprinder Management Commands
```bash
# Create test entrepreneur profiles for matching
python manage.py create_test_profiles
```

## URL Architecture

The application uses domain-based routing (`azureproject/middleware.py`):
- `vinsdelux.com` → VinsDelux app
- `powerup.lu` → PowerUP variant
- Default → Entreprinder app

All apps support i18n with language prefixes (`/en/`, `/de/`, `/fr/`).

Key endpoints:
- `/healthz/` - Health check (no i18n)
- `/accounts/` - Authentication (no i18n)
- `/{lang}/admin/` - Django admin
- `/journey/plot-selection/` - VinsDelux plot selector (direct access)
- `/vinsdelux/api/adoption-plans/` - API endpoint (direct access)

## High-Level Code Structure

### Domain Routing System
The `DomainRoutingMiddleware` in `azureproject/middleware.py` dynamically sets `request.urlconf` based on the domain, allowing different URL configurations per platform while sharing the same codebase.

### VinsDelux Plot Selection System (Complete Architecture)

#### Overview
The plot selection system is the cornerstone of the VinsDelux wine adoption experience, allowing users to select vineyard plots for adoption. It has two implementations:
1. **Basic Plot Selector** (`/journey/plot-selection/`) - Simple adoption plan browsing
2. **Enhanced Plot Selector** (`/journey/enhanced-plot-selection/`) - Full plot selection with reservation system

#### Data Model Relationships
```
VdlProducer (Vineyard/Winery)
    ├── VdlPlot (Individual vineyard plots)
    │   ├── Status: AVAILABLE, RESERVED, ADOPTED, UNAVAILABLE
    │   ├── Geographic data (coordinates, elevation, soil)
    │   ├── Viticulture info (grape varieties, vine age)
    │   └── Many-to-Many → VdlAdoptionPlan
    │
    ├── VdlCoffret (Wine boxes - one-time purchase)
    │   └── One-to-Many → VdlAdoptionPlan
    │
    └── VdlAdoptionPlan (Subscription plans)
        ├── Associated with one VdlCoffret
        ├── Many-to-Many → VdlPlot
        └── Pricing, duration, benefits

VdlPlotReservation (User selections)
    ├── Links: User ↔ VdlPlot
    ├── 24-hour expiration
    └── Session data tracking
```

#### User Journey Flow

##### Step 1: Plot Discovery
- User lands on `/journey/plot-selection/` or `/journey/enhanced-plot-selection/`
- System queries `VdlPlot.objects.filter(status=PlotStatus.AVAILABLE)`
- Plots are displayed with:
  - Producer information (vineyard name, region)
  - Plot characteristics (size, elevation, soil type, sun exposure)
  - Grape varieties and expected wine profile
  - Associated adoption plans with pricing

##### Step 2: Interactive Selection
- **Basic Selector**: Browse adoption plans directly
  - Filter by producer, region, price range
  - View plan details (duration, coffrets per year, benefits)
  
- **Enhanced Selector**: Interactive plot selection
  - Map visualization with plot locations (coordinates stored as GeoJSON)
  - Plot cards with detailed information
  - Real-time availability checking
  - Cart system via `PlotSelection` JavaScript class

##### Step 3: Data Persistence
- **Authenticated Users**:
  - Creates `VdlPlotReservation` records
  - 24-hour reservation period
  - Clears previous unconfirmed reservations
  - Updates plot status to RESERVED
  
- **Guest Users**:
  - Stores selections in Django session
  - Keys: `selected_plots`, `plot_selection_timestamp`, `plot_selection_notes`
  - Data persists for session duration

##### Step 4: API Integration
The system provides REST endpoints for dynamic interactions:
- `GET /api/plots/` - List available plots with filters
- `GET /api/plots/<id>/` - Plot details with adoption plans
- `GET /api/plots/availability/` - Real-time availability stats
- `POST /api/plots/reserve/` - Create reservations (authenticated)
- `GET/POST /api/plots/selection/` - Manage current selection
- `GET /api/adoption-plans/` - Filter and retrieve adoption plans

#### Frontend JavaScript Architecture
`static/vinsdelux/js/plot-selection.js` provides:
- `PlotSelection` class for cart management
- Session storage sync
- Event-driven architecture:
  - `vineyardMap:plotSelected`
  - `plotSelector:plotSelected`
  - `plotSelector:plotDeselected`
- Maximum selection limits
- Animation and notification system

#### Key Implementation Details

##### View Functions (`vinsdelux/views.py`)
- `plot_selector()`: Basic view, returns adoption plans with related data
- `EnhancedPlotSelectionView`: Class-based view
  - GET: Prepares plot data JSON for frontend
  - POST: Handles plot selection submission
  - Creates reservations or session storage

##### Data Validation
- Plots must have `status=PlotStatus.AVAILABLE`
- Adoption plans must have `is_available=True`
- Reservations check for duplicate user-plot combinations
- Expired reservations are handled via `is_expired` property

##### Progressive Enhancement
The system works without JavaScript:
- Basic HTML forms for selection
- Server-side filtering and pagination
- JavaScript adds interactivity when available

### Matching System Architecture
- **Swipe Interface**: JavaScript-powered card swiping (`matching/templates/`)
- **Match Logic**: Mutual likes create matches (`matching/models.py`)
- **Profile Discovery**: Excludes already-interacted profiles

### Authentication Flow
1. LinkedIn OAuth via Allauth
2. Custom adapter (`entreprinder/linkedin_adapter.py`) imports profile photos
3. Signal handlers (`entreprinder/signals.py`) create user profiles
4. JWT tokens for API access

## Environment Configuration

### Required Environment Variables
```bash
# Database (PostgreSQL in production)
DBNAME=<database-name>
DBHOST=<database-hostname>
DBUSER=<db-user-name>
DBPASS=<db-password>

# Azure Storage (production only)
AZURE_ACCOUNT_NAME=<storage-account>
AZURE_ACCOUNT_KEY=<storage-key>

# Django
SECRET_KEY=<django-secret-key>

# Email (optional)
EMAIL_HOST=<smtp-server>
EMAIL_HOST_USER=<email-user>
EMAIL_HOST_PASSWORD=<email-password>
```

## Testing Strategy

- **Unit Tests**: Each app has `tests.py` for model and view tests
- **Frontend Tests**: Selenium-based tests in `vinsdelux/tests/test_frontend.py`
- **CI/CD**: GitHub Actions runs tests on Python 3.10 and 3.11 with PostgreSQL

## Key Models

### Entreprinder
- `EntrepreneurProfile`: Extended user profile with business details
- `Industry`, `Skill`: Categorization for matching
- `Match`, `Like`, `Dislike`: Interaction tracking

### VinsDelux
- `VdlProducer`: Wine producers with map coordinates
- `VdlPlot`: Vineyard plots with adoption status
- `VdlAdoptionPlan`: Subscription plans with pricing tiers
- `VdlCoffret`: Wine box products

## Azure Deployment

- **Infrastructure**: Bicep templates in `infra/`
- **Configuration**: `azure.yaml` for Azure Developer CLI
- **Settings**: `azureproject.production` auto-loads on Azure
- **GitHub Actions**: Automated deployment workflows in `.github/workflows/`

## Important Development Guidelines

### Plot Selection System Expansion
When extending the plot selection functionality:

1. **Data Integrity**: Always work with existing model relationships
   - Never create fictional data - all plots must have real producers
   - Adoption plans must reference existing coffrets
   - Use management commands to populate test data

2. **Model Constraints**:
   - `VdlPlot.status` must be one of: AVAILABLE, RESERVED, ADOPTED, UNAVAILABLE
   - `VdlPlotReservation` enforces unique user-plot combinations
   - Reservations expire after 24 hours (configurable in view)

3. **API Response Format**: Maintain consistent JSON structure
   ```python
   {
       'success': bool,
       'message': str,
       'data': {...},  # Main response data
       'errors': [...]  # If applicable
   }
   ```

4. **Session Management**:
   - Guest users: Data stored in Django session
   - Authenticated users: Data stored in database
   - Always check both sources when retrieving selections

5. **Frontend Events**: Use established event naming
   - `vineyardMap:*` for map interactions
   - `plotSelector:*` for selection UI
   - `cart:*` for cart operations