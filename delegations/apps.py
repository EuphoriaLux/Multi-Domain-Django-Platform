from django.apps import AppConfig


class DelegationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'delegations'
    verbose_name = 'Delegations.lu'

    def ready(self):
        """Import signal handlers when the app is ready"""
        import delegations.signals  # noqa
