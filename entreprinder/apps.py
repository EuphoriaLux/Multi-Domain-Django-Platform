from django.apps import AppConfig

class EntreprinderConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'entreprinder'

    def ready(self):
        import entreprinder.signals  # Import the signals
