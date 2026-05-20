"""
Data migration: deactivate the removed algorithm_extended GlobalActivityOption,
seed all standard options so fresh environments (including test DBs) don't rely
solely on the populate_global_activity_options management command.
"""

from django.db import migrations

STANDARD_OPTIONS = [
    # Presentation Style variants (Phase 2)
    {
        "activity_type": "presentation_style",
        "activity_variant": "music",
        "display_name": "With Favorite Music",
        "display_name_fr": "Avec ta musique préférée",
        "description": "Introduce yourself while your favorite song plays in the background",
        "description_fr": "Présente-toi pendant que ta chanson préférée joue en fond sonore",
        "is_active": True,
        "sort_order": 1,
    },
    {
        "activity_type": "presentation_style",
        "activity_variant": "questions",
        "display_name": "5 Predefined Questions",
        "display_name_fr": "5 questions prédéfinies",
        "description": "Answer 5 fun questions about yourself (we provide the questions!)",
        "description_fr": "Réponds à 5 questions amusantes sur toi-même (on fournit les questions !)",
        "is_active": True,
        "sort_order": 2,
    },
    {
        "activity_type": "presentation_style",
        "activity_variant": "picture_story",
        "display_name": "Share Favorite Picture & Story",
        "display_name_fr": "Partage une photo et une histoire",
        "description": "Show us your favorite photo and tell us why it matters to you",
        "description_fr": "Montre ta photo préférée et raconte pourquoi elle compte pour toi",
        "is_active": True,
        "sort_order": 3,
    },
    # Speed Dating Twist variants (Phase 3)
    {
        "activity_type": "speed_dating_twist",
        "activity_variant": "spicy_questions",
        "display_name": "Spicy Questions First",
        "display_name_fr": "Questions piquantes d'abord",
        "description": "Break the ice with bold, fun questions right away",
        "description_fr": "Brise la glace avec des questions audacieuses et amusantes dès le départ",
        "is_active": True,
        "sort_order": 4,
    },
    {
        "activity_type": "speed_dating_twist",
        "activity_variant": "forbidden_word",
        "display_name": "Forbidden Word Challenge",
        "display_name_fr": "Défi du mot interdit",
        "description": "Each pair gets a secret word they can't say during the date",
        "description_fr": "Chaque duo reçoit un mot secret qu'il ne peut pas prononcer pendant le rendez-vous",
        "is_active": True,
        "sort_order": 5,
    },
    {
        "activity_type": "speed_dating_twist",
        "activity_variant": "open_conversation",
        "display_name": "Open Conversation",
        "display_name_fr": "Conversation libre",
        "description": "No rules — just enjoy a natural, free-flowing conversation",
        "description_fr": "Pas de règles — profitez simplement d'une conversation naturelle et libre",
        "is_active": True,
        "sort_order": 6,
    },
    {
        "activity_type": "speed_dating_twist",
        "activity_variant": "theme_based",
        "display_name": "Theme Based Conversation",
        "display_name_fr": "Conversation à thème",
        "description": "Each pair receives a theme to guide and inspire their conversation",
        "description_fr": "Chaque duo reçoit un thème pour guider et inspirer leur conversation",
        "is_active": True,
        "sort_order": 7,
    },
    # Skip option
    {
        "activity_type": "presentation_style",
        "activity_variant": "skip_presentations",
        "display_name": "Skip — Go Straight to Speed Dating",
        "display_name_fr": "Passer — Aller directement au Speed Dating",
        "description": "Vote to skip the presentation round and jump directly into speed dating!",
        "description_fr": "Vote pour passer le tour des présentations et aller directement au speed dating !",
        "is_active": True,
        "sort_order": 10,
    },
]


def replace_algorithm_extended(apps, schema_editor):
    GlobalActivityOption = apps.get_model("crush_lu", "GlobalActivityOption")

    # Deactivate (don't delete — preserves historical vote FK references)
    GlobalActivityOption.objects.filter(activity_variant="algorithm_extended").update(
        is_active=False
    )

    # Upsert all standard options so fresh DBs (e.g. test environments) are
    # fully seeded without depending solely on the management command.
    for option in STANDARD_OPTIONS:
        variant = option.pop("activity_variant")
        GlobalActivityOption.objects.update_or_create(
            activity_variant=variant,
            defaults=option,
        )
        option["activity_variant"] = variant  # restore for idempotency


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
