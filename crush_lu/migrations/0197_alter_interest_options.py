from django.db import migrations


class Migration(migrations.Migration):
    """Neutralize ``Interest``'s admin display name.

    Left over from the ``ConnectInterest`` → ``Interest`` rename (0194): the
    verbose names still said "Connect Interest" even though the taxonomy is
    now cross-product (shared by ``CrushConnectMembership.interests`` and
    ``CrushProfile.interests_new``).
    """

    dependencies = [
        ("crush_lu", "0196_seed_interest_additions"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="interest",
            options={
                "ordering": ["category", "sort_order", "label"],
                "verbose_name": "Interest",
                "verbose_name_plural": "Interests",
            },
        ),
    ]
