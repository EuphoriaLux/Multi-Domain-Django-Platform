from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0126_add_pre_screening_sms_templates"),
    ]

    operations = [
        migrations.AddField(
            model_name="profilesubmission",
            name="screening_call_mode",
            field=models.CharField(
                choices=[
                    ("legacy", "Legacy 5-section"),
                    ("calibration", "Calibration 3-section"),
                ],
                default="legacy",
                help_text="Which call-checklist shape applies to this submission",
                max_length=20,
            ),
        ),
    ]
