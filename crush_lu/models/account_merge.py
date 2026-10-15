from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class AccountMerge(models.Model):
    """One duplicate account merged into the account that was kept.

    ``services.account_merge.merge_accounts`` moves what it safely can and
    deliberately leaves the rest on the deactivated duplicate: Premium
    memberships and seat payments (#925 locking), Connect, sparks, gifts,
    safety tools and the lobby. This row is what still ties that history to
    the person, so the kept account's data export can include it and a
    payment the duplicate bought still opens for the account that survives.

    The merge is staff's assertion that both accounts are one person, so the
    duplicate's own data is the keeper's own data. ``merged_by`` is staff
    identity and is never exported.
    """

    duplicate = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        # Erasure anonymises accounts and never deletes them; PROTECT keeps a
        # stock-admin delete from silently cutting the duplicate's history off.
        on_delete=models.PROTECT,
        related_name="account_merge_as_duplicate",
    )
    keeper = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="account_merges_as_keeper",
    )
    merged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    merged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("Account merge")
        verbose_name_plural = _("Account merges")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(duplicate=models.F("keeper")),
                name="account_merge_not_into_itself",
            )
        ]

    def __str__(self):
        return f"User {self.duplicate_id} merged into {self.keeper_id}"

    @classmethod
    def surviving_user_id(cls, user_id):
        """The account ``user_id``'s history now lives on (itself if unmerged).

        Follows a chain (A into B, then B into C gives C). ``merge_accounts``
        refuses to merge into an account that was itself merged away, so a
        chain only forms when a keeper is merged later; the visited set is
        there so a hand-edited cycle stops instead of looping.
        """
        seen = set()
        while user_id is not None and user_id not in seen:
            seen.add(user_id)
            keeper_id = (
                cls.objects.filter(duplicate_id=user_id)
                .values_list("keeper_id", flat=True)
                .first()
            )
            if keeper_id is None:
                return user_id
            user_id = keeper_id
        return user_id

    @classmethod
    def merged_duplicates(cls, user_id):
        """Every merge whose duplicate's history belongs to ``user_id``.

        Transitive (C's list holds B and A after A into B, then B into C),
        oldest first; never includes ``user_id`` itself.
        """
        found = []
        seen = {user_id}
        frontier = [user_id]
        while frontier:
            rows = list(
                cls.objects.filter(keeper_id__in=frontier)
                .exclude(duplicate_id__in=seen)
                .select_related("duplicate")
            )
            frontier = [row.duplicate_id for row in rows]
            seen.update(frontier)
            found.extend(rows)
        return sorted(found, key=lambda row: (row.merged_at, row.pk))
