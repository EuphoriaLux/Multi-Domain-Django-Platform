from django.apps import AppConfig


class CrmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "power_up.crm"
    label = "crm"
    verbose_name = "CRM"
