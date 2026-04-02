# Generated manually

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0111_quizevent_num_tables_and_generated'),
    ]

    operations = [
        migrations.AlterField(
            model_name='crushprofile',
            name='phone_number',
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=20,
                validators=[
                    django.core.validators.RegexValidator(
                        message='Enter a valid phone number (e.g., +352 621 123 456).',
                        regex='^\\+[\\d\\s\\-().]{7,20}$',
                    )
                ],
            ),
        ),
    ]
