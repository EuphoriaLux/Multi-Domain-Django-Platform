from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("hub", "0006_socialpost_source_event"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialpost",
            name="buffer_profile_platforms",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="socialpost",
            name="dispatched_platforms",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
