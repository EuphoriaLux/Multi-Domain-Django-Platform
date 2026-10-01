from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hub", "0010_socialpost_media_urls")]
    operations = [
        migrations.AddField(
            "socialpost",
            "buffer_delivery_uncertain",
            models.BooleanField(default=False),
        ),
        migrations.AddField(
            "socialpost",
            "generation_key",
            models.CharField(blank=True, max_length=200, null=True, unique=True),
        ),
        migrations.AddField(
            "socialpost", "source_metadata", models.JSONField(blank=True, default=dict)
        ),
    ]
