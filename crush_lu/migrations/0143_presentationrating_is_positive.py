from django.db import migrations, models


def map_rating_to_boolean(apps, schema_editor):
    PresentationRating = apps.get_model("crush_lu", "PresentationRating")
    PresentationRating.objects.filter(rating__gte=4).update(is_positive=True)
    PresentationRating.objects.filter(rating__lt=4).update(is_positive=False)


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0142_emailpreference_whatsapp_opt_in"),
    ]

    operations = [
        # Step 1: add nullable column
        migrations.AddField(
            model_name="presentationrating",
            name="is_positive",
            field=models.BooleanField(
                default=False,
                help_text="Whether this person left a positive first impression",
            ),
            preserve_default=False,
        ),
        # Step 2: migrate existing star ratings
        migrations.RunPython(map_rating_to_boolean, migrations.RunPython.noop),
        # Step 3: remove old field
        migrations.RemoveField(
            model_name="presentationrating",
            name="rating",
        ),
    ]
