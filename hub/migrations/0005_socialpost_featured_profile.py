import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crush_lu", "0204_event_reminder_idempotency"),
        ("hub", "0004_socialpost"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialpost",
            name="featured_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="hub_social_posts",
                to="crush_lu.crushprofile",
            ),
        ),
    ]
