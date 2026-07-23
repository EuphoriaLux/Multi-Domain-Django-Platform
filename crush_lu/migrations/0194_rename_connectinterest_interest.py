from django.db import migrations


class Migration(migrations.Migration):
    """Rename ``ConnectInterest`` → ``Interest``.

    The curated interest taxonomy is becoming cross-product: the classic event
    profile's new ``interests_new`` M2M reuses it alongside Crush Connect
    (Event Identity redesign, spec O5). Renaming now — before that M2M's
    migration history references the old name — avoids a second rename later.

    ``RenameModel`` renames the table and transparently updates every existing
    reference (e.g. ``CrushConnectMembership.interests``); no data migration is
    needed.
    """

    dependencies = [
        ("crush_lu", "0193_meetupevent_crush_lu_meetupevent_duration_within_ceiling"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="ConnectInterest",
            new_name="Interest",
        ),
    ]
