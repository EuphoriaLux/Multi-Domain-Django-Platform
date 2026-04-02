from django.db import migrations, models


def migrate_completed_to_step3(apps, schema_editor):
    """Convert any existing 'completed' completion_status to 'step3'."""
    CrushProfile = apps.get_model("crush_lu", "CrushProfile")
    CrushProfile.objects.filter(completion_status="completed").update(
        completion_status="step3"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0097_add_step4_coach_selected_status"),
    ]

    operations = [
        # First, migrate data: completed -> step3
        migrations.RunPython(
            migrate_completed_to_step3,
            reverse_code=migrations.RunPython.noop,
        ),
        # Then, alter the field choices to remove 'completed'
        migrations.AlterField(
            model_name="crushprofile",
            name="completion_status",
            field=models.CharField(
                choices=[
                    ("not_started", "Not Started"),
                    ("step1", "Step 1: Basic Info Saved"),
                    ("step2", "Step 2: About You Saved"),
                    ("step3", "Step 3: Photos Saved"),
                    ("step4", "Step 4: Coach Selected"),
                    ("submitted", "Submitted for Review"),
                ],
                default="not_started",
                help_text="Track which step user completed",
                max_length=20,
            ),
        ),
    ]
