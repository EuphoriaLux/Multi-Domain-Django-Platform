from django.db import migrations, models


class Migration(migrations.Migration):
    """Curated speed-dating registration, phase 2.

    Purely additive and backwards-compatible: ``registration_mode`` defaults to
    "direct", which is the behaviour every existing event already has, and the
    new "applied" status is only a choices entry — no row is rewritten. An
    event only changes behaviour once an organiser opts it in.
    """

    dependencies = [
        ("crush_lu", "0242_eventregistrationpreference"),
    ]

    operations = [
        migrations.AddField(
            model_name="meetupevent",
            name="registration_mode",
            field=models.CharField(
                choices=[
                    ("direct", "Direct — first come, first served"),
                    ("curated", "Curated — organiser selects the group"),
                ],
                db_index=True,
                default="direct",
                help_text=(
                    "Curated mode applies to speed dating only: sign-ups are "
                    "held as applications that take no seat until an organiser "
                    "selects them."
                ),
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="eventregistration",
            name="status",
            field=models.CharField(
                choices=[
                    ("applied", "Applied — awaiting selection"),
                    ("pending", "Pending Payment"),
                    ("confirmed", "Confirmed"),
                    ("waitlist", "Waitlist"),
                    ("cancelled", "Cancelled"),
                    ("attended", "Attended"),
                    ("no_show", "No Show"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
    ]
