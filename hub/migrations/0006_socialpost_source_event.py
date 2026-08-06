import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crush_lu", "0216_outlook_photo_key"),
        ("hub", "0005_socialpost_featured_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialpost",
            name="source_event",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Published Crush event whose existing copy was reused for this post."
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="social_promotion_posts",
                to="crush_lu.meetupevent",
            ),
        ),
    ]
