"""
Account merge service for Crush.lu.

Handles merging duplicate user accounts (e.g., when Apple "Hide My Email"
creates a separate account from an existing email-based account).

Transfers all related data from the duplicate account to the keeper account,
handling unique constraints and bidirectional relationships.
"""

import logging
from django.db import transaction
from django.db.models import Q

logger = logging.getLogger(__name__)


@transaction.atomic
def merge_accounts(keeper_user, duplicate_user, admin_user=None):
    """
    Merge duplicate_user's data into keeper_user, then deactivate duplicate_user.

    Args:
        keeper_user: The User to keep (receives all data)
        duplicate_user: The User to merge from (gets deactivated)
        admin_user: The admin performing the merge (for audit logging)

    Returns:
        list[str]: Log of actions taken during the merge
    """
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount
    from crush_lu.models import (
        CrushProfile,
        CuratedEventGroup,
        CuratedEventGroupMembership,
        CuratedEventPairingParticipant,
        EventRegistration,
        EventRegistrationPreference,
        EventConnection,
        ConnectionMessage,
        JourneyProgress,
        PushSubscription,
        PWADeviceInstallation,
        EmailPreference,
        UserDataConsent,
        UserActivity,
        ProfileReminder,
        MeetupEvent,
    )
    from crush_lu.models.referrals import ReferralCode, ReferralAttribution

    log = []

    if keeper_user.id == duplicate_user.id:
        raise ValueError("Cannot merge a user with themselves.")

    logger.info(
        f"[ACCOUNT-MERGE] Starting merge: duplicate {duplicate_user.id} "
        f"({duplicate_user.email}) -> keeper {keeper_user.id} "
        f"({keeper_user.email}) by admin {admin_user}"
    )

    # Payment callbacks and checkout creation use payment -> event -> every
    # event registration -> group state.  Take the same prefix before touching
    # either account so a merge cannot detach a checkout midway through a real
    # capture or delete a certified group member behind the schedule digest.
    from crush_lu.models.payments import (
        EventCheckoutCreationClaim,
        PaymentTransaction,
    )

    account_registration_ids = list(
        EventRegistration.objects.filter(
            user__in=(keeper_user, duplicate_user)
        ).values_list("pk", flat=True)
    )
    locked_payments = list(
        PaymentTransaction.objects.select_for_update(of=("self",))
        .filter(
            Q(user_id__in=(keeper_user.pk, duplicate_user.pk))
            | Q(event_registration_id__in=account_registration_ids)
        )
        .order_by("pk")
    )
    affected_event_ids = list(
        EventRegistration.objects.filter(
            Q(user__in=(keeper_user, duplicate_user))
            | Q(resale_beneficiary__in=(keeper_user, duplicate_user))
        )
        .order_by("event_id")
        .values_list("event_id", flat=True)
        .distinct()
    )
    list(
        MeetupEvent.objects.select_for_update()
        .filter(pk__in=affected_event_ids)
        .order_by("pk")
    )
    locked_event_registrations = list(
        EventRegistration.objects.select_for_update()
        .filter(event_id__in=affected_event_ids)
        .select_related("event")
        .order_by("pk")
    )
    locked_registration_ids = [row.pk for row in locked_event_registrations]
    locked_groups = list(
        CuratedEventGroup.objects.select_for_update()
        .filter(event_id__in=affected_event_ids)
        .order_by("pk")
    )

    if EventCheckoutCreationClaim.objects.filter(
        registration_id__in=locked_registration_ids
    ).exists():
        raise ValueError(
            "Cannot merge these accounts while an event checkout is being "
            "prepared. Retry after it completes or retire the stale claim."
        )

    registrations_by_event_user = {
        (row.event_id, row.user_id): row for row in locked_event_registrations
    }
    payment_status_by_registration = {}
    for payment in locked_payments:
        if payment.event_registration_id:
            payment_status_by_registration.setdefault(
                payment.event_registration_id, set()
            ).add(payment.status)
    derived_group_registration_ids = set(
        CuratedEventGroupMembership.objects.filter(
            registration_id__in=locked_registration_ids,
        ).values_list("registration_id", flat=True)
    )
    derived_group_registration_ids.update(
        CuratedEventPairingParticipant.objects.filter(
            registration_id__in=locked_registration_ids,
        ).values_list("registration_id", flat=True)
    )
    for row in locked_event_registrations:
        if row.user_id != duplicate_user.pk:
            continue
        keeper_registration = registrations_by_event_user.get(
            (row.event_id, keeper_user.pk)
        )
        if keeper_registration is None:
            continue
        if row.pk in derived_group_registration_ids:
            raise ValueError(
                "Cannot collapse duplicate registrations while one belongs to "
                "curated-group history. Preserve or resolve that audited history first."
            )
        statuses = payment_status_by_registration.get(row.pk, set())
        if PaymentTransaction.Status.PENDING in statuses:
            raise ValueError(
                "Cannot collapse duplicate registrations with a pending event "
                "transaction. Resolve the checkout first."
            )
        if row.payment_confirmed:
            raise ValueError(
                "Cannot collapse a duplicate registration that still holds paid "
                "seat value. Return or transfer that value explicitly first."
            )

    # Keep the local name intentionally used: evaluating the row locks is the
    # point even when no conflict is found.
    del locked_groups

    # 1. Move SocialAccounts
    for sa in SocialAccount.objects.filter(user=duplicate_user):
        if not SocialAccount.objects.filter(
            user=keeper_user, provider=sa.provider, uid=sa.uid
        ).exists():
            sa.user = keeper_user
            sa.save(update_fields=["user"])
            log.append(f"Moved {sa.provider} social account to keeper")
        else:
            sa.delete()
            log.append(f"Deleted duplicate {sa.provider} social account")

    # 2. Move EmailAddresses (allauth)
    for ea in EmailAddress.objects.filter(user=duplicate_user):
        if not EmailAddress.objects.filter(user=keeper_user, email=ea.email).exists():
            ea.user = keeper_user
            ea.primary = False  # Keeper's primary email stays
            ea.save(update_fields=["user", "primary"])
            log.append(f"Moved email address {ea.email} to keeper")
        else:
            ea.delete()
            log.append(f"Deleted duplicate email address {ea.email}")

    # 2b. Donation PaymentTransactions follow the surviving account.
    #
    # LOCK ORDER: PaymentTransaction before CrushProfile. This must happen
    # BEFORE the profile handling below, and the reason is a deadlock, not
    # readability. _apply_paid_checkout takes the same two locks in that order
    # -- it locks the transaction with select_for_update, then writes the
    # profile -- and this function taking them the other way round is a
    # textbook ABBA cycle that PostgreSQL resolves by aborting one side. The
    # side it aborts might be the payment callback, mid-confirmation. Keep
    # these two blocks in this order; see the matching note in
    # views_payments._apply_paid_checkout.
    #
    # Doing it first also makes the badge decision below strictly better: a
    # donation confirming concurrently either committed before we lock these
    # rows (so its grant is visible when we read the profiles) or blocks on
    # them until we commit. Either way nobody reads a half-applied payment.
    #
    # Donations ONLY. Seat and membership rows resolve their owner through the
    # object they link to, so they need no help -- and moving them does harm:
    # step 5 below deletes a duplicate registration when the keeper already has
    # one for that event, which SET_NULLs its payment's event_registration. A
    # moved row would then be a pending checkout the keeper owns and can open,
    # but which _apply_paid_checkout can no longer route to a seat -- it would
    # take the money and confirm nothing. Left on the duplicate it is at least
    # unreachable rather than payable.
    #
    # A donation points at nothing -- _payment_owner_ids falls back to tx.user
    # for exactly that shape -- so one left behind is stranded on a deactivated
    # user: a checkout paid during or after the merge looks up a profile that
    # has since been moved or deleted and grants no badge, and the keeper
    # cannot open the widget or return page for a payment they made.
    moved_payments = PaymentTransaction.objects.filter(
        user=duplicate_user, purpose=PaymentTransaction.Purpose.DONATION
    ).update(user=keeper_user)
    if moved_payments:
        log.append(f"Moved {moved_payments} donation transaction(s) to keeper")

    # 3. Handle CrushProfile (OneToOne)
    keeper_profile = getattr(keeper_user, "crushprofile", None)
    dup_profile = getattr(duplicate_user, "crushprofile", None)

    if dup_profile and not keeper_profile:
        # Move duplicate's profile to keeper
        dup_profile.user = keeper_user
        dup_profile.save(update_fields=["user"])
        log.append("Moved CrushProfile from duplicate to keeper")
    elif dup_profile and keeper_profile:
        # Both have profiles - keep keeper's, transfer referral data
        ReferralCode.objects.filter(referrer=dup_profile).update(
            referrer=keeper_profile
        )
        moved_codes = ReferralCode.objects.filter(referrer=keeper_profile).count()
        ReferralAttribution.objects.filter(referrer=dup_profile).update(
            referrer=keeper_profile
        )
        # Supporter status was paid for, so it survives the merge on OR
        # semantics: the keeper's profile is the one that lives, and dropping
        # the flag because the donation happened to land on the duplicate would
        # take away something the member was charged for and can see.
        #
        # Re-read under a lock rather than trusting the instances above. Those
        # come from the caller's cached ``user.crushprofile``, which can be
        # arbitrarily old, and a donation confirming between that load and this
        # line would leave the flag False in memory while it is True in the
        # row -- so the badge would be skipped and then deleted with the
        # profile. Nothing recovers it afterwards: _apply_paid_checkout is
        # idempotent and will not re-apply an already-PAID checkout.
        supporter = dict(
            CrushProfile.objects.select_for_update()
            .filter(pk__in=[dup_profile.pk, keeper_profile.pk])
            .values_list("pk", "is_community_supporter")
        )
        if supporter.get(dup_profile.pk) and not supporter.get(keeper_profile.pk):
            keeper_profile.is_community_supporter = True
            keeper_profile.save(update_fields=["is_community_supporter"])
            log.append("Carried Community Supporter status over to keeper's profile")
        # Delete duplicate's profile (cascades ProfileSubmissions)
        dup_profile.delete()
        log.append(
            f"Kept keeper's profile, transferred {moved_codes} referral codes, "
            f"deleted duplicate's profile"
        )

    # 4. Update ReferralAttributions pointing to duplicate as referred_user
    updated = ReferralAttribution.objects.filter(referred_user=duplicate_user).update(
        referred_user=keeper_user
    )
    if updated:
        log.append(f"Updated {updated} referral attribution(s) pointing to duplicate")

    # 5. EventRegistrations (unique_together: event, user)
    #
    # A late-cancellation resale claim is deliberately carried on the
    # replacement registration as well as on the original payment. Move its
    # beneficiary before deleting either account's duplicate registration;
    # otherwise the source-registration SET_NULL is survivable but the money
    # is still addressed to the deactivated account.
    affected_registrations = list(
        EventRegistration.objects.select_for_update()
        .filter(Q(user=duplicate_user) | Q(resale_beneficiary=duplicate_user))
        .select_related("event")
        .order_by("pk")
    )
    moved_resale_claims = EventRegistration.objects.filter(
        resale_beneficiary=duplicate_user
    ).update(resale_beneficiary=keeper_user)
    if moved_resale_claims:
        log.append(f"Moved {moved_resale_claims} pending resale claim(s) to keeper")

    for reg in affected_registrations:
        if reg.user_id != duplicate_user.pk:
            continue
        if not EventRegistration.objects.filter(
            event=reg.event, user=keeper_user
        ).exists():
            reg.user = keeper_user
            reg.save(update_fields=["user"])
            log.append(f"Moved registration for event '{reg.event}' to keeper")
        else:
            # The keeper's registration wins, but the duplicate's speed-dating
            # preference row would go with it (OneToOne CASCADE) and the
            # organiser would silently lose an answer nobody can re-ask for
            # once selection is under way. Move it over when the keeper's
            # registration has none; when both sides answered, the surviving
            # registration keeps its own answers rather than being overwritten
            # by an application that is about to disappear.
            duplicate_pref = EventRegistrationPreference.objects.filter(
                registration=reg
            ).first()
            if duplicate_pref is not None:
                keeper_reg = EventRegistration.objects.get(
                    event=reg.event, user=keeper_user
                )
                if not EventRegistrationPreference.objects.filter(
                    registration=keeper_reg
                ).exists():
                    duplicate_pref.registration = keeper_reg
                    duplicate_pref.save(update_fields=["registration"])
                    log.append(
                        "Moved speed-dating preferences for event "
                        f"'{reg.event}' to keeper's registration"
                    )
            reg.delete()
            log.append(f"Deleted duplicate registration for event '{reg.event}'")

    # 5b. Crush Credit follows the surviving account -- this is the member's
    # money.
    #
    # Credit is account-bound and non-transferable by design, and every read
    # goes through available_credit_cents(user). Left on the duplicate, a
    # balance would sit on an account that can no longer log in and would be
    # invisible to the keeper: the merge would quietly confiscate it.
    #
    # CreditRedemption needs no move -- it hangs off the credit, not the user.
    #
    # ⚠️ LOCK ORDER -- this must stay AFTER the EventRegistration block above.
    # redeem_for_registration() takes EventRegistration and THEN CrushCredit,
    # so a merge that locked credit first and touched registrations afterwards
    # would be an ABBA against any member completing a credit checkout at that
    # moment, and PostgreSQL would resolve it by aborting one of them -- the
    # payment, or the merge, halfway through. Credit last is the invariant
    # everywhere in this codebase (see services/credits.py); it was briefly
    # placed up beside the PaymentTransaction transfer, which read as
    # consistent and was not.
    from crush_lu.models.credits import CrushCredit

    moved_credits = CrushCredit.objects.filter(user=duplicate_user).update(
        user=keeper_user
    )
    if moved_credits:
        log.append(f"Moved {moved_credits} Crush Credit row(s) to keeper")

    # 6. EventConnections (bidirectional, unique_together: requester, recipient, event)
    # Handle connections where duplicate is requester
    for conn in EventConnection.objects.filter(requester=duplicate_user):
        if conn.recipient_id == keeper_user.id:
            # Self-connection after merge - delete
            conn.delete()
            log.append(f"Deleted self-connection (requester) for event {conn.event_id}")
        elif EventConnection.objects.filter(
            requester=keeper_user, recipient=conn.recipient, event=conn.event
        ).exists():
            conn.delete()
            log.append(
                f"Deleted duplicate connection (as requester) for event {conn.event_id}"
            )
        else:
            conn.requester = keeper_user
            conn.save(update_fields=["requester"])
            log.append(f"Moved connection (as requester) for event {conn.event_id}")

    # Handle connections where duplicate is recipient
    for conn in EventConnection.objects.filter(recipient=duplicate_user):
        if conn.requester_id == keeper_user.id:
            # Self-connection after merge - delete
            conn.delete()
            log.append(f"Deleted self-connection (recipient) for event {conn.event_id}")
        elif EventConnection.objects.filter(
            requester=conn.requester, recipient=keeper_user, event=conn.event
        ).exists():
            conn.delete()
            log.append(
                f"Deleted duplicate connection (as recipient) for event {conn.event_id}"
            )
        else:
            conn.recipient = keeper_user
            conn.save(update_fields=["recipient"])
            log.append(f"Moved connection (as recipient) for event {conn.event_id}")

    # 7. ConnectionMessages
    updated = ConnectionMessage.objects.filter(sender=duplicate_user).update(
        sender=keeper_user
    )
    if updated:
        log.append(f"Updated sender on {updated} connection message(s)")

    # 8. JourneyProgress (unique_together: user, journey)
    for jp in JourneyProgress.objects.filter(user=duplicate_user):
        existing = JourneyProgress.objects.filter(
            user=keeper_user, journey=jp.journey
        ).first()
        if existing:
            # Keep whichever has more progress
            if jp.completion_percentage > existing.completion_percentage:
                existing.delete()
                jp.user = keeper_user
                jp.save(update_fields=["user"])
                log.append(
                    f"Replaced keeper's journey progress with duplicate's "
                    f"(higher: {jp.completion_percentage}%)"
                )
            else:
                jp.delete()
                log.append(
                    f"Kept keeper's journey progress "
                    f"({existing.completion_percentage}% vs {jp.completion_percentage}%)"
                )
        else:
            jp.user = keeper_user
            jp.save(update_fields=["user"])
            log.append(f"Moved journey progress for '{jp.journey}' to keeper")

    # 9. PushSubscription (unique_together: user, endpoint)
    for sub in PushSubscription.objects.filter(user=duplicate_user):
        if not PushSubscription.objects.filter(
            user=keeper_user, endpoint=sub.endpoint
        ).exists():
            sub.user = keeper_user
            sub.save(update_fields=["user"])
            log.append("Moved push subscription to keeper")
        else:
            sub.delete()
            log.append("Deleted duplicate push subscription (same endpoint)")

    # PWADeviceInstallation (unique_together: user, device_fingerprint)
    for device in PWADeviceInstallation.objects.filter(user=duplicate_user):
        if not PWADeviceInstallation.objects.filter(
            user=keeper_user, device_fingerprint=device.device_fingerprint
        ).exists():
            device.user = keeper_user
            device.save(update_fields=["user"])
            log.append("Moved PWA device installation to keeper")
        else:
            device.delete()
            log.append("Deleted duplicate PWA device installation (same fingerprint)")

    # 10. EmailPreference (effectively OneToOne)
    if not EmailPreference.objects.filter(user=keeper_user).exists():
        EmailPreference.objects.filter(user=duplicate_user).update(user=keeper_user)
        log.append("Moved email preferences to keeper")
    else:
        EmailPreference.objects.filter(user=duplicate_user).delete()
        log.append("Deleted duplicate's email preferences (keeper already has them)")

    # 11. UserDataConsent (OneToOne)
    keeper_consent = UserDataConsent.objects.filter(user=keeper_user).first()
    dup_consent = UserDataConsent.objects.filter(user=duplicate_user).first()
    if dup_consent and not keeper_consent:
        dup_consent.user = keeper_user
        dup_consent.save(update_fields=["user"])
        log.append("Moved data consent record to keeper")
    elif dup_consent:
        dup_consent.delete()
        log.append("Deleted duplicate's consent record (keeper already has one)")

    # 12. UserActivity
    if not UserActivity.objects.filter(user=keeper_user).exists():
        UserActivity.objects.filter(user=duplicate_user).update(user=keeper_user)
        log.append("Moved user activity to keeper")
    else:
        UserActivity.objects.filter(user=duplicate_user).delete()
        log.append("Deleted duplicate's user activity (keeper already has one)")

    # 13. ProfileReminder (unique_together: user, reminder_type)
    for reminder in ProfileReminder.objects.filter(user=duplicate_user):
        if not ProfileReminder.objects.filter(
            user=keeper_user, reminder_type=reminder.reminder_type
        ).exists():
            reminder.user = keeper_user
            reminder.save(update_fields=["user"])
            log.append(f"Moved profile reminder ({reminder.reminder_type}) to keeper")
        else:
            reminder.delete()
            log.append(f"Deleted duplicate profile reminder ({reminder.reminder_type})")

    # 14. Deactivate duplicate user
    duplicate_user.is_active = False
    duplicate_user.save(update_fields=["is_active"])
    log.append(f"Deactivated duplicate user (id={duplicate_user.id})")

    logger.info(
        f"[ACCOUNT-MERGE] Completed merge: {duplicate_user.id} -> {keeper_user.id}. "
        f"Actions: {len(log)}"
    )

    return log
