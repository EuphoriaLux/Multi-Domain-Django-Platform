from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hub", "0012_partner_and_offers")]
    operations = [
        migrations.AddField(
            model_name="socialpost",
            name="media_type",
            field=models.CharField(
                choices=[("image", "Image"), ("video", "Video")],
                default="image",
                max_length=5,
            ),
        )
    ]
