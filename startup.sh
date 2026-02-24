#!/bin/bash
# startup.sh is used by infra/resources.bicep to automate database migrations and start the application
set -e  # Exit on error

echo "🚀 Starting deployment..."
echo "📍 Working directory: $(pwd)"
echo "🐍 Python version: $(python --version)"

# Note: collectstatic is handled by Oryx during build (SCM_DO_BUILD_DURING_DEPLOYMENT=true)
# Running it here would be redundant and add ~30-60s to startup time

# Run migrations with no-input for faster execution
python manage.py migrate --no-input

# Create cache table for database-backed caching (rate limiting)
# Only needed when Redis is NOT configured (fallback to database cache)
if [ -z "$AZURE_REDIS_CONNECTIONSTRING" ]; then
    echo "📦 Creating cache table if needed (no Redis configured)..."
    python manage.py createcachetable 2>&1 || echo "Cache table already exists or creation skipped"
else
    echo "✅ Redis cache configured - skipping createcachetable"
fi

# Only deploy media/data on initial deployment or when explicitly needed
# Set INITIAL_DEPLOYMENT=true in Azure portal only for first deployment
if [ "$INITIAL_DEPLOYMENT" = "true" ]; then
    echo "📦 Initial deployment - setting up media and data..."
    python manage.py deploy_media_and_data --force-refresh
elif [ "$DEPLOY_MEDIA_AND_DATA" = "true" ]; then
    echo "🚀 Deploying complete media and data setup..."
    python manage.py deploy_media_and_data --force-refresh
fi

# Legacy options (kept for backward compatibility)
if [ "$SYNC_MEDIA_TO_AZURE" = "true" ]; then
    echo "📸 Syncing local media files to Azure Blob Storage..."
    python manage.py sync_media_to_azure
fi

if [ "$POPULATE_SAMPLE_DATA" = "true" ]; then
    echo "🍷 Auto-populating sample data with images..."
    python manage.py populate_with_images --force-refresh
fi

# Clean expired sessions to prevent django_session table bloat
# Sessions expire after SESSION_COOKIE_AGE (14 days) but rows remain until cleaned
echo "🧹 Cleaning expired sessions..."
python manage.py clearsessions

echo "✅ Migrations complete. Starting Gunicorn..."

# Gunicorn settings
# OPTIMIZATION: Increased workers from 2 to 4 for P0v3 plan (2 vCPU, 8GB RAM)
# Formula: (2 * CPU_CORES) + 1 = (2 * 2) + 1 = 5 workers (using 4 for safety)
# Throughput increase: 8 → 16 concurrent requests (2x capacity)
# Access logs disabled - Application Insights captures HTTP requests via OpenTelemetry
# Error logs still go to stdout for Log Stream visibility
gunicorn --workers 4 --threads 4 --timeout 120 \
    --access-logfile /dev/null --error-logfile '-' --bind=0.0.0.0:8000 \
    azureproject.wsgi
