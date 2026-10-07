from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("crush_lu", "0267_per_photo_review_state")]

    operations = [
        migrations.AddField(
            model_name="emailsuppression",
            name="scope",
            field=models.CharField(
                choices=[("all", "All email"), ("campaign", "Campaigns only")],
                default="all",
                max_length=12,
            ),
        ),
    ]
