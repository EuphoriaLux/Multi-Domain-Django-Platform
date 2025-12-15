# Generated manually to fix storage parameter mismatch between dev and production
# Uses lazy storage object to ensure consistent migration state

import crush_lu.models.profiles
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0022_add_coach_photo'),
    ]

    operations = [
        # Re-apply the photo fields with lazy storage object
        # Using a lazy object ensures the migration state is identical in all environments
        # because Django stores the object reference, not the evaluated storage
        migrations.AlterField(
            model_name='crushprofile',
            name='photo_1',
            field=models.ImageField(
                blank=True,
                null=True,
                storage=crush_lu.models.profiles.crush_photo_storage,
                upload_to=crush_lu.models.profiles.user_photo_path
            ),
        ),
        migrations.AlterField(
            model_name='crushprofile',
            name='photo_2',
            field=models.ImageField(
                blank=True,
                null=True,
                storage=crush_lu.models.profiles.crush_photo_storage,
                upload_to=crush_lu.models.profiles.user_photo_path
            ),
        ),
        migrations.AlterField(
            model_name='crushprofile',
            name='photo_3',
            field=models.ImageField(
                blank=True,
                null=True,
                storage=crush_lu.models.profiles.crush_photo_storage,
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
                storage=crush_lu.models.profiles.crush_photo_storage,
                upload_to=crush_lu.models.profiles.coach_photo_path,
                help_text='Coach profile photo shown to users'
            ),
        ),
    ]
