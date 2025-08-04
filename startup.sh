# startup.sh is used by infra/resources.bicep to automate database migrations and isn't used by the sample application
python manage.py migrate

# Complete deployment with media and data
if [ "$DEPLOY_MEDIA_AND_DATA" = "true" ]; then
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
    pip install requests Pillow
    python manage.py populate_with_images --force-refresh
fi

gunicorn --workers 2 --threads 4 --timeout 60 --access-logfile \
    '-' --error-logfile '-' --bind=0.0.0.0:8000 \
     --chdir=/home/site/wwwroot azureproject.wsgi