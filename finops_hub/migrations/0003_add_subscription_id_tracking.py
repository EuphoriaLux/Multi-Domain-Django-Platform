# Generated migration for subscription ID tracking feature

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finops_hub', '0002_add_record_hash_deduplication'),
    ]

    operations = [
        migrations.AddField(
            model_name='costexport',
            name='subscription_id',
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='costexport',
            name='needs_subscription_id',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AlterField(
            model_name='costexport',
            name='import_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('processing', 'Processing'),
                    ('completed', 'Completed'),
                    ('failed', 'Failed'),
                    ('superseded', 'Superseded'),
                ],
                db_index=True,
                default='pending',
                max_length=20,
            ),
        ),
    ]
