#!/bin/bash
# startup.sh is used by infra/resources.bicep to automate database migrations and start the application
set -e  # Exit on error

echo "🚀 Starting deployment..."
echo "📍 Working directory: $(pwd)"
echo "🐍 Python version: $(python --version)"

# Extract pre-built virtual environment if not already present
if [ ! -d "/antenv" ] && [ -f "antenv.tar.gz" ]; then
    echo "📦 Extracting pre-built virtual environment..."
    tar xzf antenv.tar.gz -C /
    echo "✅ Virtual environment extracted to /antenv"
fi

# Activate virtual environment
if [ -d "/antenv/bin" ]; then
    echo "🔧 Activating virtual environment from /antenv"
    . /antenv/bin/activate
elif [ -d "antenv/bin" ]; then
    echo "🔧 Activating virtual environment from antenv"
    . antenv/bin/activate
fi

# Note: collectstatic is handled during CI/CD build (GitHub Actions workflow)
# The antenv virtual environment and static files are pre-built and included in the deployment zip
# Do NOT run collectstatic here — running it twice causes manifest conflicts

# Run migrations with no-input for faster execution
python manage.py migrate --no-input

# Create cache table for database-backed caching (rate limiting)
# This is idempotent - safe to run on every deployment
echo "📦 Creating cache table if needed..."
python manage.py createcachetable 2>&1 || echo "Cache table already exists or creation skipped"

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

echo "✅ Migrations complete. Starting Gunicorn..."

# Gunicorn + Uvicorn ASGI settings
# OPTIMIZATION: Increased workers from 2 to 4 for P0v3 plan (2 vCPU, 8GB RAM)
# Formula: (2 * CPU_CORES) + 1 = (2 * 2) + 1 = 5 workers (using 4 for safety)
# Using UvicornWorker for ASGI support (HTTP + WebSocket via Django Channels)
# Access logs sent to stderr for Azure Log Stream visibility (minimal format)
# Application Insights also captures requests via OpenTelemetry for full telemetry
gunicorn --workers 4 --timeout 120 \
    -k uvicorn.workers.UvicornWorker \
    --access-logfile '-' --access-logformat '%(h)s %(m)s %(U)s %(s)s %(D)sms' \
    --error-logfile '-' --bind=0.0.0.0:8000 \
    azureproject.asgi:application
