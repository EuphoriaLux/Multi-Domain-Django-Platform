from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("crush_lu", "0263_premium_recovery_resolution")]

    operations = [
        migrations.AddField(
            model_name="oauthstate",
            name="auth_origin_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="oauthstate",
            name="auth_recovery_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
