"""
Account merge service for Crush.lu.

Handles merging duplicate user accounts (e.g., when Apple "Hide My Email"
creates a separate account from an existing email-based account).

Transfers all related data from the duplicate account to the keeper account,
handling unique constraints and bidirectional relationships.
"""

import logging
from django.db import transaction
from django.utils import timezone

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
        EventRegistration,
        EventConnection,
        ConnectionMessage,
        JourneyProgress,
        PushSubscription,
        PWADeviceInstallation,
        EmailPreference,
        UserDataConsent,
        UserActivity,
        ProfileReminder,
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

    # 1. Move SocialAccounts
    for sa in SocialAccount.objects.filter(user=duplicate_user):
        if not SocialAccount.objects.filter(
            user=keeper_user, provider=sa.provider, uid=sa.uid
        ).exists():
            sa.user = keeper_user
            sa.save(update_fields=['user'])
            log.append(f"Moved {sa.provider} social account to keeper")
        else:
            sa.delete()
            log.append(f"Deleted duplicate {sa.provider} social account")

    # 2. Move EmailAddresses (allauth)
    for ea in EmailAddress.objects.filter(user=duplicate_user):
        if not EmailAddress.objects.filter(user=keeper_user, email=ea.email).exists():
            ea.user = keeper_user
            ea.primary = False  # Keeper's primary email stays
            ea.save(update_fields=['user', 'primary'])
            log.append(f"Moved email address {ea.email} to keeper")
        else:
            ea.delete()
            log.append(f"Deleted duplicate email address {ea.email}")

    # 3. Handle CrushProfile (OneToOne)
    keeper_profile = getattr(keeper_user, 'crushprofile', None)
    dup_profile = getattr(duplicate_user, 'crushprofile', None)

    if dup_profile and not keeper_profile:
        # Move duplicate's profile to keeper
        dup_profile.user = keeper_user
        dup_profile.save(update_fields=['user'])
        log.append("Moved CrushProfile from duplicate to keeper")
    elif dup_profile and keeper_profile:
        # Both have profiles - keep keeper's, transfer referral data
        ReferralCode.objects.filter(referrer=dup_profile).update(referrer=keeper_profile)
        moved_codes = ReferralCode.objects.filter(referrer=keeper_profile).count()
        ReferralAttribution.objects.filter(referrer=dup_profile).update(
            referrer=keeper_profile
        )
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
    for reg in EventRegistration.objects.filter(user=duplicate_user):
        if not EventRegistration.objects.filter(
            event=reg.event, user=keeper_user
        ).exists():
            reg.user = keeper_user
            reg.save(update_fields=['user'])
            log.append(f"Moved registration for event '{reg.event}' to keeper")
        else:
            reg.delete()
            log.append(
                f"Deleted duplicate registration for event '{reg.event}'"
            )

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
            conn.save(update_fields=['requester'])
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
            conn.save(update_fields=['recipient'])
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
                jp.save(update_fields=['user'])
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
            jp.save(update_fields=['user'])
            log.append(f"Moved journey progress for '{jp.journey}' to keeper")

    # 9. PushSubscription (unique_together: user, endpoint)
    for sub in PushSubscription.objects.filter(user=duplicate_user):
        if not PushSubscription.objects.filter(
            user=keeper_user, endpoint=sub.endpoint
        ).exists():
            sub.user = keeper_user
            sub.save(update_fields=['user'])
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
            device.save(update_fields=['user'])
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
        dup_consent.save(update_fields=['user'])
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
            reminder.save(update_fields=['user'])
            log.append(f"Moved profile reminder ({reminder.reminder_type}) to keeper")
        else:
            reminder.delete()
            log.append(f"Deleted duplicate profile reminder ({reminder.reminder_type})")

    # 14. Deactivate duplicate user
    duplicate_user.is_active = False
    duplicate_user.save(update_fields=['is_active'])
    log.append(f"Deactivated duplicate user (id={duplicate_user.id})")

    logger.info(
        f"[ACCOUNT-MERGE] Completed merge: {duplicate_user.id} -> {keeper_user.id}. "
        f"Actions: {len(log)}"
    )

    return log
