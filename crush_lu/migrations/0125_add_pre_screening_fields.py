from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0124_add_event_registration_user_status_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="profilesubmission",
            name="pre_screening_responses",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="User-submitted answers to pre-screening questionnaire",
            ),
        ),
        migrations.AddField(
            model_name="profilesubmission",
            name="pre_screening_submitted_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="When the user finalized their pre-screening answers",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="profilesubmission",
            name="pre_screening_version",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Schema version the user answered (0 = not offered yet)",
            ),
        ),
        migrations.AddField(
            model_name="profilesubmission",
            name="pre_screening_readiness_score",
            field=models.IntegerField(
                blank=True,
                help_text="Rule-based 0–10 readiness score, null if no pre-screening",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="profilesubmission",
            name="pre_screening_flags",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Flag identifiers surfaced to the Coach for attention",
            ),
        ),
    ]
