# Generated migration for auto-detection of updated Azure cost exports

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finops_hub', '0003_add_subscription_id_tracking'),
    ]

    operations = [
        migrations.AddField(
            model_name='costexport',
            name='blob_last_modified',
            field=models.DateTimeField(blank=True, db_index=True, null=True, help_text='Azure blob last modified timestamp for change detection'),
        ),
        migrations.AddField(
            model_name='costexport',
            name='blob_etag',
            field=models.CharField(blank=True, max_length=100, null=True, help_text='Azure blob ETag for detecting content changes'),
        ),
    ]
