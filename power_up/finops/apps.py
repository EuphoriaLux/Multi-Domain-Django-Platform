# power_up/finops/apps.py
from django.apps import AppConfig


class FinopsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "power_up.finops"
    label = "finops"
    verbose_name = "FinOps Hub"
