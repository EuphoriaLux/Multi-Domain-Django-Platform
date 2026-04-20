"""
Celery application for azureproject.

Used by the Hybrid Coach Review System (crush_lu) to run periodic SLA checks,
nudges, fallback offers, escalations, and recontact auto-expiry. Broker and
result backend both share the existing REDIS_URL environment variable used
by Channels and the Django cache.

Deployment (production):
    - Worker:  celery -A azureproject worker -l INFO
    - Beat:    celery -A azureproject beat -l INFO \
                 --scheduler django_celery_beat.schedulers:DatabaseScheduler

Run both processes in separate containers (e.g. Azure Container Apps) pointed
at the same Redis instance. Schedules live in the DB (django-celery-beat),
so ops can tune cadence in admin without redeploying.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "azureproject.settings")

app = Celery("azureproject")

# Pull any CELERY_* settings from Django's settings module.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Discover @shared_task functions in every installed app (e.g. crush_lu).
app.autodiscover_tasks()
