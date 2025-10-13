# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Entreprinder is a multi-domain Django application serving four distinct platforms:
1. **Entreprinder** (`entreprinder.app`) - Entrepreneur networking with Tinder-style matching
2. **PowerUP** (`powerup.lu`) - Business platform variant for Luxembourg
3. **VinsDelux** (`vinsdelux.com`) - Premium wine e-commerce with vineyard plot adoption
4. **Crush.lu** (`crush.lu`) - Privacy-focused dating platform with event-based meetups

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

### Crush.lu Management Commands
```bash
# Create Crush Coach profiles
python manage.py create_crush_coaches

# Create sample meetup events
python manage.py create_sample_events
```

## URL Architecture

The application uses domain-based routing (`azureproject/middleware.py`):
- `vinsdelux.com` → VinsDelux app
- `powerup.lu` → PowerUP variant
- `crush.lu` → Crush.lu dating platform
- Default → Entreprinder app (PowerUP fallback)

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

## Storage Architecture

The application uses a **dual-storage strategy** for Azure Blob Storage:

### 1. Public CDN Storage (Media Files)
- **Container**: `AZURE_CONTAINER_NAME` (e.g., `media`)
- **Access Level**: Public (anonymous read access)
- **Usage**: Wine images, vineyard photos, general media assets
- **Configuration**: Configured via `STORAGES["default"]` in [azureproject/production.py](azureproject/production.py)
- **URLs**: Direct public URLs without authentication

### 2. Private Storage (Crush.lu Profile Photos)
- **Container**: `crush-profiles-private` (hardcoded)
- **Access Level**: Private (no anonymous access)
- **Usage**: User profile photos on Crush.lu platform
- **Configuration**: Custom storage backend in [crush_lu/storage.py](crush_lu/storage.py)
- **URLs**: Time-limited SAS tokens (default: 1 hour expiration)
- **Security**: Prevents unauthorized access and photo scraping

### Storage Backend Classes

**`CrushProfilePhotoStorage`** ([crush_lu/storage.py:56](crush_lu/storage.py#L56)):
- Extends `PrivateAzureStorage`
- Generates SAS tokens with read-only permissions
- Unique UUID-based filenames prevent enumeration
- 30-minute token expiration for profile photos
- Format: `https://{account}.blob.core.windows.net/crush-profiles-private/{path}?{sas_token}`

**Implementation in Models** ([crush_lu/models.py:106-123](crush_lu/models.py#L106)):
```python
# Conditional storage: private in production, default in development
photo_1 = models.ImageField(storage=crush_photo_storage)
```

### Azure Setup for Private Photos
1. Create container: `crush-profiles-private`
2. Set access level to **Private (no anonymous access)**
3. Use same `AZURE_ACCOUNT_NAME` and `AZURE_ACCOUNT_KEY`
4. Photos automatically served with SAS tokens in production

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

### Crush.lu
- `CrushProfile`: User profiles with privacy controls and approval status
- `CrushCoach`: Coach profiles for onboarding/review
- `ProfileSubmission`: Profile review workflow with statuses (pending/approved/rejected/revision)
- `MeetupEvent`: Speed dating and social events
- `EventRegistration`: User event RSVPs with waitlist support
- `CoachSession`: Coach-user interaction tracking

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

### Crush.lu System Architecture

The Crush.lu platform is a privacy-first, event-based dating platform specifically designed for Luxembourg's small community. Unlike traditional swipe-based dating apps, Crush.lu focuses on real-world connections through organized meetup events.

#### Core Philosophy
- **Privacy First**: Small community considerations with profile anonymization options
- **Coach-Curated**: Human review ensures authentic, respectful profiles
- **Event-Driven**: No endless browsing - meet people at organized events
- **Gen Z/Millennial Focus**: Modern language and approach to dating

#### User Onboarding Flow

1. **Registration** (`crush_lu/views.py:signup`)
   - Custom signup form extending `UserCreationForm`
   - Email as username for simplicity
   - First name, last name, email, password

2. **Profile Creation** (`crush_lu/views.py:create_profile`)
   - Comprehensive profile form with:
     - Date of birth (18+ validation)
     - Gender, location, phone
     - Bio and interests
     - Up to 3 photos
     - Privacy settings (name display, age display, photo blur)
   - Creates `CrushProfile` instance
   - Automatically creates `ProfileSubmission` for review

3. **Coach Assignment** (`ProfileSubmission.assign_coach()`)
   - Auto-assigns to available coach with lowest workload
   - Coaches have `max_active_reviews` limit
   - Tracks pending, approved, rejected, revision statuses

4. **Profile Review** (Coach dashboard)
   - Coaches access pending submissions
   - Can approve, reject, or request revisions
   - Provides feedback to users
   - Updates `is_approved` status on profile

5. **Event Access**
   - Only approved profiles can register for events
   - Browse upcoming speed dating, mixers, activity meetups
   - Registration with dietary restrictions and special requests
   - Waitlist functionality when events are full

#### Data Models Architecture

**CrushProfile**:
- Linked to Django User (OneToOne)
- Privacy controls: `show_full_name`, `show_exact_age`, `blur_photos`
- Properties: `age`, `age_range`, `display_name`
- Status: `is_approved`, `is_active`, `approved_at`

**ProfileSubmission**:
- Tracks review workflow
- Status choices: pending, approved, rejected, revision
- Links profile to assigned coach
- Stores `coach_notes` (internal) and `feedback_to_user` (visible to user)
- Auto-assignment via `assign_coach()` method

**MeetupEvent**:
- Event types: speed_dating, mixer, activity, themed
- Capacity management: `max_participants`, min/max age
- Registration deadline and fees
- Properties: `is_registration_open`, `is_full`, `spots_remaining`
- Methods: `get_confirmed_count()`, `get_waitlist_count()`

**EventRegistration**:
- Status workflow: pending → confirmed/waitlist → attended/no_show
- Payment tracking: `payment_confirmed`, `payment_date`
- Unique constraint on (event, user)
- Additional info: dietary restrictions, special requests

#### Key Views and Logic

**Public Views**:
- `home()`: Landing page with upcoming events
- `about()`, `how_it_works()`: Information pages
- `event_list()`: Browse all published events
- `event_detail()`: Individual event information

**Authenticated User Views**:
- `dashboard()`: User dashboard with profile status and event registrations
- `create_profile()`, `edit_profile()`: Profile management
- `event_register()`: Register for events (requires approved profile)
- `event_cancel()`: Cancel event registration

**Coach Views** (requires `CrushCoach` object):
- `coach_dashboard()`: View pending and recent reviews
- `coach_review_profile()`: Review and approve/reject profiles
- `coach_sessions()`: View coaching sessions history

#### Privacy and Safety Features

1. **Display Name Control**: Users choose between full name or first name only
2. **Age Display Options**: Exact age or age range (18-24, 25-29, etc.)
3. **Photo Blurring**: Option to blur photos until mutual interest
4. **Profile Approval**: All profiles reviewed before going live
5. **18+ Only**: Date of birth validation in forms
6. **Event-Only Interaction**: No direct messaging - connections happen at events

#### Management Commands

**`create_crush_coaches`**:
- Creates 3 sample coach profiles (Marie, Thomas, Sophie)
- Assigns specializations (young professionals, students, 35+)
- Sets default password: `crushcoach2025`
- Configures workload limits

**`create_sample_events`**:
- Creates 6 diverse sample events
- Various types: speed dating, mixers, activity meetups, themed events
- Different locations across Luxembourg
- Age ranges and capacity limits
- Registration fees from free to €25

#### Development Best Practices

When working on Crush.lu:

1. **Privacy First**: Always consider privacy implications
   - Never expose more user data than explicitly allowed by privacy settings
   - Respect `show_full_name`, `show_exact_age`, `blur_photos` flags
   - Use `display_name` property instead of direct name access

2. **Approval Workflow**: Maintain profile approval integrity
   - Check `is_approved` before allowing event registration
   - Ensure `ProfileSubmission` is created for all new profiles
   - Auto-assign coaches using `assign_coach()` method

3. **Event Management**: 
   - Respect capacity limits via `is_full` property
   - Handle waitlist logic when events reach capacity
   - Check `is_registration_open` before allowing registrations
   - Enforce registration deadlines

4. **Coach Workload**: 
   - Never exceed coach's `max_active_reviews`
   - Use `can_accept_reviews()` method before assignment
   - Balance workload across available coaches

5. **Age Validation**: 
   - Always validate 18+ in forms
   - Use `age` property (calculated from DOB) instead of storing age
   - Respect event age ranges (`min_age`, `max_age`)

#### URL Structure (crush.lu domain)

```
/                           - Landing page
/about/                     - About Crush.lu
/how-it-works/             - How it works page
/signup/                    - User registration
/create-profile/            - Profile creation (after signup)
/profile-submitted/         - Confirmation page
/dashboard/                 - User dashboard
/profile/edit/              - Edit profile
/events/                    - Event list
/events/<id>/               - Event detail
/events/<id>/register/      - Register for event
/events/<id>/cancel/        - Cancel registration
/coach/dashboard/           - Coach dashboard
/coach/review/<id>/         - Review profile submission
/coach/sessions/            - Coach sessions
```

#### Frontend Design

- Bootstrap 5 with custom CSS variables
- Color scheme: Purple (#9B59B6) and Pink (#FF6B9D) gradients
- Responsive design with mobile-first approach
- Card-based layouts for events and profiles
- Custom `.btn-crush-primary` button style with gradient and hover effects
- Emoji-enhanced UX for visual appeal to younger audience
