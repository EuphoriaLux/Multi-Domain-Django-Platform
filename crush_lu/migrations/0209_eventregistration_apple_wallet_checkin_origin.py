# Persist the origin that issued an Apple Wallet ticket's check-in QR, so a
# PassKit rebuild (which has no request) can't flip the QR to another slot.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0208_eventregistration_apple_wallet_auth_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventregistration",
            name="apple_wallet_checkin_origin",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "scheme://host that issued this ticket's check-in QR. A "
                    "check-in token only validates in the environment that "
                    "minted it, and a PassKit rebuild has no request to derive "
                    "the host from — the forwarded webServiceURL points at "
                    "whatever the setting names, which may be a different "
                    "slot. Persisted so rebuilds keep the original check-in "
                    "host."
                ),
                max_length=255,
            ),
        ),
    ]
