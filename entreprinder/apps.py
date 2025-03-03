# entreprinder/apps.py
from django.apps import AppConfig

class EntreprinderConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'entreprinder'
    
    def ready(self):
        # Import and register without custom adapter for now
        pass