# Generated manually to fix storage parameter mismatch between dev and production

import crush_lu.models.profiles
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0022_add_coach_photo'),
    ]

    operations = [
        # Re-apply the photo fields with explicit storage parameter
        # This ensures the migration state matches the model definition in all environments
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
    ]
