import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("crush_lu", "0264_oauthstate_auth_recovery_hash")]

    operations = [
        # Do not backdate legacy cards to an invented time, or reset their
        # cooldown to deployment time. Their generated_date remains the anchor.
        migrations.AddField(
            model_name="connectcyclecard",
            name="generated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="connectcyclecard",
            name="generated_at",
            field=models.DateTimeField(
                blank=True, null=True, default=django.utils.timezone.now
            ),
        ),
    ]
