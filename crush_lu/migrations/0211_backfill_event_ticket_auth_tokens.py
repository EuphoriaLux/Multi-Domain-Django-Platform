# Backfill apple_wallet_auth_token for tickets issued before 0208.
#
# 0208 added the field defaulting to "", so an already-installed ticket only
# gets stamped if it is downloaded or rebuilt again. Until then the resolver
# falls back to the owning profile's token — which is what those packages
# actually carry, so they keep working — but the fallback is fragile: an
# account merge deletes the duplicate profile and moves its registrations to
# the keeper, after which the resolver hands back the keeper's different token
# and every request for that ticket 401s.
#
# Copying the profile token onto the registration makes the invariant true for
# existing data as well as new: a ticket's authentication identity is fixed at
# issue time and cannot be changed by later ownership changes.
#
# NOT recoverable here: tickets issued to attendees with no CrushProfile. The
# pre-0208 builder generated those tokens with secrets.token_hex(16) and never
# persisted them anywhere, so the value exists only inside the installed
# .pkpass. Those passes are already failing in production and can only be fixed
# by the holder re-downloading the ticket.

from django.db import migrations


def backfill_ticket_tokens(apps, schema_editor):
    EventRegistration = apps.get_model("crush_lu", "EventRegistration")
    CrushProfile = apps.get_model("crush_lu", "CrushProfile")

    tokens = dict(
        CrushProfile.objects.exclude(apple_auth_token="")
        .exclude(apple_auth_token=None)
        .values_list("user_id", "apple_auth_token")
    )
    if not tokens:
        return

    pending = []
    stale = (
        EventRegistration.objects.exclude(apple_wallet_ticket_serial="")
        .filter(apple_wallet_auth_token="")
        .only("id", "user_id", "apple_wallet_auth_token")
    )
    for registration in stale.iterator():
        token = tokens.get(registration.user_id)
        if token:
            registration.apple_wallet_auth_token = token
            pending.append(registration)

    if pending:
        EventRegistration.objects.bulk_update(
            pending, ["apple_wallet_auth_token"], batch_size=500
        )


def noop_reverse(apps, schema_editor):
    # Deliberately not clearing the column: after this runs there is no way to
    # tell a backfilled value from one stamped by a real download, and blanking
    # a stamped token would break a working pass.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0210_eventregistration_apple_wallet_language"),
    ]

    operations = [
        migrations.RunPython(backfill_ticket_tokens, noop_reverse),
    ]
