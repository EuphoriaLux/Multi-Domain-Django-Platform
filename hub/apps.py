from django.apps import AppConfig


class HubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hub"
    verbose_name = "Hub API"

    def ready(self):
        """Register Hub lifecycle handlers after all app models are loaded."""

        import hub.signals  # noqa: F401
