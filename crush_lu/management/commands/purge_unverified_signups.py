"""Delete email signups that never got past email confirmation.

Built for the 2026-11 signup bot wave: a bot created accounts with strangers'
email addresses, then kept logging in to them, and every login of an
unverified account sends the confirmation email again. Deleting those accounts
stops the re-sends and clears them out of the admin.

An account is only selected when ALL of these hold:

* joined in the ``--since`` / ``--until`` window,
* not staff and not superuser,
* never logged in (``last_login`` is empty: unverified accounts can't),
* no verified email address,
* no Crush profile, no social login and no event registration.

Dry-run by default: it prints how many accounts match, the email domains and a
few masked addresses. Pass ``--apply`` to delete.

Usage:
    python manage.py purge_unverified_signups --since 2026-11-19T17:00:00Z
    python manage.py purge_unverified_signups --since 2026-11-19T17:00:00Z --apply
"""

from collections import Counter
from datetime import UTC

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from crush_lu.models import CrushProfile, EventRegistration

BATCH_SIZE = 200


def _parse_when(value, flag):
    when = parse_datetime(value)
    if when is None:
        raise CommandError(f"{flag}: not an ISO date-time: {value!r}")
    if timezone.is_naive(when):
        when = timezone.make_aware(when, UTC)
    return when


def _mask(email):
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "-"
    return f"{local[:1]}***@{domain}"


def unverified_signups(since, until=None):
    """The accounts this command would delete (see the module docstring)."""
    User = get_user_model()
    qs = User.objects.filter(
        date_joined__gte=since,
        is_staff=False,
        is_superuser=False,
        last_login__isnull=True,
    )
    if until is not None:
        qs = qs.filter(date_joined__lt=until)
    return (
        qs.annotate(
            _verified=Exists(
                EmailAddress.objects.filter(user=OuterRef("pk"), verified=True)
            ),
            _profile=Exists(CrushProfile.objects.filter(user=OuterRef("pk"))),
            _social=Exists(SocialAccount.objects.filter(user=OuterRef("pk"))),
            _registered=Exists(EventRegistration.objects.filter(user=OuterRef("pk"))),
        )
        .filter(_verified=False, _profile=False, _social=False, _registered=False)
        .order_by("pk")
    )


class Command(BaseCommand):
    help = "Delete email signups that never confirmed their email (dry-run by default)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            required=True,
            help="Only accounts that joined at or after this ISO date-time (UTC if no offset).",
        )
        parser.add_argument(
            "--until",
            help="Only accounts that joined before this ISO date-time (default: now).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete. Without it, only report what would be deleted.",
        )

    def handle(self, *args, **options):
        since = _parse_when(options["since"], "--since")
        until = _parse_when(options["until"], "--until") if options["until"] else None

        qs = unverified_signups(since, until)
        rows = list(qs.values_list("pk", "email"))
        domains = Counter(
            (email or "").rpartition("@")[2].lower() or "-" for _, email in rows
        )

        self.stdout.write(
            f"{len(rows)} unverified signup(s) since {since.isoformat()}"
            + (f" until {until.isoformat()}" if until else "")
        )
        for domain, count in domains.most_common(15):
            self.stdout.write(f"  {count:5d}  {domain}")
        for _, email in rows[:10]:
            self.stdout.write(f"  e.g. {_mask(email)}")

        if not options["apply"]:
            self.stdout.write(
                "Dry run: nothing deleted. Re-run with --apply to delete."
            )
            return

        User = get_user_model()
        pks = [pk for pk, _ in rows]
        deleted = 0
        for start in range(0, len(pks), BATCH_SIZE):
            chunk = pks[start : start + BATCH_SIZE]
            with transaction.atomic():
                # Re-check inside the transaction: an account that confirmed
                # its email or built a profile since the listing is kept.
                still = unverified_signups(since, until).filter(pk__in=chunk)
                ids = list(still.values_list("pk", flat=True))
                User.objects.filter(pk__in=ids).delete()
                deleted += len(ids)
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} account(s)."))
