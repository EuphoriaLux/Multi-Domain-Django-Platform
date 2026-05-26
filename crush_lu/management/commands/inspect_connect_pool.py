"""
inspect_connect_pool — debug command for Crush Connect M1.

Prints the eligible-pool size and the first ``--limit`` entries for the
given user. Used during M1/M2 to sanity-check the pool definition before
the Daily Drop and user-facing surfaces exist.

Usage::

    python manage.py inspect_connect_pool tom
    python manage.py inspect_connect_pool tom@example.com --limit 25
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from crush_lu.services import get_eligible_pool


User = get_user_model()


class Command(BaseCommand):
    help = "Print the Crush Connect eligible pool for a given user."

    def add_arguments(self, parser):
        parser.add_argument(
            "username_or_email",
            help="Username or email of the user whose pool to inspect",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="Max number of pool entries to print (default: 10)",
        )

    def handle(self, *args, **options):
        ident = options["username_or_email"]
        try:
            user = User.objects.get(username=ident)
        except User.DoesNotExist:
            try:
                user = User.objects.get(email__iexact=ident)
            except User.DoesNotExist:
                raise CommandError(f"No user found for '{ident}'")

        profile = getattr(user, "crushprofile", None)
        membership = getattr(user, "crush_connect_membership", None)
        self.stdout.write(self.style.MIGRATE_HEADING(f"User: {user} (id={user.pk})"))
        if profile is None:
            self.stdout.write(self.style.WARNING("  no CrushProfile"))
        else:
            self.stdout.write(
                f"  approved={profile.is_approved}  "
                f"gender={profile.gender or '-'}  "
                f"age={profile.age or '-'}  "
                f"pref_genders={profile.preferred_genders or '-'}  "
                f"pref_age=[{profile.preferred_age_min}, {profile.preferred_age_max}]"
            )
        if membership is None:
            self.stdout.write(self.style.WARNING("  no CrushConnectMembership (not onboarded)"))
        else:
            self.stdout.write(
                f"  onboarded={'YES' if membership.is_onboarded else 'NO'}  "
                f"excluded_by_coach={membership.excluded_by_coach}  "
                f"last_login={user.last_login.isoformat() if user.last_login else '-'}"
            )

        pool = get_eligible_pool(user)
        total = pool.count()
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Eligible pool size: {total}"))

        if total == 0:
            return

        limit = max(0, options["limit"])
        self.stdout.write(self.style.MIGRATE_HEADING(f"First {min(limit, total)} entries:"))
        for u in pool[:limit]:
            p = getattr(u, "crushprofile", None)
            line = f"  #{u.pk}  {u.username}"
            if p:
                line += f"  gender={p.gender or '-'}  age={p.age or '-'}"
            self.stdout.write(line)
