# Generated for Apple Wallet event-ticket auth tokens.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0207_paymenttransaction"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventregistration",
            name="apple_wallet_auth_token",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Apple Wallet PassKit auth token for this ticket. Set when "
                    "the ticket is built so the web service can authenticate "
                    "update requests for it, including for attendees with no "
                    "CrushProfile."
                ),
                max_length=64,
            ),
        ),
    ]
