# Generated for: snapshot the currency an order was placed in.

from django.db import migrations, models


def _backfill(apps, schema_editor):
    """The field defaults to "EUR" for every existing row, which is wrong for
    any pre-existing order placed at a non-EUR venue. Backfill from the
    order's own (denormalised) venue FK's CURRENT currency.

    This is a best-effort backfill, NOT an exact historical reconstruction —
    it cannot be, since no prior schema version ever recorded what currency
    an order was actually placed in. Orders never change venue (the FK
    itself is exact), but `Venue.currency` is a live, editable field: if a
    venue's currency was ever changed between an old order's placement and
    whenever this migration runs, that order gets backfilled with the
    venue's currency AT MIGRATION TIME, not at placement time — the same
    class of staleness this field exists to prevent going forward, just
    unavoidable for history predating it. In practice this only matters for
    venues that have actually changed currency, which this prototype has
    none of yet; flagging it here rather than overclaiming exactness.
    """
    Order = apps.get_model("atmos", "Order")
    Venue = apps.get_model("atmos", "Venue")
    currencies = dict(Venue.objects.values_list("id", "currency"))
    for order in Order.objects.exclude(venue_id__isnull=True).only("id", "venue_id"):
        currency = currencies.get(order.venue_id)
        if currency and currency != "EUR":
            order.currency = currency
            order.save(update_fields=["currency"])


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("atmos", "0004_alter_menuitem_price_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="currency",
            field=models.CharField(default="EUR", max_length=3),
        ),
        migrations.RunPython(_backfill, _noop_reverse),
    ]
