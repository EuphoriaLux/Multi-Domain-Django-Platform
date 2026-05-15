from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0141_connection_window_hours"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailpreference",
            name="whatsapp_opt_in",
            field=models.BooleanField(
                default=False,
                help_text="User has opted in to receive WhatsApp notifications",
            ),
        ),
    ]
