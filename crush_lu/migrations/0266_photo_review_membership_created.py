from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0265_merge_premium_recovery_resolution"),
    ]

    operations = [
        migrations.AddField(
            model_name="profilephotoreviewlog",
            name="membership_created",
            field=models.BooleanField(default=False),
        ),
    ]
