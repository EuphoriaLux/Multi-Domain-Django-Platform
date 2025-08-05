# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Entreprinder is a Django web application with multiple domains/apps that serves as a platform for entrepreneur networking and wine commerce. The project is designed to run on Azure App Service with PostgreSQL and includes three main applications:

1. **Entreprinder** - Core entrepreneur networking platform with Tinder-like matching
2. **Matching** - Handles the matching logic for entrepreneurs
3. **VinsDelux** - Wine e-commerce platform with producers, products, and adoption plans

## Architecture

- **Framework**: Django 5.1 with Python
- **Database**: SQLite for development, PostgreSQL for production
- **Authentication**: Django Allauth with LinkedIn OAuth2 integration
- **Frontend**: Bootstrap 5 with crispy forms
- **File Storage**: Local filesystem for development, Azure Blob Storage for production
- **Internationalization**: Supports English, German, and French
- **API**: Django REST Framework with JWT authentication

## Key Applications

### Entreprinder App
- User profiles with LinkedIn integration
- Industry and skill models for categorization
- Entrepreneur matching system
- Profile management with photo uploads

### Matching App
- Like/Dislike functionality
- Match creation when mutual likes occur
- Swipe interface for entrepreneur discovery

### VinsDelux App
- Wine producer profiles
- Product catalog with image galleries
- Adoption plans for wine subscriptions
- User address management

## Development Commands

### Setup
```bash
# Install dependencies
python -m pip install -r requirements.txt

# Load environment variables (development only)
# The app automatically loads .env file when not on Azure

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Start development server
python manage.py runserver
```

### Database
```bash
# Make migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Reset database (caution: data loss)
python manage.py migrate --run-syncdb
```

### Static Files
```bash
# Collect static files (for production)
python manage.py collectstatic
```

### Management Commands
```bash
# Create test entrepreneur profiles
python manage.py create_test_profiles

# Generate dummy data for VinsDelux
python manage.py generate_dummy_data

# Sync media to Azure (production)
python manage.py sync_media_to_azure
```

## URL Structure

The project uses internationalization patterns with language prefixes:
- `/healthz/` - Health check endpoint
- `/accounts/` - Authentication (outside i18n)
- `/{lang}/admin/` - Django admin
- `/{lang}/` - Entreprinder home
- `/{lang}/matching/` - Matching interface
- `/{lang}/vinsdelux/` - Wine platform
- `/{lang}/vibe-coding/` - Additional app

## Environment Configuration

The application automatically detects the environment:
- **Local Development**: Uses `azureproject.settings` and loads `.env` file
- **Azure Production**: Uses `azureproject.production` when `WEBSITE_HOSTNAME` is set

Key environment variables:
- `SECRET_KEY` - Django secret key
- `AZURE_ACCOUNT_NAME` - Azure storage account
- `AZURE_ACCOUNT_KEY` - Azure storage key
- `EMAIL_HOST_*` - SMTP configuration for emails

## Model Architecture

### Core Models
- `User` (Django built-in) - Base user authentication
- `EntrepreneurProfile` - Extended user profile with business info
- `Industry` & `Skill` - Categorization models
- `Match`, `Like`, `Dislike` - Matching system models

### VinsDelux Models
- `VdlUserProfile` - Wine platform user extensions
- `VdlAddress` - Address management
- Product and producer models (see vinsdelux/models.py)

## Key Features

- **Multi-language Support**: German, French, English
- **LinkedIn OAuth**: Automatic profile photo import
- **Swipe Interface**: Mobile-friendly entrepreneur matching
- **Azure Integration**: Blob storage for media files
- **Email System**: SMTP configuration for notifications
- **Admin Interface**: Customized for profile management

## Testing

Test files are located in each app's `tests.py`. The project follows Django's standard testing framework.

## Deployment

The project is configured for Azure App Service deployment using:
- `azure.yaml` - Azure Developer CLI configuration
- `infra/` directory - Bicep infrastructure templates
- `startup.sh` - Azure startup script
- WhiteNoise for static file serving in production