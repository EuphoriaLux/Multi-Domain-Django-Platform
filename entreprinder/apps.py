from django.apps import AppConfig

class EntreprinderConfig(AppConfig):
    name = 'entreprinder'

    def ready(self):
        import entreprinder.signals
