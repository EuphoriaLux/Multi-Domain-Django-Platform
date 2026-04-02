from django.apps import AppConfig


class CrushLuConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crush_lu'
    verbose_name = 'Crush.lu'

    def ready(self):
        """Import signal handlers when the app is ready"""
        import crush_lu.signals  # noqa

        # Apply OAuth statekit patch for cross-browser state persistence
        # This fixes Android PWA OAuth issue where OAuth opens in system browser
        from crush_lu.oauth_statekit import patch_allauth_statekit
        patch_allauth_statekit()
