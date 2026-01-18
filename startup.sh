#!/bin/bash
# startup.sh is used by infra/resources.bicep to automate database migrations and start the application
set -e  # Exit on error

echo "🚀 Starting deployment..."
echo "📍 Working directory: $(pwd)"
echo "🐍 Python version: $(python --version)"

# Delete old Oryx tarball cache and manifest to force fresh deployment
# The manifest tells Oryx to look for a tarball - we need to remove both
if [ -f /home/site/wwwroot/output.tar.gz ]; then
    echo "🗑️ Removing old Oryx tarball cache and manifest..."
    rm -f /home/site/wwwroot/output.tar.gz
    rm -f /home/site/wwwroot/oryx-manifest.toml
    echo "⚠️ Tarball and manifest removed - forcing restart..."
    exit 1
fi

# Also check if manifest references a tarball that doesn't exist
if [ -f /home/site/wwwroot/oryx-manifest.toml ] && grep -q "compressedOutput" /home/site/wwwroot/oryx-manifest.toml 2>/dev/null; then
    if [ ! -f /home/site/wwwroot/output.tar.gz ]; then
        echo "🗑️ Removing stale Oryx manifest (references missing tarball)..."
        rm -f /home/site/wwwroot/oryx-manifest.toml
        echo "⚠️ Manifest removed - forcing restart..."
        exit 1
    fi
fi

# Run collectstatic at startup to ensure manifest is generated
# Note: DISABLE_COLLECTSTATIC=true prevents Oryx from running collectstatic during build
# We run it here to generate the manifest after deployment with all files present
echo "📦 Collecting static files..."
python manage.py collectstatic --no-input

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

echo "✅ Setup complete. Starting Gunicorn..."

# Optimized Gunicorn settings for faster startup and better performance
# Note: Access logs disabled to reduce noise. Errors still logged via --error-logfile.
# Application errors are tracked via Django logging and Azure Application Insights.
gunicorn --workers 2 --threads 4 --timeout 120 \
    --access-logfile /dev/null --error-logfile '-' --bind=0.0.0.0:8000 \
    --preload \
    --chdir=/home/site/wwwroot azureproject.wsgi