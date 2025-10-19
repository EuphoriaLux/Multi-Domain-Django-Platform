# startup.sh is used by infra/resources.bicep to automate database migrations and isn't used by the sample application
echo "🚀 Starting deployment..."

# Run migrations with no-input for faster execution
python manage.py migrate --no-input

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

# Optimized Gunicorn settings for faster startup and better performance
gunicorn --workers 2 --threads 4 --timeout 120 --access-logfile \
    '-' --error-logfile '-' --bind=0.0.0.0:8000 \
    --preload \
    --chdir=/home/site/wwwroot azureproject.wsgi