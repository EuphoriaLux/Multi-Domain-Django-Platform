from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0144_add_last_minute_sms_templates'),
    ]

    operations = [
        migrations.AddField(
            model_name='globalactivityoption',
            name='display_name_fr',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='globalactivityoption',
            name='description_fr',
            field=models.TextField(blank=True, default=''),
        ),
    ]
