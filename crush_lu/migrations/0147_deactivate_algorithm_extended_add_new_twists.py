"""
Data migration: deactivate the removed algorithm_extended GlobalActivityOption
and ensure open_conversation / theme_based rows exist and are active.
"""

from django.db import migrations


def replace_algorithm_extended(apps, schema_editor):
    GlobalActivityOption = apps.get_model("crush_lu", "GlobalActivityOption")

    # Deactivate (don't delete — preserves historical vote FK references)
    GlobalActivityOption.objects.filter(activity_variant="algorithm_extended").update(
        is_active=False
    )

    # Upsert open_conversation
    GlobalActivityOption.objects.update_or_create(
        activity_variant="open_conversation",
        defaults={
            "activity_type": "speed_dating_twist",
            "display_name": "Open Conversation",
            "display_name_fr": "Conversation libre",
            "description": "No rules — just enjoy a natural, free-flowing conversation",
            "description_fr": "Pas de règles — profitez simplement d'une conversation naturelle et libre",
            "is_active": True,
            "sort_order": 6,
        },
    )

    # Upsert theme_based
    GlobalActivityOption.objects.update_or_create(
        activity_variant="theme_based",
        defaults={
            "activity_type": "speed_dating_twist",
            "display_name": "Theme Based Conversation",
            "display_name_fr": "Conversation à thème",
            "description": "Each pair receives a theme to guide and inspire their conversation",
            "description_fr": "Chaque duo reçoit un thème pour guider et inspirer leur conversation",
            "is_active": True,
            "sort_order": 7,
        },
    )


def reverse_replace(apps, schema_editor):
    GlobalActivityOption = apps.get_model("crush_lu", "GlobalActivityOption")

    GlobalActivityOption.objects.filter(activity_variant="algorithm_extended").update(
        is_active=True
    )
    GlobalActivityOption.objects.filter(
        activity_variant__in=["open_conversation", "theme_based"]
    ).update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0146_alter_eventactivityoption_activity_variant"),
    ]

    operations = [
        migrations.RunPython(replace_algorithm_extended, reverse_code=reverse_replace),
    ]
