# Generated manually to fix storage parameter mismatch between dev and production
# Uses callable storage to ensure consistent migration state across all environments

import crush_lu.models.profiles
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0022_add_coach_photo'),
    ]

    operations = [
        # Re-apply the photo fields with callable storage
        # Using a callable (not instance) prevents Django from comparing storage objects
        # between environments, eliminating false "model changes detected" warnings
        migrations.AlterField(
            model_name='crushprofile',
            name='photo_1',
            field=models.ImageField(
                blank=True,
                null=True,
                storage=crush_lu.models.profiles.get_crush_photo_storage,
                upload_to=crush_lu.models.profiles.user_photo_path
            ),
        ),
        migrations.AlterField(
            model_name='crushprofile',
            name='photo_2',
            field=models.ImageField(
                blank=True,
                null=True,
                storage=crush_lu.models.profiles.get_crush_photo_storage,
                upload_to=crush_lu.models.profiles.user_photo_path
            ),
        ),
        migrations.AlterField(
            model_name='crushprofile',
            name='photo_3',
            field=models.ImageField(
                blank=True,
                null=True,
                storage=crush_lu.models.profiles.get_crush_photo_storage,
                upload_to=crush_lu.models.profiles.user_photo_path
            ),
        ),
        # Also fix CrushCoach photo field
        migrations.AlterField(
            model_name='crushcoach',
            name='photo',
            field=models.ImageField(
                blank=True,
                null=True,
                storage=crush_lu.models.profiles.get_crush_photo_storage,
                upload_to=crush_lu.models.profiles.coach_photo_path,
                help_text='Coach profile photo shown to users'
            ),
        ),
    ]
