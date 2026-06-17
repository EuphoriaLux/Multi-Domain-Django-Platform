from django.db import migrations


class Migration(migrations.Migration):
    """Drop the unused SpeedDatingPair model (see #331).

    Nothing in the codebase created SpeedDatingPair rows; only the admin and the
    TV-display Phase-3 read referenced it, both removed in the same change.
    """

    dependencies = [
        ("crush_lu", "0165_migrate_legacy_traits_to_memberships"),
    ]

    operations = [
        migrations.DeleteModel(
            name="SpeedDatingPair",
        ),
    ]
