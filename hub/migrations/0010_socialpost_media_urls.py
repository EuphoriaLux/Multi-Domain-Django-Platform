from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hub", "0009_paymentin_payroll_refund_paymentout")]
    operations = [
        migrations.AddField(
            model_name="socialpost",
            name="media_urls",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
