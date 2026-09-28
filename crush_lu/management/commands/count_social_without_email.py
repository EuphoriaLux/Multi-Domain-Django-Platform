"""Count users with a social login but no allauth ``EmailAddress`` row.

Read-only. Such accounts (legacy or admin-created) pass the social
``pre_login`` verification hold, which only holds a user who has an
unverified row. This command sizes that group before anyone decides what to
do about it (#1059); it writes nothing.
"""

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Count, Exists, OuterRef


class Command(BaseCommand):
    help = (
        "Read-only: count users who have a SocialAccount but no allauth "
        "EmailAddress row, with a breakdown by provider."
    )

    def handle(self, *args, **options):
        users = (
            get_user_model()
            .objects.filter(Exists(SocialAccount.objects.filter(user=OuterRef("pk"))))
            .exclude(Exists(EmailAddress.objects.filter(user=OuterRef("pk"))))
        )
        total = users.count()
        self.stdout.write(f"Users with a social account but no EmailAddress: {total}")
        by_provider = (
            SocialAccount.objects.filter(user__in=users.values("pk"))
            .values("provider")
            .annotate(users=Count("user", distinct=True))
            .order_by("provider")
        )
        for row in by_provider:
            self.stdout.write(f"  {row['provider']}: {row['users']}")
