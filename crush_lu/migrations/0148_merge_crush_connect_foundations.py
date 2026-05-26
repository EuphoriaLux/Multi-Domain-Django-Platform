from django.db import migrations


class Migration(migrations.Migration):
    """
    Merge migration: combines the crush-connect foundations branch
    (0142_sparkprompt … 0145_connectmembership_story) with the main branch
    (0142_emailpreference … 0147_deactivate_algorithm_extended_add_new_twists).

    No database operations — both branches touch entirely different tables.
    """

    dependencies = [
        ("crush_lu", "0147_deactivate_algorithm_extended_add_new_twists"),
        ("crush_lu", "0145_connectmembership_story"),
    ]

    operations = []
