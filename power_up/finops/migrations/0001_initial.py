# Generated migration for finops app
# This migration acknowledges that the finops tables were already created
# by power_up app migrations (specifically power_up.0002_ensure_finops_tables_exist)

from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("power_up", "0002_ensure_finops_tables_exist"),
    ]

    operations = [
        # No operations needed - tables already exist from power_up migrations
    ]
