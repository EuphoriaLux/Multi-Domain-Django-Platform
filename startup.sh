# startup.sh is used by infra/resources.bicep to automate database migrations and isn't used by the sample application
python manage.py migrate

# Auto-populate sample data if POPULATE_SAMPLE_DATA environment variable is set
if [ "$POPULATE_SAMPLE_DATA" = "true" ]; then
    echo "🍷 Auto-populating sample data with images..."
    pip install requests Pillow
    python manage.py populate_with_images --force-refresh
fi

gunicorn --workers 2 --threads 4 --timeout 60 --access-logfile \
    '-' --error-logfile '-' --bind=0.0.0.0:8000 \
     --chdir=/home/site/wwwroot azureproject.wsgi