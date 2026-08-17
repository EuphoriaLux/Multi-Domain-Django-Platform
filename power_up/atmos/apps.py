from django.apps import AppConfig


class AtmosConfig(AppConfig):
    """Atmos — QR bar ordering, part of the Power-Up submodule family."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "power_up.atmos"
    label = "atmos"
    verbose_name = "Atmos Bar Ordering"
