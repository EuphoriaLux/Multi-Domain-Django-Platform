# Remember the language an Apple ticket was issued in, so a PassKit rebuild
# (which carries no locale) does not hand the holder an English pass.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0209_eventregistration_apple_wallet_checkin_origin"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventregistration",
            name="apple_wallet_language",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Language this Apple ticket was issued in. A PassKit "
                    "rebuild carries no locale, so without this the pass would "
                    "come back in English — and an open-event attendee may "
                    "have no CrushProfile whose preference could stand in."
                ),
                max_length=10,
            ),
        ),
    ]
