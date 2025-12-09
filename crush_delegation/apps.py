from django.apps import AppConfig


class CrushDelegationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crush_delegation'
    verbose_name = 'Crush Delegation'

    def ready(self):
        """Import signal handlers when the app is ready"""
        import crush_delegation.signals  # noqa
