from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0033_crushprofile_preferred_language'),
    ]

    operations = [
        migrations.AddField(
            model_name='pushsubscription',
            name='device_fingerprint',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Browser fingerprint hash for stable device identification across sessions',
                max_length=64
            ),
        ),
        migrations.AddField(
            model_name='coachpushsubscription',
            name='device_fingerprint',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Browser fingerprint hash for stable device identification across sessions',
                max_length=64
            ),
        ),
    ]
