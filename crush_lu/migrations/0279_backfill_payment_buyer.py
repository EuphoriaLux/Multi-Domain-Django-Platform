"""Stamp ``PaymentTransaction.buyer`` on the rows that predate it.

Same rule as ``PaymentTransaction._evident_buyer_id``, so a backfilled row and
one stamped by ``save`` agree:

1. a linked registration names the buyer (its member);
2. otherwise a linked Premium membership does;
3. otherwise ``user`` does, but only for donations and the MANUAL / CREDIT
   providers, whose creators always pass the member.

What is left -- a SumUp seat or Premium row whose link is gone -- stays NULL:
``user`` there may be the staff account that opened the checkout, and
stamping it would turn a guess into the record. ``owner_id()`` reads those
rows exactly as it did before this field existed.

Separate from 0278 so the column, its index and its constraint commit before
any row is written. Each pass touches only rows still NULL, so a re-run (or
rows the old code creates while a slot swap warms up) is harmless; those
rows get their buyer on their next ``save``. Only PaymentTransaction rows are
locked, never a CrushProfile, so this cannot invert the payment-before-profile
lock order a concurrent capture takes.
"""

from django.db import migrations
from django.db.models import F, OuterRef, Q, Subquery


def backfill_buyer(apps, schema_editor):
    PaymentTransaction = apps.get_model("crush_lu", "PaymentTransaction")
    EventRegistration = apps.get_model("crush_lu", "EventRegistration")
    PremiumMembership = apps.get_model("crush_lu", "PremiumMembership")

    unstamped = PaymentTransaction.objects.filter(buyer__isnull=True)
    unstamped.filter(event_registration__isnull=False).update(
        buyer_id=Subquery(
            EventRegistration.objects.filter(
                pk=OuterRef("event_registration_id")
            ).values("user_id")[:1]
        )
    )
    unstamped.filter(
        event_registration__isnull=True, premium_membership__isnull=False
    ).update(
        buyer_id=Subquery(
            PremiumMembership.objects.filter(
                pk=OuterRef("premium_membership_id")
            ).values("user_id")[:1]
        )
    )
    unstamped.filter(
        event_registration__isnull=True,
        premium_membership__isnull=True,
        user__isnull=False,
    ).filter(Q(purpose="donation") | Q(provider__in=("manual", "credit"))).update(
        buyer_id=F("user_id")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0278_payment_buyer_account_merge"),
    ]

    operations = [
        migrations.RunPython(backfill_buyer, migrations.RunPython.noop),
    ]
