from django.apps import AppConfig


class CrushLuConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crush_lu'
    verbose_name = 'Crush.lu'

    def ready(self):
        """Import signal handlers when the app is ready"""
        import crush_lu.signals  # noqa
