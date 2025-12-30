# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## General Development Guidelines

**IMPORTANT - Documentation Policy**:
- **DO NOT** automatically create or update documentation files (README.md, docs/, etc.) after completing tasks
- **DO NOT** proactively suggest creating documentation unless explicitly requested
- Only create/update documentation when the user specifically asks for it
- Focus on writing working code, not documentation, unless instructed otherwise
- If asked to document something, be concise and only document what's necessary

## Development Agents

Specialized Claude agents are defined in `.claude/agents/` for common development tasks:

- **api-expert** - REST API development with Django REST Framework
- **azure-deployment-expert** - Azure infrastructure and deployment
- **css-expert** - Styling, Tailwind CSS, responsive design
- **database-expert** - Database modeling and query optimization
- **django-expert** - Django backend development
- **email-template-expert** - HTML email templates
- **javascript-expert** - Frontend JavaScript, HTMX, Alpine.js
- **migration-expert** - Data migrations and version upgrades
- **security-expert** - Security reviews and authentication
- **testing-expert** - Unit tests, Playwright browser tests
- **visual-ui-debugger** - Analyzing screenshots for UI issues

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
- **Frontend**: Tailwind CSS, HTMX, Alpine.js, Crispy Forms
- **Storage**: Local filesystem (dev), Azure Blob Storage (prod)
- **API**: Django REST Framework with JWT authentication
- **Deployment**: Azure App Service with WhiteNoise for static files
- **Code Style**: Black/Ruff with 88 character line length (configured in `pyproject.toml`)

## Frontend Stack

- **Tailwind CSS**: Custom configuration in `tailwind.config.js` with Crush.lu design tokens
  - Custom colors: `crush-purple`, `crush-pink`, `crush-dark`, `crush-light`
  - Custom border radius: `crush-sm`, `crush-md`, `crush-lg`, `crush-pill`
  - Custom shadows: `crush-purple`, `crush-pink`, `crush-sm`, `crush-md`, `crush-lg`
  - Plugins: `@tailwindcss/forms`, `@tailwindcss/typography`
- **HTMX**: Progressive enhancement for dynamic content loading without full page reloads
- **Alpine.js**: Lightweight JavaScript framework for interactive UI components
- **Crispy Forms**: Django form rendering with Tailwind CSS template pack

CSS files:
- Input: `static/crush_lu/css/tailwind-input.css`
- Output: `static/crush_lu/css/tailwind.css`

## Development Commands

### Essential Commands
```bash
# Run development server
python manage.py runserver

# Run all tests with pytest
pytest

# Run specific test file
pytest crush_lu/tests/test_models.py

# Run single test
pytest crush_lu/tests/test_models.py::TestCrushProfile::test_age_calculation

# Run tests with Playwright browser automation
pytest -m playwright

# Run only fast tests (skip Playwright)
pytest -m "not playwright"
```

### Frontend Development (Tailwind CSS)
```bash
# Install Node.js dependencies
npm install

# Build Tailwind CSS for production
npm run build:css

# Watch mode for Tailwind CSS development
npm run watch:css
```

### Code Formatting
```bash
# Format with black
black .

# Lint with ruff
ruff check .
ruff check . --fix
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

# Create Wonderland journey (interactive experience)
python manage.py create_wonderland_journey

# Populate global activity options for event voting
python manage.py populate_global_activity_options
```

## URL Architecture

The application uses domain-based routing via `DomainURLRoutingMiddleware` (`azureproject/middleware.py`):
- `vinsdelux.com` → VinsDelux app (`azureproject/urls_vinsdelux.py`)
- `powerup.lu` → PowerUP variant (`azureproject/urls_powerup.py`)
- `crush.lu` → Crush.lu dating platform (`azureproject/urls_crush.py`)
- `localhost` → Crush.lu (development default)
- `*.azurewebsites.net` → PowerUP (Azure hostname)
- Default → PowerUP (`azureproject/urls_default.py`)

All apps support i18n with language prefixes (`/en/`, `/de/`, `/fr/`). See the **Internationalization (i18n) System** section below for detailed implementation.

Key endpoints:
- `/healthz/` - Health check (no i18n)
- `/accounts/` - Authentication (no i18n)
- `/{lang}/admin/` - Django admin
- `/journey/plot-selection/` - VinsDelux plot selector (direct access)
- `/vinsdelux/api/adoption-plans/` - API endpoint (direct access)

## High-Level Code Structure

### Domain Routing System

The application uses multiple middleware components to handle multi-domain architecture and production requirements.

#### DomainURLRoutingMiddleware
(`azureproject/middleware.py`)

Dynamically sets `request.urlconf` based on the HTTP host, allowing different URL configurations per platform while sharing the same codebase:
- Inspects `request.get_host()` to determine domain
- Sets appropriate `request.urlconf` for each domain
- Logs routing decisions for debugging
- Handles Azure App Service hostnames (`*.azurewebsites.net`)
- Routes `localhost` to Crush.lu for development

#### HealthCheckMiddleware
(`azureproject/middleware.py`)

**Must be placed FIRST in MIDDLEWARE list** to bypass all middleware for health checks:
- Immediately returns HTTP 200 for `/healthz/` endpoint
- Prevents Azure health checks from failing due to missing Site objects
- Bypasses Sites framework, authentication, and all other middleware
- Critical for Azure App Service health monitoring

#### RedirectWWWToRootDomainMiddleware
(`azureproject/redirect_www_middleware.py`)

Redirects `www.` subdomains to root domains with 301 permanent redirects:
- `www.powerup.lu` → `powerup.lu`
- `www.vinsdelux.com` → `vinsdelux.com`
- `www.crush.lu` → `crush.lu`
- Skips redirect for `/healthz/` endpoint
- Preserves path and query parameters during redirect

#### AzureInternalIPMiddleware
(`azureproject/redirect_www_middleware.py`)

Handles Azure internal IPs for Application Insights:
- Allows `169.254.*.*` (Azure internal IPs) to bypass ALLOWED_HOSTS
- Dynamically adds internal hosts to ALLOWED_HOSTS
- Required for OpenTelemetry autoinstrumentation health checks
- Prevents DisallowedHost errors in production

#### ForceAdminToEnglishMiddleware
(`azureproject/middleware.py`)

Forces Django admin interface to English:
- Activates English translation for all `/admin/` paths
- Sets `request.LANGUAGE_CODE = 'en'`
- Ensures consistent admin experience regardless of user language preference

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

# Email - Option 1: SMTP (traditional)
EMAIL_HOST=<smtp-server>
EMAIL_HOST_USER=<email-user>
EMAIL_HOST_PASSWORD=<email-password>
EMAIL_PORT=587
EMAIL_USE_TLS=True

# Email - Option 2: Microsoft Graph API (recommended for Microsoft 365)
GRAPH_TENANT_ID=<azure-ad-tenant-id>
GRAPH_CLIENT_ID=<app-registration-client-id>
GRAPH_CLIENT_SECRET=<app-registration-secret>
GRAPH_FROM_EMAIL=noreply@crush.lu
```

### Email Backend Configuration

The application supports two email backends:

**1. Microsoft Graph API Backend** (Recommended for production)
- Modern approach for Microsoft 365 email
- More reliable than SMTP for Azure-hosted apps
- Uses app-only authentication (client credentials flow)
- Implemented in `azureproject/graph_email_backend.py`
- Requires MSAL library and Azure AD app registration
- Set in production: `EMAIL_BACKEND = 'azureproject.graph_email_backend.GraphEmailBackend'`

**2. SMTP Backend** (Standard Django)
- Traditional SMTP email sending
- Works with any SMTP server
- Set in development: `EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'`

**Console Backend** (Development only)
- Outputs emails to console instead of sending
- Useful for local testing without email server
- Set: `EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'`

## Testing Strategy

- **Test Runner**: pytest with pytest-django and pytest-playwright
- **Unit Tests**: Each app has `tests/` directory for model and view tests
- **Browser Tests**: Playwright-based tests for frontend interactions
- **CI/CD**: GitHub Actions runs tests on Python 3.10 and 3.11 with PostgreSQL
- **Configuration**: `pytest.ini` and `conftest.py` for shared fixtures

### pytest Fixtures (conftest.py)

Key fixtures available for Crush.lu tests:
- `test_user` - Basic authenticated user
- `test_user_with_profile` - User with approved CrushProfile
- `coach_user` - User with CrushCoach privileges
- `sample_event` - Published MeetupEvent 7 days in future
- `event_with_registrations` - Event with confirmed registrations
- `connection_pair` - Two users with mutual EventConnection
- `unapproved_user` - User pending profile approval
- `authenticated_page` - Playwright page with logged-in session

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
- `MeetupEvent`: Speed dating and social events with invitation system
- `EventRegistration`: User event RSVPs with waitlist support
- `EventInvitation`: Private invitation system for exclusive events
- `EventConnection`: Post-event mutual connections between users
- `ConnectionMessage`: Direct messaging after mutual event connections
- `CoachSession`: Coach-user interaction tracking
- `JourneyConfiguration`: Interactive multi-chapter journey experiences
- `JourneyChapter`: Individual chapters within a journey
- `JourneyChallenge`: Interactive challenges (riddles, puzzles, etc.)
- `JourneyReward`: Unlockable rewards (poems, photos, letters)
- `JourneyProgress`: User progress tracking through journeys
- `SpecialUserExperience`: Personalized experiences linked to users

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

6. **Journey System**:
   - Journeys linked via `SpecialUserExperience` (1-to-1 with User)
   - Always maintain chapter ordering (`chapter_number` field)
   - Challenge answers validated server-side (never trust client)
   - Rewards unlock only when chapter requirements met
   - Use `JourneyProgress` model to track user state
   - JSON fields store completed challenges and unlocked rewards

7. **Invitation System**:
   - Check `is_private_invitation` before displaying event publicly
   - Validate `invitation_token` for external guest access
   - Check `invitation_expires_at` before accepting invitations
   - Convert external guests to full users on acceptance
   - Send appropriate email template based on user status

8. **Connection System**:
   - Only show attendee lists after event completion
   - Enforce mutual connection requirement
   - Messages only between users with "connected" status
   - Respect privacy settings in connection displays
   - Track `is_read` status for message notifications

#### URL Structure (crush.lu domain)

```
/                                          - Landing page
/about/                                    - About Crush.lu
/how-it-works/                            - How it works page
/signup/                                   - User registration
/create-profile/                           - Profile creation (after signup)
/profile-submitted/                        - Confirmation page
/dashboard/                                - User dashboard
/profile/edit/                             - Edit profile

# Events
/events/                                   - Event list
/events/<id>/                              - Event detail
/events/<id>/register/                     - Register for event
/events/<id>/cancel/                       - Cancel registration
/events/<id>/attendees/                    - View attendees (post-event)
/events/<id>/vote/                         - Vote on event activities
/events/<id>/presentations/                - Event presentations (coach control)

# Connections (post-event)
/connections/                              - View mutual connections
/connections/<id>/                         - Connection detail with messaging

# Journey System
/journey/                                  - Journey map/overview
/journey/chapter/<num>/                    - Chapter view
/journey/chapter/<num>/challenge/<id>/     - Interactive challenge
/journey/reward/<id>/                      - View reward (poem, photo, letter)
/journey/certificate/                      - Journey completion certificate

# Coach Panel
/coach/dashboard/                          - Coach dashboard
/coach/review/<id>/                        - Review profile submission
/coach/sessions/                           - Coach sessions
/coach/journeys/                           - Journey management
/coach/journeys/<id>/edit/                 - Edit journey
/coach/journeys/challenge/<id>/edit/       - Edit challenge
/coach/journeys/progress/<id>/             - View user progress
/coach/invitations/                        - Manage event invitations
/coach/screening/                          - Event screening dashboard

# API Endpoints
/api/journey/submit-challenge/             - Submit challenge answer
/api/journey/unlock-hint/                  - Unlock challenge hint
/api/journey/progress/                     - Get journey progress
```

#### Journey System Architecture

The Journey System is an interactive storytelling and gamification feature that creates personalized, multi-chapter experiences for users (e.g., Alice in Wonderland themed journey).

**Data Model Flow**:
```
SpecialUserExperience (1-to-1 with User)
    └── JourneyConfiguration
        └── JourneyChapter (ordered by chapter_number)
            ├── JourneyChallenge (interactive puzzles)
            │   └── Types: riddle, multiple_choice, word_scramble,
            │       timeline_sort, would_you_rather, open_text
            └── JourneyReward (unlockable content)
                └── Types: poem, photo_reveal, future_letter

JourneyProgress (tracks user's journey state)
    ├── current_chapter
    ├── completed_challenges (JSON)
    ├── unlocked_rewards (JSON)
    ├── completed (boolean)
    └── completed_at (timestamp)
```

**Key Features**:
- **Progressive Unlocking**: Chapters unlock sequentially as challenges are completed
- **Hint System**: Users can unlock hints (tracked via API)
- **Multiple Challenge Types**: Riddles, puzzles, interactive games
- **Reward System**: Poems, photo reveals, personalized letters
- **Certificate Generation**: Completion certificate for finished journeys
- **Coach Management**: Coaches can create/edit journeys and track user progress

**Implementation Files**:
- Models: `crush_lu/models.py` (Journey* classes)
- Views: `crush_lu/views_journey.py`
- API: `crush_lu/api_journey.py`
- Templates: `crush_lu/templates/crush_lu/journey/`
- Challenge Templates: `crush_lu/templates/crush_lu/journey/challenges/`
- Reward Templates: `crush_lu/templates/crush_lu/journey/rewards/`

#### Event Invitation System

Crush.lu supports both public and private invitation-only events.

**Private Event Features**:
- `is_private_invitation` flag on `MeetupEvent`
- `invitation_code` for general access to private events
- `invitation_expires_at` for time-limited invitations
- `special_users` ManyToMany field for direct invitations to existing users

**EventInvitation Model**:
- Tracks individual invitations to external guests (non-users)
- Status workflow: pending → accepted/declined/expired
- `is_external_guest` flag for non-registered users
- External guests provide: email, first_name, phone
- Generates unique `invitation_token` for secure access
- Converts to user registration when accepted

**Invitation Flow**:
1. Coach creates private event with invitation settings
2. Coach sends invitations via `/coach/invitations/`
3. Guests receive email with invitation link and token
4. External guests land on invitation acceptance page
5. System creates user account + profile for accepted invitations
6. Auto-registers new user for the event

**Email Notifications**:
- `existing_user_invitation.html` - For registered users
- `external_guest_invitation.html` - For non-users
- `invitation_approved.html` - Confirmation of acceptance
- `invitation_rejected.html` - Declined invitation

#### Post-Event Connection System

After events, users can form mutual connections and communicate.

**EventConnection Model**:
- Links two users who attended the same event
- Status: pending → connected/declined
- Mutual interest required (both users must connect)
- `connected_at` timestamp tracking
- Associated with the `MeetupEvent` where they met

**ConnectionMessage Model**:
- Direct messaging between connected users
- `sender` and `recipient` fields
- `is_read` tracking for unread messages
- Belongs to an `EventConnection`
- Messages only visible if connection is mutual

**Connection Flow**:
1. After event, users can view attendees list (`/events/<id>/attendees/`)
2. Users can request connections with other attendees
3. If mutual interest, status changes to "connected"
4. Connected users can exchange messages via `/connections/<id>/`
5. Dashboard shows all connections and unread message count

**Privacy Considerations**:
- Attendee lists only visible post-event
- Connection requests respect privacy settings
- Messages only between mutually connected users
- Display names follow user's privacy preferences

#### Coach Admin Panel

Custom Django admin integration for Crush coaches with analytics dashboard.

**Admin Dashboard** (`crush_lu/admin_views.py:crush_admin_dashboard`):
- Accessible via custom admin template override
- Comprehensive analytics across all platform areas:
  - User metrics (total, active, approved, pending)
  - Event metrics (upcoming, registrations, attendance rates)
  - Connection metrics (total connections, messages, response rates)
  - Journey metrics (active journeys, completion rates, challenge performance)
  - Coach workload (pending reviews, session counts)

**Custom Admin Templates**:
- `crush_lu/templates/admin/index.html` - Dashboard integration
- `crush_lu/templates/admin/crush_lu/` - Model-specific admin pages
- Override standard Django admin with Crush.lu branding

**Access Control**:
- Only active `CrushCoach` users and superusers
- Non-coach users receive 403 Forbidden
- Dashboard respects coach permissions and specializations

#### Email Notification System

Comprehensive email notification system using Django templates.

**Email Templates** (`crush_lu/templates/crush_lu/emails/`):
- `base_email.html` - Base template with Crush.lu branding
- `welcome.html` - Welcome new users
- `profile_submission_confirmation.html` - Profile submitted
- `profile_approved.html` - Profile approved by coach
- `profile_rejected.html` - Profile rejected with feedback
- `profile_revision_request.html` - Revision requested
- `coach_assignment.html` - Coach assigned notification
- `event_registration_confirmation.html` - Event registration confirmed
- `event_reminder.html` - Upcoming event reminder
- `event_waitlist.html` - Moved to/from waitlist
- `event_cancellation.html` - Event cancelled

**Email Helpers** (`crush_lu/email_helpers.py`, `crush_lu/email_notifications.py`):
- Centralized email sending functions
- Template rendering with context
- HTML and plain text versions
- Integration with Django's email backend
- Support for Microsoft Graph email backend (production)

#### Frontend Design

- Tailwind CSS with custom design tokens (see `tailwind.config.js`)
- Color scheme: Purple (`crush-purple` #9B59B6) and Pink (`crush-pink` #FF6B9D) gradients
- Responsive design with mobile-first approach
- Card-based layouts for events and profiles
- Custom button styles with gradient and hover effects
- Emoji-enhanced UX for visual appeal to younger audience
- HTMX for dynamic content loading without full page reloads
- Alpine.js for interactive UI components
- Custom CSS in `static/crush_lu/css/` for journey, admin, and event styling

## Internationalization (i18n) System

### Supported Languages
- English (`en`) - Default
- German (`de`)
- French (`fr`)

### URL Structure
All Crush.lu pages use language-prefixed URLs with `i18n_patterns()`:
- `https://crush.lu/en/events/` - English
- `https://crush.lu/de/events/` - German
- `https://crush.lu/fr/events/` - French

**Language-neutral paths** (no prefix):
- `/healthz/` - Health check endpoint
- `/accounts/` - Authentication (Allauth)
- `/api/` - API endpoints
- `/robots.txt`, `/sitemap.xml`

### Configuration Files
- **Settings**: `azureproject/settings.py` - `LANGUAGES`, `LOCALE_PATHS`, `USE_I18N`
- **URL Routing**: `azureproject/urls_crush.py` - `i18n_patterns()` wrapper
- **Middleware**: `LocaleMiddleware` in `MIDDLEWARE` stack

### Translation Commands
```bash
# Extract strings for translation (run from project root)
python manage.py makemessages -l de -l fr --ignore=venv --ignore=node_modules

# Compile translations after editing .po files
python manage.py compilemessages
```

### Translation File Locations
- `locale/de/LC_MESSAGES/django.po` - German translations
- `locale/fr/LC_MESSAGES/django.po` - French translations

### Adding New Translatable Strings

**In Templates:**
```html
{% load i18n %}
<h1>{% trans "Welcome" %}</h1>
{% blocktrans with name=user.first_name %}Hello, {{ name }}!{% endblocktrans %}
```

**In Python (views, forms):**
```python
from django.utils.translation import gettext_lazy as _
messages.success(request, _('Profile updated successfully!'))
```

### Language Switcher
Located in navigation bar (`crush_lu/templates/crush_lu/partials/language_switcher.html`):
- Desktop: Alpine.js dropdown with globe icon
- Mobile: Full-width select dropdown
- Uses Django's built-in `set_language` view

### User Language Preference
Stored in `CrushProfile.preferred_language` field for:
- Email notifications in user's preferred language
- Potential future auto-detection on login

### SEO for Multi-Language
- **Canonical URLs**: Self-referencing (each language version is its own canonical)
- **Hreflang Tags**: Generated via `{% hreflang_tags %}` template tag
- **Custom Template Tags**: `crush_lu/templatetags/seo_tags.py`
  - `{% hreflang_tags %}` - Generate all language alternates
  - `{% canonical_url %}` - Current page canonical URL
  - `{% localized_url 'de' %}` - URL for specific language

### GA4 Analytics with Language Tracking
Custom dimension `content_language` prevents traffic fragmentation:
- Added in `azureproject/templatetags/analytics.py`
- Allows unified reporting across language versions
- Filter GA4 reports by language dimension

### Development Best Practices
1. Always wrap user-facing strings with `_()` or `{% trans %}`
2. Use `gettext_lazy` (`_()`) for class-level strings (forms, models)
3. Use `gettext` for runtime strings (views)
4. Run `makemessages` after adding new translatable strings
5. Keep translations in sync - update .po files for all languages
