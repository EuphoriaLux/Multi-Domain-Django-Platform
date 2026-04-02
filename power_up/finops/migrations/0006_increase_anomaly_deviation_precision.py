# Generated manually to fix numeric overflow in anomaly detection
# Increases deviation_percent from max 999.99% to 99,999,999.99%

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finops', '0005_reservationcost'),
    ]

    operations = [
        migrations.AlterField(
            model_name='costanomaly',
            name='deviation_percent',
            field=models.DecimalField(
                decimal_places=2,
                max_digits=10,
                help_text="Percentage deviation from expected cost (supports extreme spikes up to 99,999,999.99%)"
            ),
        ),
    ]
