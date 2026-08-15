"""
Signal handlers for Crush.lu app
"""

import logging
import threading
import time
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.core.files.base import ContentFile
from django.db.models import Exists, OuterRef, Q
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone
from allauth.account.signals import email_confirmation_sent, email_confirmed
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.signals import (
    pre_social_login,
    social_account_added,
)
from django.contrib import messages
from django.utils.translation import gettext_lazy as _

from azureproject.domains import DOMAINS as _PLATFORM_DOMAINS

from .utils.image_processing import process_uploaded_image

from .models import (
    MeetupEvent,
    GlobalActivityOption,
    EventVotingSession,
    CrushProfile,
    SpecialUserExperience,
    EmailPreference,
    EventRegistration,
    CrushCoach,
    CrushSpark,
    ProfileSubmission,
    ReferralCode,
)
from .models.journey import JourneyProgress
from .utils.i18n import is_valid_language

from crush_lu.models.events import SEAT_HOLDING_STATUSES
from crush_lu.wallet_pass import PASS_NEXT_EVENT_STATUSES

logger = logging.getLogger(__name__)

# Thread-local storage to pass domain context between signals
_thread_local = threading.local()


# =============================================================================
# WALLET PASS UPDATE TRIGGERS
# =============================================================================

# Every input a wallet pass renders from, INCLUDING the three that are
# properties rather than columns. No receiver reads this set any more — they
# gate on the two subsets below, which are what can actually be compared — so
# treat it as the inventory the subsets are checked against:
# test_member_pass_fields_are_real_columns asserts the split, which turns
# "added a field here and forgot the subset" into a failing test rather than a
# pass that silently never refreshes.
WALLET_UPDATE_PROFILE_FIELDS = {
    "referral_points",
    "membership_tier",
    "show_photo_on_wallet",
    "photo_1",
    "display_name",
    "first_name",
    "last_name",
    "show_full_name",
}

# The subset that appears on an EVENT TICKET, whose only profile input is the
# attendee name. `display_name` is a property (not a field, so it can never
# reach update_fields) computed from `show_full_name` plus the User's names —
# and User renames are handled by refresh_wallet_passes_on_user_rename.
WALLET_TICKET_IDENTITY_FIELDS = {"show_full_name"}

# The subset that is a real model field, and therefore the only part of the set
# a change can be *proven* against: `display_name`/`first_name`/`last_name` are
# PROPERTIES reading the User, so they can neither reach a profile
# `update_fields` nor be fetched with .values() — renames arrive through
# refresh_wallet_passes_on_user_rename instead. Everything else the member pass
# prints is derived from these (tier_display), owned by another model (the
# referral code, the next event) or immutable (member_since).
WALLET_MEMBER_PASS_FIELDS = {
    "referral_points",
    "membership_tier",
    "show_photo_on_wallet",
    "photo_1",
    "show_full_name",
}


def _trigger_apple_pass_refresh(profile):
    """
    Trigger Apple Wallet pass refresh via APNS push notification.

    When Apple Wallet receives this silent push, it calls our PassKit web service
    endpoint to fetch the updated pass.

    Args:
        profile: CrushProfile instance with apple_pass_serial set
    """
    if not profile.apple_pass_serial:
        return

    pass_type_id = getattr(settings, "WALLET_APPLE_PASS_TYPE_IDENTIFIER", None)
    if not pass_type_id:
        logger.warning(
            "Cannot trigger Apple pass refresh: WALLET_APPLE_PASS_TYPE_IDENTIFIER not configured"
        )
        return

    def _after_commit():
        try:
            from .wallet.passkit_apns import send_passkit_push_notifications

            result = send_passkit_push_notifications(
                pass_type_identifier=pass_type_id,
                serial_number=profile.apple_pass_serial,
            )

            if result["total"] > 0:
                logger.info(
                    f"Apple Wallet pass refresh triggered for user {profile.user_id}: "
                    f"success={result['success']}, failed={result['failed']}, total={result['total']}"
                )
            else:
                logger.debug(
                    f"No Apple Wallet device registrations for user {profile.user_id}"
                )
        except Exception as e:
            logger.error(
                f"Error triggering Apple pass refresh for user {profile.user_id}: {e}"
            )

    try:
        # Deferred to commit. Callers such as event_cancel() save inside
        # transaction.atomic() holding a select_for_update; pushing before
        # commit means Wallet's poll arrives on a different connection, sees
        # neither the new state nor the advanced update marker, and gets 204 —
        # then the commit advances the marker with no second push, so the
        # refresh is lost until Wallet's next periodic poll. on_commit runs
        # inline when there is no transaction, so other callers are unaffected.
        transaction.on_commit(_after_commit)
    except Exception as e:
        logger.error(
            f"Error scheduling Apple pass refresh for user {profile.user_id}: {e}"
        )


def _trigger_google_wallet_object_update(profile):
    """
    Trigger Google Wallet object update via REST API.

    For Google Wallet, we PATCH the object to update its content.
    Uses the Google Wallet REST API with service account authentication.

    Args:
        profile: CrushProfile instance with google_wallet_object_id set
    """
    if not profile.google_wallet_object_id:
        return

    def _after_commit():
        try:
            from .wallet.google_api import update_google_wallet_pass

            # Re-read rather than PATCH from the instance captured at save
            # time. Unlike the Apple half — which pushes a content-free "come
            # back for it" and lets the device fetch from the web service, so
            # it always renders current rows — this call ships the payload
            # itself, built from whatever the instance holds. Deferring to
            # commit releases the profile row lock first, so between scheduling
            # and running, another transaction can have committed a newer tier
            # or point total; sending the captured copy would put Google back
            # on the older one indefinitely. Reading committed state here means
            # the write carries what the database actually says.
            #
            # This narrows the window rather than closing it: two overlapping
            # callbacks can still finish their network calls out of order, and
            # the loser is whichever re-read first — it lands last and writes
            # the older balance.
            #
            # Do NOT reason about this as a rare case. An earlier version of
            # this comment argued the payload only changes on a tier upgrade,
            # since points move via QuerySet.update() and emit no signal; that
            # stopped being true when refresh_passes_after_points_change()
            # started scheduling one for every award and redemption. Any two
            # overlapping points transactions for the same profile can hit it.
            #
            # Closing it needs per-profile serialization or server-side
            # versioning: a mutex puts blocking back in the request (on_commit
            # runs inline), and a queue is not available — production leaves
            # DJANGO_TASKS_BACKEND unset, so .enqueue() would run inline too.
            # Left open deliberately, and written down rather than assumed
            # away.
            fresh = CrushProfile.objects.filter(pk=profile.pk).first()
            if fresh is None or not fresh.google_wallet_object_id:
                # Deleted, or the object was unlinked, while the callback was
                # queued. cleanup_passkit_registrations_on_profile_delete owns
                # that teardown; there is nothing left to PATCH.
                return

            result = update_google_wallet_pass(fresh)

            if result["success"]:
                logger.info(
                    f"Google Wallet pass updated for user {fresh.user_id}: "
                    f"object_id={fresh.google_wallet_object_id}"
                )
            else:
                logger.warning(
                    f"Google Wallet pass update failed for user {fresh.user_id}: "
                    f"{result['message']}"
                )

        except Exception as e:
            logger.error(
                f"Error updating Google Wallet pass for user {profile.user_id}: {e}"
            )

    try:
        # Deferred to commit like the Apple half above, for two reasons of its
        # own. This one is not a "come back for the new package" push — it
        # PATCHes the content straight onto Google, so a caller that rolls back
        # afterwards leaves Google showing state that never existed, and the
        # payload comes from build_wallet_pass_data(), which re-queries for the
        # next event and should read committed rows. It is also two HTTP calls
        # (token exchange, then the PATCH) at a 30s timeout each, and callers
        # such as referrals.update_membership_tier() save inside
        # transaction.atomic() holding a select_for_update — up to a minute of
        # network with the row locked. on_commit runs inline when there is no
        # transaction, so every other caller is unaffected.
        transaction.on_commit(_after_commit)
    except Exception as e:
        logger.error(
            f"Error scheduling Google Wallet update for user {profile.user_id}: {e}"
        )


def trigger_wallet_pass_updates(profile):
    """
    Trigger updates for both Apple and Google Wallet passes.

    Args:
        profile: CrushProfile instance
    """
    _trigger_apple_pass_refresh(profile)
    _trigger_google_wallet_object_update(profile)


@receiver(post_save, sender=User)
def create_email_preference_for_user(sender, instance, created, **kwargs):
    """
    Create EmailPreference with default settings when a new user is created.

    Default settings (GDPR compliant):
    - All transactional/engagement emails: ON
    - Marketing emails: OFF (requires explicit opt-in)

    This ensures every user has email preferences from the start,
    enabling proper unsubscribe functionality.
    """
    if not created:
        return

    try:
        EmailPreference.objects.get_or_create(
            user=instance,
            defaults={
                "email_profile_updates": True,
                "email_event_reminders": True,
                "email_new_connections": True,
                "email_new_messages": True,
                "email_marketing": False,  # OFF by default - GDPR compliance
            },
        )
        logger.info(
            f"Created email preferences for new user {instance.id} ({instance.email})"
        )
    except Exception as e:
        # Don't fail user creation if email preference creation fails
        logger.error(
            f"Error creating email preferences for user {instance.id}: {str(e)}"
        )


@receiver(post_save, sender=User)
def create_user_data_consent(sender, instance, created, **kwargs):
    """
    Create UserDataConsent record for every new user.
    PowerUp consent is given implicitly on account creation.
    Crush.lu consent must be explicitly given during profile creation (form signup)
    or implicitly during OAuth signup.
    """
    if not created:
        return

    try:
        from crush_lu.models.profiles import UserDataConsent

        # Check if OAuth consent data is available (set by pre_social_login)
        oauth_consent = getattr(_thread_local, "oauth_consent_data", None)

        defaults = {
            "powerup_consent_given": True,  # Implicit consent on account creation
            "powerup_consent_date": timezone.now(),
            "crushlu_consent_given": False,  # Must be explicitly given (form) or implicit (OAuth)
        }

        # If OAuth signup, apply implicit Crush.lu consent
        if oauth_consent:
            defaults["crushlu_consent_given"] = oauth_consent.get(
                "crushlu_consent", False
            )
            defaults["crushlu_consent_date"] = timezone.now()
            defaults["crushlu_consent_ip"] = oauth_consent.get("crushlu_consent_ip")
            logger.info(
                f"OAuth signup detected - applying implicit Crush.lu consent for user {instance.id}"
            )

        UserDataConsent.objects.get_or_create(user=instance, defaults=defaults)
        logger.info(
            f"Created data consent for new user {instance.id} ({instance.email})"
        )

        # Clear OAuth consent data after use
        if hasattr(_thread_local, "oauth_consent_data"):
            delattr(_thread_local, "oauth_consent_data")

    except Exception as e:
        # Don't fail user creation if consent creation fails
        logger.error(f"Error creating data consent for user {instance.id}: {str(e)}")


# List of Crush.lu domains - profile should be created for logins on these domains.
# Built from azureproject.domains so test.crush.lu and any future alias is picked
# up automatically. Mirrors crush_lu/adapter.py:16-17.
CRUSH_LU_DOMAINS = {"crush.lu", "localhost", "127.0.0.1"}
CRUSH_LU_DOMAINS.update(_PLATFORM_DOMAINS["crush.lu"].get("aliases", []))


def _is_crush_domain(request):
    """Check if current request is from a crush.lu host (incl. staging aliases)."""
    if not request:
        return False
    try:
        host = request.get_host().split(":")[0].lower()
    except (KeyError, AttributeError):
        return False
    return host in CRUSH_LU_DOMAINS


@receiver(user_logged_in)
def create_crush_profile_on_login(sender, request, user, **kwargs):
    """
    Create a CrushProfile when a user logs in on crush.lu domain.

    This ensures ALL users who log in via crush.lu (regardless of auth method:
    email/password, Facebook, Microsoft, LinkedIn) get a basic CrushProfile,
    even if they don't complete the full profile creation form.

    This does NOT create profiles for logins on other domains (powerup.lu,
    vinsdelux.com, delegations.lu).
    """
    if not request:
        return

    # Get the host without port - handle test client edge cases
    try:
        host = request.get_host().split(":")[0].lower()
    except (KeyError, AttributeError):
        # Test client may not have SERVER_NAME set
        logger.debug(
            "Could not determine host for login signal - skipping CrushProfile creation"
        )
        return

    # Only create profile for crush.lu domain logins
    if host not in CRUSH_LU_DOMAINS:
        logger.debug(
            f"Skipping CrushProfile creation for non-Crush domain login: {host}"
        )
        return

    # Skip delegations.lu domain - they have their own profile system
    if "delegations.lu" in host:
        logger.debug(
            f"Skipping CrushProfile creation for delegations.lu domain: {host}"
        )
        return

    preferred_language = "en"
    request_language = getattr(request, "LANGUAGE_CODE", None)
    if is_valid_language(request_language):
        preferred_language = request_language
    else:
        session_language = request.session.get("django_language")
        if is_valid_language(session_language):
            preferred_language = session_language

    try:
        # Use get_or_create to avoid duplicates
        profile, created = CrushProfile.objects.get_or_create(
            user=user,
            defaults={
                "completion_status": "not_started",  # Mark as not started
                "preferred_language": preferred_language,
            },
        )

        if created:
            logger.info(
                f"Created CrushProfile for user {user.email} on login (domain: {host})"
            )
        else:
            logger.debug(f"CrushProfile already exists for user {user.email}")

    except Exception as e:
        # Don't fail login if profile creation fails
        logger.error(
            f"Error creating CrushProfile on login for user {user.id}: {str(e)}",
            exc_info=True,
        )


@receiver(user_logged_in)
def sync_language_preference_on_login(sender, request, user, **kwargs):
    """
    Sync session language to CrushProfile.preferred_language on login.

    This handles the edge case where a user signs up on a non-English page
    (e.g., /fr/signup/) but the CrushProfile is created with default 'en'.

    On first login, if the profile has the default 'en' language but the session
    has a different language (de/fr), we update the profile to match.

    This ensures push notifications and emails are sent in the language the
    user was browsing in when they signed up.
    """
    if not request:
        return

    # Only process for crush.lu domain
    try:
        host = request.get_host().split(":")[0].lower()
    except (KeyError, AttributeError):
        return

    if host not in CRUSH_LU_DOMAINS:
        return

    try:
        if hasattr(user, "crushprofile") and user.crushprofile:
            profile = user.crushprofile
            # Get session language (set by Django's LocaleMiddleware)
            #
            # NB this key is never written: LocaleMiddleware does not touch the
            # session, Django 6's set_language sets only the cookie, and
            # LANGUAGE_SESSION_KEY was removed in Django 4.0 (and was "_language"
            # anyway, never "django_language"). Nothing in the repo, Django or
            # allauth assigns it, so session_lang is always None and this
            # handler is currently a no-op — see also the matching dead branch
            # in create_crush_profile_on_login. Kept guarded rather than trusted
            # to stay unreachable.
            session_lang = request.session.get("django_language", None)

            # If profile has default 'en' but session has different valid language,
            # update profile to match session (user's browsing language preference)
            #
            # Yields to an explicit pick, like every other inference: this is a
            # guess from how the member was browsing, and == "en" cannot tell an
            # untouched default from an answered English. Without the flag check
            # this would replace a deliberate English with the browsing language
            # and, because get_onscreen_language reads a stored de/fr as
            # self-evidently chosen, the substitution would then look authoritative.
            if (
                profile.preferred_language == "en"
                and not profile.language_explicitly_set
                and session_lang
                and is_valid_language(session_lang)
                and session_lang != "en"
            ):
                profile.preferred_language = session_lang
                profile.save(update_fields=["preferred_language"])
                logger.info(
                    f"Synced language preference for user {user.id}: "
                    f"'en' -> '{session_lang}' (from session)"
                )
    except Exception as e:
        # Don't fail login if language sync fails
        logger.warning(f"Error syncing language preference for user {user.id}: {e}")


@receiver(post_save, sender=MeetupEvent)
def auto_create_event_ticket_class_on_publish(sender, instance, created, **kwargs):
    """
    Create a Google Wallet EventTicketClass when an event is published.

    Only triggers when is_published changes to True and the event doesn't
    already have a wallet class. Runs in a separate thread to avoid blocking.
    """
    if not instance.is_published:
        return

    if instance.google_wallet_event_class_id:
        return

    if not getattr(settings, "WALLET_GOOGLE_EVENT_TICKET_ENABLED", True):
        return

    # Check if Google Wallet is configured
    if not getattr(settings, "WALLET_GOOGLE_ISSUER_ID", ""):
        return

    # create_event_ticket_class persists the new class id with
    # event.save(update_fields=[...]) on THIS instance, and that nested save
    # re-runs remember_previous_event_start — which re-reads the row the outer
    # save has already written and overwrites the "before" snapshot with the
    # new values. Every later receiver then compares new against new: reminder
    # markers are not cleared on a reschedule, and no wallet refresh is
    # scheduled at all. It bites precisely the first edit of a published event
    # that has no class yet, which is exactly when a coach reschedules one.
    #
    # Preserving it across the call also keeps the nested receiver chain inert
    # (it sees the clobbered snapshot, finds nothing changed, and no-ops), so
    # the outer chain still schedules exactly ONE fan-out rather than two.
    snapshot_fields = getattr(instance, "_previous_ticket_fields", None)
    snapshot_start = getattr(instance, "_previous_date_time", None)
    try:
        from .wallet.google_event_ticket_api import create_event_ticket_class

        result = create_event_ticket_class(instance)
        if result["success"]:
            logger.info(
                f"Created EventTicketClass for event {instance.id}: {result['class_id']}"
            )
        else:
            logger.warning(
                f"Failed to create EventTicketClass for event {instance.id}: {result['message']}"
            )
    except Exception as e:
        logger.error(f"Error creating EventTicketClass for event {instance.id}: {e}")
    finally:
        instance._previous_ticket_fields = snapshot_fields
        instance._previous_date_time = snapshot_start


# Every MeetupEvent field the Apple Wallet ticket payload embeds. A change to
# any of them leaves installed passes showing something that is simply wrong,
# so refresh_apple_tickets_on_event_change compares all of them — not just the
# start time. Keep in sync with apple_event_ticket.build_apple_event_ticket.
#
# The back field is composed by MeetupEvent.full_address, so *every component
# that property reads* belongs in this tuple — including the legacy `address`
# it falls back to. Miss one and editing that field silently stops refreshing
# installed passes: no error anywhere, just a card showing the old venue.
#
# `canton` is deliberately absent: no wallet surface renders it, so listing it
# would cost an APNs fan-out that changes nothing on the card.
_TICKET_PAYLOAD_BASE_FIELDS = (
    "date_time",  # date/time fields, relevantDate, expirationDate
    "duration_minutes",  # expirationDate
    "location",  # auxiliary field
    "address",  # back field — legacy free text, still the fallback
    "address_street",  # back field, via full_address
    "address_number",  # back field, via full_address
    "address_postcode",  # back field, via full_address
    "address_town",  # back field, via full_address
    "event_type",  # back field (get_event_type_display)
    "latitude",  # locations[] lock-screen trigger
    "longitude",
    "is_cancelled",  # voided
)

# `title` is registered with django-modeltranslation (see translation.py), so
# the bare `title` descriptor resolves to whichever language is active — and
# admin is forced to English. Comparing it would miss staff editing only
# title_fr or title_de, while the ticket renders whichever translation its
# holder reads, leaving those passes showing the old name. Compare the concrete
# columns instead.
_TICKET_TRANSLATED_FIELDS = ("title",)

_TICKET_PAYLOAD_FIELDS = _TICKET_PAYLOAD_BASE_FIELDS + tuple(
    f"{field}_{code.replace('-', '_')}"
    for field in _TICKET_TRANSLATED_FIELDS
    for code, _label in settings.LANGUAGES
)

# The subset of the above that can actually change the GOOGLE member object.
# _build_generic_object_payload renders only the next event's title and date,
# so a venue move, address fix, coordinate correction or event-type change
# rewrites the Apple ticket while leaving the Google object byte-identical —
# and paying an OAuth exchange plus up to WALLET_GOOGLE_BULK_UPDATE_LIMIT
# synchronous PATCHes to write an unchanged object is exactly the budget spend
# the cap exists to protect.
#
# Membership in this set has TWO grounds, and missing the second is the easy
# mistake: a field belongs here if the card PRINTS it (the translated title
# columns — the card renders whichever language its holder reads — and
# date_time), or if it decides whether this event is ON the card at all:
#
#   * is_cancelled — get_next_event_for_pass excludes cancelled events, so the
#     block flips to "Browse events on crush.lu";
#   * duration_minutes — nothing prints it, but the same helper keeps an event
#     only while `end_time >= now`, and end_time is date_time + duration. So
#     shortening a running event past now drops it off the card, and extending
#     one that just ended puts it back.
#
# Keep in sync with google_api._build_generic_object_payload AND with
# wallet_pass.get_next_event_for_pass, which between them decide both grounds.
_GOOGLE_PAYLOAD_FIELDS = frozenset(
    ("date_time", "duration_minutes", "is_cancelled")
    + tuple(f"title_{code.replace('-', '_')}" for code, _label in settings.LANGUAGES)
)


def _google_relevant_changes(previous, instance, changed, now=None):
    """The changed fields that can actually alter the Google member card.

    Mostly a membership test against _GOOGLE_PAYLOAD_FIELDS, with one field
    that cannot be decided statically. `duration_minutes` earns its place by
    SELECTING rather than rendering, so a length edit is only worth a fan-out
    when it moves the event across the `end_time >= now` boundary. Treating
    every duration edit as relevant would fire up to
    WALLET_GOOGLE_BULK_UPDATE_LIMIT synchronous PATCHes from the admin request
    to write byte-identical objects — the exact spend the field subset exists
    to prevent.

    Exactly ONE case is dropped, and the two same-side cases are NOT
    symmetric — which is the trap:

      * still-upcoming both before and after: the event is on the card in both
        states with its title and date untouched, so the stored Google object
        already matches what we would write. Genuinely nothing to send.
      * ALREADY ENDED both before and after: skipping looks equally safe and is
        not. get_next_event_for_pass stopped selecting the event when it
        finished, but the Google object is server-side state whose last PATCH
        went out while the event was still upcoming — so it can still be
        advertising it, and nothing else in the estate recomputes that (there
        is no "event ended" trigger; see the same reasoning in
        admin.quiz.mark_unattended_as_no_show). The edit is then the only thing
        that would heal the card, and dropping it forfeits that for good.

    So the guard is "neither side is over", not "both sides agree".

    A `date_time` edit needs no such care: it is rendered, so it is relevant on
    its own, and it is already in the set.
    """
    relevant = [field for field in changed if field in _GOOGLE_PAYLOAD_FIELDS]
    if "duration_minutes" not in relevant:
        return relevant

    now = now or timezone.now()

    def _ended(start, minutes):
        # Mirrors MeetupEvent.end_time; the snapshot is a values() dict, so
        # there is no model instance to ask.
        return start + timedelta(minutes=minutes or 0) < now

    was_over = _ended(previous["date_time"], previous["duration_minutes"])
    is_over = _ended(instance.date_time, instance.duration_minutes)
    if not was_over and not is_over:
        relevant.remove("duration_minutes")
    return relevant


# Registration statuses under which an event can appear on a MEMBER card —
# imported, not restated. This and wallet_pass.get_next_event_for_pass each
# used to carry their own copy under a "keep in sync" note, which is the
# arrangement that lets them drift; the admin bulk actions consume the same
# rule. Anything outside this set is a holder the event-change fan-outs must
# not spend their budget on.
_NEXT_EVENT_STATUSES = PASS_NEXT_EVENT_STATUSES


@receiver(pre_save, sender=MeetupEvent)
def remember_previous_event_start(sender, instance, **kwargs):
    """Stash the stored start so post_save can detect a reschedule.

    Also snapshots every field the wallet ticket embeds, so the ticket-refresh
    receiver can tell whether an edit actually changed the pass.
    """
    if not instance.pk:
        instance._previous_date_time = None
        instance._previous_ticket_fields = None
        return
    previous = (
        MeetupEvent.objects.filter(pk=instance.pk)
        .values(*_TICKET_PAYLOAD_FIELDS)
        .first()
    )
    instance._previous_ticket_fields = previous
    instance._previous_date_time = previous["date_time"] if previous else None


@receiver(post_save, sender=MeetupEvent)
def reset_reminders_on_reschedule(sender, instance, created, **kwargs):
    """Clear the day-before reminder markers when an event is moved.

    ``date_time`` is editable in the admin, and the markers say "the reminder
    for this event has been handled" without recording *which* start time it
    described. Move an event to a later date after its reminders went out and
    every registration stays stamped, so the sweep on the new day-before date
    filters them all out and nobody is told the time changed.

    Only forward-looking events are reset — re-reminding for an event that has
    already happened would be worse than silence.
    """
    if created:
        return
    previous = getattr(instance, "_previous_date_time", None)
    if previous is None or previous == instance.date_time:
        return
    if instance.date_time <= timezone.now():
        return

    # .update() so the wallet-pass receivers don't fire per registration.
    cleared = (
        EventRegistration.objects.filter(event=instance)
        .exclude(reminder_notified_at__isnull=True, reminder_sent_at__isnull=True)
        .update(reminder_notified_at=None, reminder_sent_at=None)
    )
    if cleared:
        logger.info(
            "Event %s rescheduled %s -> %s; cleared reminder markers on %s "
            "registration(s)",
            instance.pk,
            previous.isoformat(),
            instance.date_time.isoformat(),
            cleared,
        )


@receiver(post_save, sender=MeetupEvent)
def reset_cancellation_notifications_on_restore(sender, instance, created, **kwargs):
    """Make a later cancellation notify every attendee again.

    The delivery cursor belongs to one organiser-cancellation cycle. Paid
    registrations often get it reset while a new remedy is issued, but free,
    unpaid and waitlisted rows have no credit lifecycle to do that for them.
    Restoring the event is the boundary that invalidates every old cursor.
    """
    previous = getattr(instance, "_previous_ticket_fields", None)
    if instance.is_cancelled:
        if instance.organiser_cancellation_started_at is None and (
            created or (previous and not previous.get("is_cancelled"))
        ):
            started_at = timezone.now()
            MeetupEvent.objects.filter(pk=instance.pk).update(
                organiser_cancellation_started_at=started_at
            )
            instance.organiser_cancellation_started_at = started_at
        return
    if created:
        return
    if not previous or not previous.get("is_cancelled"):
        return

    MeetupEvent.objects.filter(pk=instance.pk).update(
        organiser_cancellation_started_at=None
    )
    instance.organiser_cancellation_started_at = None
    cleared = (
        EventRegistration.objects.filter(event=instance)
        .exclude(organiser_cancellation_notified_at__isnull=True)
        .update(organiser_cancellation_notified_at=None)
    )
    if cleared:
        logger.info(
            "Event %s restored; cleared organiser-cancellation notification "
            "markers on %s registration(s)",
            instance.pk,
            cleared,
        )


@receiver(post_save, sender=MeetupEvent)
def refresh_apple_tickets_on_event_change(sender, instance, created, **kwargs):
    """Refresh installed wallet passes when the EVENT itself changes.

    The pass embeds the event's date, time, location and address, so a
    reschedule or relocation leaves every installed ticket showing details that
    are simply wrong. The per-registration receiver cannot cover this: nothing
    touches an EventRegistration row, and the reschedule handler above uses
    `.update()` precisely so per-registration receivers do not fire.

    Separate from reset_reminders_on_reschedule so it does not inherit that
    handler's "forward-looking events only" guard — a ticket showing the wrong
    details is worth correcting either way — and so an error in one cannot skip
    the other.

    Fires on a change to ANY embedded field (_TICKET_PAYLOAD_FIELDS), not just
    the start time: retitling, moving venue, correcting the address or
    coordinates, or changing the duration all rewrite the pass just as surely
    as a reschedule does.

    Named for Apple because that is all it used to cover, but it now drives
    three fan-outs, each bounded and each isolated from the others' failures:
    Apple event tickets, Apple member passes, and Google member objects (which
    print the same "Next Event" block).

    The Google half was never handled here because it appeared to fix itself:
    any bare profile.save() — the Microsoft SSO login path does one on every
    sign-in — reaches trigger_wallet_pass_update_on_profile_change, which treats
    an update_fields-less save as "refresh everything". That is an accident of
    an over-broad receiver, not a mechanism, it only ever healed members who
    happened to log in, and narrowing that receiver to real field changes
    removes it entirely. The refresh belongs on the event change either way.
    """
    if created:
        return

    previous = getattr(instance, "_previous_ticket_fields", None)
    if previous is None:
        return

    changed = [
        field
        for field in _TICKET_PAYLOAD_FIELDS
        if previous.get(field) != getattr(instance, field, None)
    ]
    if not changed:
        return

    # Members whose card can actually show this event. get_next_event_for_pass
    # only ever considers seat-holding or waitlisted registrations, so a
    # cancelled or no-show attendee's card does not display this event and
    # rebuilding it achieves nothing. That is not merely wasted work: both
    # fan-outs below are capped, and on the Google side the cap is a hard loss
    # (no poll heals what it skips), so an ineligible profile taking a slot
    # leaves someone whose card IS wrong stale for good.
    #
    # Lazy, so it costs nothing until each branch narrows and evaluates it, and
    # defined outside both try blocks so a failure in one cannot NameError the
    # other.
    attendee_profiles = CrushProfile.objects.filter(
        user__eventregistration__event=instance,
        user__eventregistration__status__in=_NEXT_EVENT_STATUSES,
    )

    try:
        from .wallet.passkit_service import (
            refresh_event_tickets,
            refresh_ticket_serials,
        )

        refreshed = refresh_event_tickets(instance)

        # Member passes embed this event too — build_wallet_pass_data puts the
        # holder's NEXT event (title, date, time, location) on the card — and
        # they carry a different serial, so refreshing only the ticket serials
        # leaves every attendee's member pass showing the old details.
        member_serials = list(
            attendee_profiles.exclude(apple_pass_serial="")
            .values_list("apple_pass_serial", flat=True)
            .distinct()
        )
        refresh_ticket_serials(
            member_serials, context=f"Event {instance.pk} member passes"
        )

        if refreshed or member_serials:
            logger.info(
                "Scheduled Apple refresh for %s ticket(s) and %s member "
                "pass(es) on event %s (changed: %s)",
                refreshed,
                len(member_serials),
                instance.pk,
                ", ".join(changed),
            )
    except Exception as e:
        logger.error(
            f"Error refreshing Apple event tickets for event {instance.pk}: {e}"
        )

    # Google member objects print the same "Next Event" block, and there is no
    # Google ticket to carry the change instead — the member object is the only
    # Google surface this event appears on.
    #
    # Gated on its OWN field subset, not the Apple one: the Google object
    # renders far less of the event, so most edits that rewrite a ticket leave
    # it identical, and this fan-out is far more expensive per pass than
    # advancing an Apple update tag.
    #
    # Its own try/except, not the Apple block's: these are independent
    # fan-outs over independent APIs, and a Google outage must not skip the
    # Apple refresh (nor be reported as an Apple failure).
    google_changed = _google_relevant_changes(previous, instance, changed)
    if not google_changed:
        return

    try:
        from .wallet.google_api import refresh_google_wallet_objects

        # Narrow again, to holders whose card can actually be showing THIS
        # event. get_next_event_for_pass renders the earliest upcoming
        # registration, so a member with a nearer event never displayed this
        # one and rebuilding them writes a byte-identical object. Under the
        # Google cap that is not merely waste: enough no-op profiles ahead of
        # the limit push someone whose card IS wrong past it, for good.
        #
        # Deliberately conservative — it drops only holders with a qualifying
        # event earlier than BOTH the old and the new start. A reschedule that
        # moves this event PAST someone's other event therefore still refreshes
        # them, which it must: their card has to stop showing this one. Getting
        # it wrong in that direction leaves a wrong card with nothing left to
        # correct it, while getting it wrong the other way only spends budget.
        #
        # One EXISTS subquery, not a per-profile get_next_event_for_pass call —
        # the whole point of the batch is to keep this off a per-attendee
        # query.
        #
        # The lower bound is `now`, NOT live_lookback_cutoff. That helper is
        # get_next_event_for_pass's coarse DB-side pre-filter — a 7-day window
        # it then narrows with a precise per-candidate `end_time >= now`, which
        # SQL cannot express portably (timedelta * F() is unsupported on
        # SQLite). Borrowing only the coarse half would count an event that
        # started inside the window but has already FINISHED as somebody's
        # nearer event, excluding a holder whose card really does show the
        # changed event — precisely the permanent staleness this filter exists
        # to prevent, reintroduced from the other side.
        #
        # `>= now` counts only events that have not started yet, which are
        # therefore certainly still live, so every exclusion is provably safe.
        # The price is that a nearer event running RIGHT NOW no longer
        # suppresses anything and its holders get a redundant PATCH — the
        # correct direction to err: over-refreshing spends budget, while
        # under-refreshing leaves a wrong card with nothing to correct it.
        # (Refining the coarse set in Python instead would mean loading and
        # checking end_time per candidate, which is the per-attendee work the
        # batch exists to avoid.)
        # Compared on the SAME key _next_event_candidates orders by, not on the
        # start time alone. Since that selector tie-breaks with (date_time,
        # event_id, id), an event sharing this one's start but carrying a
        # smaller id sorts first and IS what the holder sees — while a bare
        # `date_time__lt` calls it "not nearer" and queues a byte-identical
        # PATCH. The registration id, the selector's third key, is not needed:
        # it only separates two registrations for the same event, which one
        # user cannot have.
        previous_start = (previous or {}).get("date_time") or instance.date_time
        bound_start = min(previous_start, instance.date_time)
        nearer_event = EventRegistration.objects.filter(
            Q(event__date_time__lt=bound_start)
            | Q(event__date_time=bound_start, event_id__lt=instance.pk),
            user_id=OuterRef("user_id"),
            status__in=_NEXT_EVENT_STATUSES,
            event__is_cancelled=False,
            event__date_time__gte=timezone.now(),
        )
        google_profiles = list(
            attendee_profiles.exclude(google_wallet_object_id="")
            .exclude(Exists(nearer_event))
            .distinct()
        )
        scheduled = refresh_google_wallet_objects(
            google_profiles, context=f"Event {instance.pk} Google member passes"
        )
        if scheduled:
            logger.info(
                "Scheduled Google refresh for %s member object(s) on event %s "
                "(changed: %s)",
                scheduled,
                instance.pk,
                ", ".join(google_changed),
            )
    except Exception as e:
        logger.error(
            f"Error refreshing Google Wallet objects for event {instance.pk}: {e}"
        )


@receiver(post_save, sender=MeetupEvent)
def sync_event_to_echo_lu(sender, instance, created, **kwargs):
    """Mirror the event onto echo.lu whenever it is saved.

    No change detection here, deliberately. The service fingerprints the
    payload it would send and compares it with the one echo.lu last accepted,
    so an unrelated save costs a hash and no HTTP request — which makes a
    snapshot receiver (a second SELECT on every event save, kept in sync with a
    field list by hand) pure overhead. It also means the receiver cannot get
    the field list *wrong*: publishing, cancelling, going private, retitling,
    moving venue and re-pricing all reach echo.lu through the same one check.

    Enqueued rather than called inline, and on_commit rather than immediately:
    the admin's save path would otherwise wait on a third-party HTTP round trip
    before rendering, and a task that beat the transaction would read the
    pre-save row (or, for a new event, no row at all).
    """
    from .services import echo_lu

    if not echo_lu.is_sync_enabled():
        return

    from .tasks import sync_event_to_echo_task

    event_id = instance.pk
    transaction.on_commit(lambda: sync_event_to_echo_task.enqueue(event_id=event_id))


_echo_deletions = threading.local()

# What separates one delete's take-downs from the next delete's. The callbacks
# of a single commit run back to back in one loop, so the only thing between
# consecutive members of a burst is the HTTP call this function just made —
# measured from when that call *finished*, the gap is effectively zero. Any
# real pause means a different delete, on a thread the pool happened to reuse.
_ECHO_DELETE_BURST_IDLE_SECONDS = 1.0


def _withdraw_deleted_echo_listing(event_pk, experience_id):
    """Take down one listing whose event has just been deleted.

    Registered per event and run after the delete commits. Two things follow
    from *after*:

    * A rolled-back delete withdraws nothing. Doing this at pre_delete meant
      an aborted delete still pulled the listing, leaving an event that exists
      and a listing that does not — with no row saying so, since the rollback
      restored the row too.
    * No HTTP happens inside the delete's transaction. Django's collector
      wraps the whole cascade in one, and holding it open across a series of
      third-party calls is what every other entry point here was bounded to
      avoid.

    A bulk delete arrives as N of these, back to back, so a per-call timeout
    alone would multiply by N. They share a wall-clock budget instead: the
    first call of a burst starts the clock, and one that finds it spent logs
    rather than dials.

    A burst is delimited by an **idle gap**, not by the budget. Measuring the
    gap from the first call of the burst left a window — anything from
    ``budget - timeout`` to ``budget`` after it — in which an unrelated later
    delete on the same worker thread was refused *and* did not start a burst
    of its own, so it silently skipped its take-down while its row was already
    gone. The thread-local has no transaction or request boundary of its own,
    so the boundary has to be inferred, and the end of the previous call is the
    one signal that actually distinguishes the two cases.

    Whatever is not reached is logged with its id. The event is gone, so
    nothing can retry from the database: this log line is the last copy.
    """
    from .services import echo_lu

    timeout = getattr(settings, "ECHO_LU_SIGNAL_TIMEOUT_SECONDS", 5)
    budget = getattr(settings, "ECHO_LU_ADMIN_BUDGET_SECONDS", 30)

    now = time.monotonic()
    started = getattr(_echo_deletions, "burst_started", None)
    finished = getattr(_echo_deletions, "burst_last_finished", None)
    if (
        started is None
        or finished is None
        or now - finished > _ECHO_DELETE_BURST_IDLE_SECONDS
    ):
        started = _echo_deletions.burst_started = now

    # Recorded however this call ends, including the refusal below: a burst
    # that has run out of budget still refuses back to back, and those
    # instantaneous refusals must not read as an idle gap that hands the rest
    # of the same bulk delete a fresh budget each.
    try:
        if now - started > budget - timeout:
            logger.error(
                "[ECHO] Out of time withdrawing experience %s for deleted event "
                "%s; it is live and untracked — remove it in the organiser back "
                "office, or find it with `sync_events_to_echo --audit`",
                experience_id,
                event_pk,
            )
            return

        client = echo_lu.EchoLuClient(timeout=timeout, max_retries=0)
        try:
            client.unpublish_experience(experience_id)
        except echo_lu.EchoLuError as exc:
            logger.error(
                "[ECHO] Could not withdraw experience %s for deleted event %s "
                "(%s); it is live and untracked — remove it in the organiser "
                "back office, or find it with `sync_events_to_echo --audit`",
                experience_id,
                event_pk,
                exc,
            )
    finally:
        _echo_deletions.burst_last_finished = time.monotonic()


@receiver(pre_delete, sender=MeetupEvent)
def withdraw_from_echo_lu_before_delete(sender, instance, **kwargs):
    """Note the listing's id while the row that holds it still exists.

    `EchoExperienceSync` cascades with the event, and the experience id is the
    only handle echo.lu gives us. Once the row is gone the listing is still
    public and nothing left in the database can name it — no sweep, no admin
    action, no retry. `--audit` can still spot it as untracked, but that is a
    person noticing later, not a mechanism.

    So the id is captured here, where it is readable, and the take-down itself
    happens after the delete commits — see
    :func:`_withdraw_deleted_echo_listing`. No network here; this fires once
    per instance for *any* delete reaching this model, including Django's
    stock bulk action and any cascade from somewhere else.

    The row is read **under its own lock**, not off the instance. Two things
    went wrong with the plain attribute read:

    * ``instance.echo_sync`` can be a cached copy from whenever the event was
      loaded, and an id written since would not be on it.
    * Even a fresh unlocked read sees only committed state. A first publish
      holds this row locked across its POST, so a delete racing it reads the
      pre-create blank id, concludes there is no listing, and schedules
      nothing — then the cascade waits for that lock, deletes the row the
      winner just wrote the id into, and the new listing is public with
      nothing left that can name it. The lock is not an extra cost: the
      cascade's own DELETE takes it moments later regardless, so this only
      moves the wait earlier, to where the answer still matters.
    """
    from .models.echo_lu import EchoExperienceSync
    from .services import echo_lu

    sync = EchoExperienceSync.objects.select_for_update().filter(event=instance).first()
    if sync is None or not sync.experience_id:
        return
    # Already confirmed down, and the id is kept on purpose so a republish
    # reuses the listing — so there is nothing to take down and dialling anyway
    # spends the batch's shared budget on a no-op. That is not merely wasteful:
    # enough already-down events ahead of a live one in a bulk delete push its
    # callback past the budget, and its row is gone by then.
    #
    # CANCELLED is deliberately NOT in this set. That listing is still on
    # display — a public notice with title, venue and date — and the delete is
    # the last moment anything can take it down. FAILED is not either: the last
    # attempt failed, so whether the listing is up is exactly what we do not
    # know, and a failed take-down means it certainly is.
    if sync.status in (
        EchoExperienceSync.Status.WITHDRAWN,
        EchoExperienceSync.Status.SUPPRESSED,
    ):
        return
    if not echo_lu.is_sync_enabled():
        logger.warning(
            "[ECHO] Event %s is being deleted while echo.lu sync is off; "
            "experience %s stays live and untracked — remove it in the "
            "organiser back office",
            instance.pk,
            sync.experience_id,
        )
        return

    # Bound to this instance rather than accumulated in a list: a batch built
    # up here would have to survive a transaction that may roll back, and a
    # stale one carried into the next delete would withdraw a listing whose
    # event is still very much alive.
    event_pk = instance.pk
    experience_id = sync.experience_id
    transaction.on_commit(
        lambda: _withdraw_deleted_echo_listing(event_pk, experience_id)
    )


@receiver(post_save, sender=MeetupEvent)
def promote_waitlist_on_capacity_increase(sender, instance, created, **kwargs):
    """
    Auto-promote waitlisted users when event capacity is increased.

    Triggers when a MeetupEvent is saved (not created) and there are waitlisted
    registrations while the event is not full. Promotes candidates one at a time
    using the gender-aware _promote_from_waitlist() logic.
    """
    if created:
        return

    # This fires on *any* save of a MeetupEvent, not just a capacity change, so
    # without this guard simply opening a finished event in the admin and
    # hitting Save would confirm its leftover waitlist and email each of them a
    # registration confirmation for a party that already happened.
    #
    # Marking attendees as no_show makes this materially more likely, not less:
    # no_show rows drop out of get_confirmed_count(), so a full event reads as
    # having free seats again the moment the door work is tidied up.
    if not instance.accepts_waitlist_promotion:
        return

    # Quick check: any waitlisted registrations?
    if not EventRegistration.objects.filter(event=instance, status="waitlist").exists():
        return

    # Only proceed if event is not full
    if instance.is_full:
        return

    from django.db import transaction
    from .views_events import _promote_from_waitlist
    from .email_helpers import (
        send_event_payment_pending_notification,
        send_event_registration_confirmation,
    )

    with transaction.atomic():
        locked_event = MeetupEvent.objects.select_for_update().get(pk=instance.pk)

        # Re-read eligibility from the *locked* row. The check above ran
        # against the post-save instance, which is a snapshot: another
        # transaction may have cancelled or unpublished the event between that
        # read and this lock, and the loop below would then confirm and email
        # waitlisted members for it. Same reasoning as
        # `promote_waitlist_on_cancellation._promote_after_commit`.
        if not locked_event.accepts_waitlist_promotion:
            return

        promoted_registrations = []
        while not locked_event.is_full:
            promoted = _promote_from_waitlist(locked_event)
            if not promoted:
                break
            promoted_registrations.append(promoted)
            # Refresh to pick up updated counts
            locked_event.refresh_from_db()

    # Send confirmation emails outside the transaction
    for reg in promoted_registrations:
        try:
            # Paid events admit as "pending" (Pending Payment), so the promoted
            # seat needs the payment ask, not a confirmation it hasn't earned.
            if reg.status == "pending":
                send_event_payment_pending_notification(reg)
            else:
                send_event_registration_confirmation(reg)
        except Exception as e:
            logger.error(
                "Failed to send waitlist promotion email for user %s, event %s: %s",
                reg.user.pk,
                instance.id,
                type(e).__name__,
            )

    if promoted_registrations:
        logger.info(
            f"Event {instance.id}: Auto-promoted {len(promoted_registrations)} "
            f"waitlisted user(s) after capacity change"
        )


@receiver(post_save, sender=MeetupEvent)
def setup_event_voting(sender, instance, created, **kwargs):
    """
    When a speed dating event with activity voting is created or updated:
    1. Ensure GlobalActivityOption records exist (auto-populate if missing)
    2. Auto-create EventVotingSession with sensible defaults
    """
    if not instance.enable_activity_voting:
        return

    try:
        # Step 1: Ensure GlobalActivityOption records exist
        if not GlobalActivityOption.objects.filter(is_active=True).exists():
            from django.core.management import call_command

            call_command("populate_global_activity_options")
            logger.info(
                "Auto-populated GlobalActivityOption records for event %s", instance.id
            )

        # Step 2: Auto-create EventVotingSession if it doesn't exist
        if not EventVotingSession.objects.filter(event=instance).exists():
            from datetime import timedelta

            # Defaults: voting starts 15 min after event, lasts 30 min
            voting_start = instance.date_time + timedelta(minutes=15)
            voting_end = voting_start + timedelta(minutes=30)

            EventVotingSession.objects.create(
                event=instance,
                is_active=True,
                voting_start_time=voting_start,
                voting_end_time=voting_end,
            )
            logger.info(
                "Auto-created EventVotingSession for event %s (voting: %s to %s)",
                instance.id,
                voting_start,
                voting_end,
            )
    except Exception as e:
        logger.error(
            "Failed to setup voting for event %s: %s", instance.id, type(e).__name__
        )


def get_high_res_facebook_photo_url(facebook_id, access_token=None):
    """
    Get high-resolution Facebook profile photo URL.

    Facebook Graph API allows requesting specific dimensions.
    We request 720x720 which is the max for profile pictures.

    Args:
        facebook_id: The Facebook user ID
        access_token: Optional access token for authenticated requests

    Returns:
        str: URL to high-resolution photo, or None if unavailable
    """
    # Request 720x720 photo (maximum size for profile pictures)
    # Using redirect=false returns JSON with the actual URL
    # Use Graph API v24.0 to match settings.py
    url = f"https://graph.facebook.com/v24.0/{facebook_id}/picture?width=720&height=720&redirect=false"

    if access_token:
        url += f"&access_token={access_token}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("data", {}).get("url"):
            return data["data"]["url"]
    except Exception as e:
        logger.warning(f"Could not get high-res Facebook photo: {str(e)}")

    return None


# Centralized gender mapping to avoid duplication
FACEBOOK_GENDER_MAPPING = {
    "male": "M",
    "female": "F",
    "non-binary": "NB",
    "nonbinary": "NB",
    "trans": "NB",
    "transgender": "NB",
    "genderqueer": "NB",
    "genderfluid": "NB",
    "agender": "NB",
    "bigender": "NB",
    "pangender": "NB",
    "two-spirit": "NB",
}


def download_and_save_facebook_photo(profile, photo_url, user_id):
    """
    Download photo from URL and save to CrushProfile.

    Args:
        profile: CrushProfile instance
        photo_url: URL to download photo from
        user_id: User ID for filename

    Returns:
        bool: True if photo was saved, False otherwise
    """
    try:
        response = requests.get(photo_url, timeout=15)
        response.raise_for_status()

        # Validate content type is an image
        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            logger.warning(
                f"Facebook photo URL returned non-image content: {content_type}"
            )
            return False

        # Determine file extension from content type
        ext = "jpg"  # Default
        if "png" in content_type:
            ext = "png"
        elif "gif" in content_type:
            ext = "gif"
        elif "webp" in content_type:
            ext = "webp"

        filename = f"facebook_{user_id}.{ext}"
        raw_file = ContentFile(response.content, name=filename)
        processed = process_uploaded_image(raw_file, filename)
        profile.photo_1.save(filename, processed, save=False)
        return True
    except requests.exceptions.Timeout:
        logger.error(f"Timeout downloading Facebook photo for user {user_id}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading Facebook photo for user {user_id}: {str(e)}")
    except Exception as e:
        logger.error(
            f"Unexpected error saving Facebook photo for user {user_id}: {str(e)}"
        )

    return False


def update_profile_from_facebook_data(profile, extra_data):
    """
    Update CrushProfile fields from Facebook extra_data.

    Args:
        profile: CrushProfile instance
        extra_data: Dictionary of Facebook user data

    Returns:
        bool: True if any fields were updated
    """
    updated = False

    # Set birthday if available and not already set
    if extra_data.get("birthday") and not profile.date_of_birth:
        try:
            # Facebook birthday format: "MM/DD/YYYY"
            birthday = datetime.strptime(extra_data["birthday"], "%m/%d/%Y").date()
            profile.date_of_birth = birthday
            updated = True
            # Log without birthday details (avoid clear-text PII)
            logger.info(f"Set date_of_birth from Facebook for profile {profile.pk}")
        except ValueError:
            # Don't log the error details (could contain PII)
            logger.error(f"Error parsing Facebook birthday for profile {profile.pk}")

    # Set gender if available and not already set
    if extra_data.get("gender") and not profile.gender:
        fb_gender = extra_data["gender"].lower()
        # Map known genders, default to 'O' (Other) for custom/unknown
        profile.gender = FACEBOOK_GENDER_MAPPING.get(fb_gender, "O")
        updated = True
        logger.info(f"Set gender from Facebook for profile {profile.pk}")

    return updated


def _get_facebook_photo_url(extra_data, access_token=None):
    """
    Get the best available Facebook photo URL.

    Tries high-res first, falls back to standard picture from extra_data.

    Args:
        extra_data: Facebook extra_data dictionary
        access_token: Optional access token for authenticated requests

    Returns:
        str: Photo URL or None
    """
    facebook_id = extra_data.get("id")
    photo_url = None

    # Try high-resolution photo first
    if facebook_id:
        photo_url = get_high_res_facebook_photo_url(facebook_id, access_token)
        if photo_url:
            logger.info("Got high-res Facebook photo URL")
            return photo_url

    # Fallback to standard picture from extra_data
    if "picture" in extra_data:
        if isinstance(extra_data["picture"], dict):
            photo_url = extra_data["picture"].get("data", {}).get("url")
        else:
            photo_url = extra_data["picture"]
        if photo_url:
            logger.info("Using fallback Facebook photo URL")

    return photo_url


@receiver(pre_social_login)
def update_facebook_profile_on_login(sender, request, sociallogin, **kwargs):
    """
    Update CrushProfile with Facebook data when user logs in with Facebook.
    Only processes Facebook logins for Crush.lu domain.

    This handles EXISTING users logging in again - updates profile if photo missing.
    Also sets a thread-local flag for the post_save handler.
    """
    # Reset the flag at the start (only for Facebook provider)
    if sociallogin.account.provider == "facebook":
        _thread_local.is_crush_facebook_login = False

    # Only process Facebook logins
    if sociallogin.account.provider != "facebook":
        return

    # Only process for crush.lu domain
    if not _is_crush_domain(request):
        logger.info("Skipping Facebook login processing for non-Crush domain")
        return

    # Set flag to indicate this is a crush.lu Facebook login
    _thread_local.is_crush_facebook_login = True

    # Store request for post_save handler (needed for welcome email)
    if not sociallogin.is_existing:
        _thread_local.oauth_signup_request = request

    # Track Crush.lu consent for OAuth signups (implicit consent via OAuth)
    if not sociallogin.is_existing:
        from crush_lu.oauth_statekit import get_client_ip

        _thread_local.oauth_consent_data = {
            "crushlu_consent": True,  # Implicit consent via OAuth signup
            "crushlu_consent_ip": get_client_ip(request),
        }

    logger.info("pre_social_login signal received for Facebook provider on crush.lu")

    try:
        extra_data = sociallogin.account.extra_data
        logger.debug(f"Facebook extra_data keys: {list(extra_data.keys())}")

        # If user exists, update their CrushProfile
        if hasattr(sociallogin.user, "id") and sociallogin.user.id:
            try:
                profile = CrushProfile.objects.get(user=sociallogin.user)

                # Get access token for authenticated request
                access_token = sociallogin.token.token if sociallogin.token else None

                # Download and save profile photo if available and not already set
                if not profile.photo_1:
                    photo_url = _get_facebook_photo_url(extra_data, access_token)
                    if photo_url:
                        if download_and_save_facebook_photo(
                            profile, photo_url, sociallogin.user.id
                        ):
                            logger.info(
                                f"Saved Facebook photo for user {sociallogin.user.email}"
                            )

                # Update profile data from Facebook
                update_profile_from_facebook_data(profile, extra_data)

                profile.save()
                logger.info(
                    f"Updated CrushProfile for existing user {sociallogin.user.email}"
                )

            except CrushProfile.DoesNotExist:
                logger.info(
                    f"No CrushProfile exists yet for user {sociallogin.user.email}"
                )

        # Refresh persisted social photo cache (fresh token available now)
        # Only for existing users - new users are handled in post_save
        if hasattr(sociallogin.user, "id") and sociallogin.user.id:
            try:
                from .social_photos import refresh_social_photo_cache

                token = sociallogin.token if sociallogin.token else None
                refresh_social_photo_cache(sociallogin.account, token)
            except Exception as e:
                logger.warning(f"Failed to refresh Facebook photo cache: {e}")

    except Exception as e:
        logger.error(f"Error in pre_social_login handler: {str(e)}", exc_info=True)


@receiver(pre_social_login)
def detect_apple_relay_email(sender, request, sociallogin, **kwargs):
    """
    Detect Apple "Hide My Email" relay addresses on new signups.

    When a user signs in with Apple and chooses "Hide My Email", Apple provides
    a relay address like xxx@privaterelay.appleid.com. If this is a new signup
    (not an existing user), set a session flag so the oauth_landing view can
    redirect to the account linking prompt.
    """
    if sociallogin.account.provider != "apple":
        return

    if not _is_crush_domain(request):
        return

    email = sociallogin.account.extra_data.get("email", "")
    if email.endswith("@privaterelay.appleid.com") and not sociallogin.is_existing:
        request.session["apple_relay_needs_linking"] = True
        logger.info(
            "[APPLE-RELAY] New signup with Hide My Email detected, "
            "setting linking prompt flag"
        )


@receiver(post_save, sender=SocialAccount)
def create_crush_profile_from_facebook(sender, instance, created, **kwargs):
    """
    Create CrushProfile when a new Facebook SocialAccount is created.
    Pre-fill profile data from Facebook information.

    This handles NEW users signing up via Facebook - creates profile with Facebook data.
    Only creates profile if the login originated from crush.lu domain.
    """
    if instance.provider != "facebook":
        return

    if not created:
        return

    # Only create CrushProfile if login was from crush.lu
    # This flag is set by update_facebook_profile_on_login in pre_social_login
    if not getattr(_thread_local, "is_crush_facebook_login", False):
        logger.info(
            f"Skipping CrushProfile creation for Facebook user {instance.user.email} - not a crush.lu login"
        )
        return

    logger.info(
        f"SocialAccount post_save signal for Facebook on crush.lu (user: {instance.user.email})"
    )

    try:
        # Get or create CrushProfile
        profile, profile_created = CrushProfile.objects.get_or_create(
            user=instance.user
        )

        if profile_created:
            logger.info(
                f"Created new CrushProfile for Facebook user {instance.user.email}"
            )

        extra_data = instance.extra_data

        # Download and save Facebook photo if not already set
        # Note: We don't have access token in post_save, but high-res endpoint works without it
        if not profile.photo_1:
            photo_url = _get_facebook_photo_url(extra_data)
            if photo_url:
                if download_and_save_facebook_photo(
                    profile, photo_url, instance.user.id
                ):
                    logger.info(
                        f"Saved Facebook photo for new user {instance.user.email}"
                    )

        # Update profile data from Facebook
        update_profile_from_facebook_data(profile, extra_data)

        # Don't set completion_status - let model default handle it
        # The view will detect empty profiles and start at step 1

        profile.save()
        logger.info("Updated CrushProfile from Facebook data in post_save")

        # Persist social photo to cache for token-expiry fallback
        try:
            from .social_photos import refresh_social_photo_cache

            refresh_social_photo_cache(instance)
        except Exception as e:
            logger.warning(f"Failed to persist Facebook photo cache for new user: {e}")

        # Send welcome email for new OAuth signups
        if profile_created:
            oauth_request = getattr(_thread_local, "oauth_signup_request", None)
            if oauth_request:
                try:
                    from .email_helpers import send_welcome_email

                    result = send_welcome_email(instance.user, oauth_request)
                    logger.info(
                        f"Welcome email sent to Facebook OAuth user {instance.user.email}: {result}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send welcome email to Facebook OAuth user {instance.user.email}: {e}",
                        exc_info=True,
                    )
                finally:
                    _thread_local.oauth_signup_request = None

    except Exception as e:
        logger.error(
            f"Error in SocialAccount post_save handler: {str(e)}", exc_info=True
        )


@receiver(user_logged_in)
def store_oauth_result_for_duplicate_handling(sender, request, user, **kwargs):
    """
    Store OAuth result in database for handling duplicate callback requests.

    On Android PWA, duplicate OAuth callbacks can arrive before cookies are committed.
    The first callback processes successfully but the second sees no session.
    By storing the result in the database, the second request can recover the auth.

    This signal fires AFTER Allauth successfully logs in the user.
    We store the user_id and redirect URL so duplicate requests can complete.
    """
    # Get state_id from request GET params (should be available on callback URL)
    state_id = request.GET.get("state")
    if not state_id:
        # Not an OAuth login, skip
        return

    try:
        from crush_lu.models import OAuthState

        # Determine redirect URL based on profile existence
        has_profile = hasattr(user, "crushprofile")
        redirect_url = "/dashboard/" if has_profile else "/create-profile/"

        # Update the OAuth state with completion info
        updated = OAuthState.objects.filter(state_id=state_id).update(
            auth_completed=True,
            auth_user_id=user.id,
            auth_redirect_url=redirect_url,
            last_callback_at=timezone.now(),
        )

        if updated:
            logger.info(
                f"[OAUTH-RESULT] Stored OAuth result for state {state_id[:8]}... "
                f"(user_id={user.id}, redirect={redirect_url})"
            )
        else:
            logger.warning(
                f"[OAUTH-RESULT] Could not find OAuth state {state_id[:8]}... to store result"
            )

    except Exception as e:
        # Non-critical - just log and continue
        logger.error(f"[OAUTH-RESULT] Error storing OAuth result: {e}")


@receiver(user_logged_in)
def check_special_user_experience(sender, request, user, **kwargs):
    """
    Check if the logged-in user matches a special user experience configuration.
    If matched, activate the special experience in the session.
    Only processes for crush.lu domain.
    """
    # Only process for crush.lu domain
    try:
        host = request.get_host().split(":")[0].lower()
    except KeyError:
        # During tests, the request may not have SERVER_NAME set
        return
    if host not in CRUSH_LU_DOMAINS:
        return

    try:
        # Check if there's a matching special experience
        # Prioritize linked_user (gifts), then fall back to name match (legacy)
        special_experience = SpecialUserExperience.objects.filter(
            Q(is_active=True)
            & (
                Q(linked_user=user)  # Direct link (gifts)
                | Q(
                    first_name__iexact=user.first_name,
                    last_name__iexact=user.last_name,
                    linked_user__isnull=True,  # Only name-match if no linked_user
                )
            )
        ).first()

        if special_experience:
            # Activate special experience in session
            request.session["special_experience_active"] = True
            request.session["special_experience_id"] = special_experience.id
            request.session["special_experience_data"] = {
                "welcome_title": special_experience.custom_welcome_title,
                "welcome_message": special_experience.custom_welcome_message,
                "theme_color": special_experience.custom_theme_color,
                "animation_style": special_experience.animation_style,
                "vip_badge": special_experience.vip_badge,
                "custom_landing_url": special_experience.custom_landing_url,
            }

            # Track the trigger
            special_experience.trigger()

            # Auto-approve profile if configured
            if special_experience.auto_approve_profile:
                try:
                    profile = CrushProfile.objects.get(user=user)
                    if not profile.is_approved:
                        profile.is_approved = True
                        profile.approved_at = timezone.now()
                        profile.verification_status = "verified"
                        if not profile.verification_method:
                            profile.verification_method = "admin"
                        profile.save()
                        logger.info(
                            f"Auto-approved profile for special user: {user.email}"
                        )
                        # Award referral bonus points to referrer (if referred)
                        from .referrals import check_and_apply_profile_approved_reward

                        check_and_apply_profile_approved_reward(profile)
                except CrushProfile.DoesNotExist:
                    pass

            logger.info(
                f"✨ Special experience activated for {user.first_name} {user.last_name}"
            )
        else:
            # Clear any existing special experience from session
            request.session.pop("special_experience_active", None)
            request.session.pop("special_experience_id", None)
            request.session.pop("special_experience_data", None)

    except Exception as e:
        logger.error(
            f"Error in check_special_user_experience handler: {str(e)}", exc_info=True
        )


# =============================================================================
# GOOGLE OAUTH PROFILE INTEGRATION
# =============================================================================


def get_high_res_google_photo_url(extra_data):
    """
    Get high-resolution Google profile photo URL.

    Google profile photos can be resized by modifying the URL:
    - Original: https://lh3.googleusercontent.com/a/...=s96-c
    - High-res: https://lh3.googleusercontent.com/a/...=s720-c

    Args:
        extra_data: Google extra_data dictionary containing 'picture' field

    Returns:
        str: URL to high-resolution photo, or None if unavailable
    """
    picture_url = extra_data.get("picture")
    if not picture_url:
        return None

    # Google photo URLs end with size parameter like =s96-c
    # We can replace this with =s720-c for higher resolution
    import re

    # Match patterns like =s96-c, =s96, ?sz=96
    if "=s" in picture_url:
        # Replace size parameter with 720
        high_res_url = re.sub(r"=s\d+(-c)?", "=s720-c", picture_url)
        logger.info("Enhanced Google photo URL to high-res")
        return high_res_url
    elif "?sz=" in picture_url:
        # Alternative size parameter format
        high_res_url = re.sub(r"\?sz=\d+", "?sz=720", picture_url)
        logger.info("Enhanced Google photo URL to high-res (sz format)")
        return high_res_url

    # Return original URL if no size parameter found
    return picture_url


def download_and_save_google_photo(profile, photo_url, user_id):
    """
    Download photo from Google URL and save to CrushProfile.

    Args:
        profile: CrushProfile instance
        photo_url: URL to download photo from
        user_id: User ID for filename

    Returns:
        bool: True if photo was saved, False otherwise
    """
    try:
        response = requests.get(photo_url, timeout=15)
        response.raise_for_status()

        # Validate content type is an image
        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            logger.warning(
                f"Google photo URL returned non-image content: {content_type}"
            )
            return False

        # Determine file extension from content type
        ext = "jpg"  # Default
        if "png" in content_type:
            ext = "png"
        elif "gif" in content_type:
            ext = "gif"
        elif "webp" in content_type:
            ext = "webp"

        filename = f"google_{user_id}.{ext}"
        raw_file = ContentFile(response.content, name=filename)
        processed = process_uploaded_image(raw_file, filename)
        profile.photo_1.save(filename, processed, save=False)
        return True
    except requests.exceptions.Timeout:
        logger.error(f"Timeout downloading Google photo for user {user_id}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading Google photo for user {user_id}: {str(e)}")
    except Exception as e:
        logger.error(
            f"Unexpected error saving Google photo for user {user_id}: {str(e)}"
        )

    return False


@receiver(pre_social_login)
def update_google_profile_on_login(sender, request, sociallogin, **kwargs):
    """
    Update CrushProfile with Google data when user logs in with Google.
    Only processes Google logins for Crush.lu domain.

    This handles EXISTING users logging in again - updates profile if photo missing.
    Also sets a thread-local flag for the post_save handler.
    """
    # Reset the flag at the start (only for Google provider)
    if sociallogin.account.provider == "google":
        _thread_local.is_crush_google_login = False

    # Only process Google logins
    if sociallogin.account.provider != "google":
        return

    # Only process for crush.lu domain
    if not _is_crush_domain(request):
        logger.info("Skipping Google login processing for non-Crush domain")
        return

    # Set flag to indicate this is a crush.lu Google login
    _thread_local.is_crush_google_login = True

    # Store request for post_save handler (needed for welcome email)
    if not sociallogin.is_existing:
        _thread_local.oauth_signup_request = request

    # Track Crush.lu consent for OAuth signups (implicit consent via OAuth)
    if not sociallogin.is_existing:
        from crush_lu.oauth_statekit import get_client_ip

        _thread_local.oauth_consent_data = {
            "crushlu_consent": True,  # Implicit consent via OAuth signup
            "crushlu_consent_ip": get_client_ip(request),
        }

    logger.info("pre_social_login signal received for Google provider on crush.lu")

    try:
        extra_data = sociallogin.account.extra_data
        logger.debug(f"Google extra_data keys: {list(extra_data.keys())}")

        # If user exists, update their CrushProfile
        if hasattr(sociallogin.user, "id") and sociallogin.user.id:
            try:
                profile = CrushProfile.objects.get(user=sociallogin.user)

                # Download and save profile photo if available and not already set
                if not profile.photo_1:
                    photo_url = get_high_res_google_photo_url(extra_data)
                    if photo_url:
                        if download_and_save_google_photo(
                            profile, photo_url, sociallogin.user.id
                        ):
                            logger.info(
                                f"Saved Google photo for user {sociallogin.user.email}"
                            )

                profile.save()
                logger.info(
                    f"Updated CrushProfile for existing Google user {sociallogin.user.email}"
                )

            except CrushProfile.DoesNotExist:
                logger.info(
                    f"No CrushProfile exists yet for user {sociallogin.user.email}"
                )

        # Refresh persisted social photo cache
        # Only for existing users - new users are handled in post_save
        if hasattr(sociallogin.user, "id") and sociallogin.user.id:
            try:
                from .social_photos import refresh_social_photo_cache

                refresh_social_photo_cache(sociallogin.account)
            except Exception as e:
                logger.warning(f"Failed to refresh Google photo cache: {e}")

    except Exception as e:
        logger.error(
            f"Error in Google pre_social_login handler: {str(e)}", exc_info=True
        )


@receiver(post_save, sender=SocialAccount)
def create_crush_profile_from_google(sender, instance, created, **kwargs):
    """
    Create CrushProfile when a new Google SocialAccount is created.
    Pre-fill profile data from Google information including profile photo.

    This handles NEW users signing up via Google - creates profile with Google photo.
    Only creates profile if the login originated from crush.lu domain.
    """
    if instance.provider != "google":
        return

    if not created:
        return

    # Only create CrushProfile if login was from crush.lu
    # This flag is set by update_google_profile_on_login in pre_social_login
    if not getattr(_thread_local, "is_crush_google_login", False):
        logger.info(
            f"Skipping CrushProfile creation for Google user {instance.user.email} - not a crush.lu login"
        )
        return

    logger.info(
        f"SocialAccount post_save signal for Google on crush.lu (user: {instance.user.email})"
    )

    try:
        # Get or create CrushProfile
        profile, profile_created = CrushProfile.objects.get_or_create(
            user=instance.user
        )

        if profile_created:
            logger.info(
                f"Created new CrushProfile for Google user {instance.user.email}"
            )

        extra_data = instance.extra_data

        # Download and save Google photo if not already set
        if not profile.photo_1:
            photo_url = get_high_res_google_photo_url(extra_data)
            if photo_url:
                if download_and_save_google_photo(profile, photo_url, instance.user.id):
                    logger.info(
                        f"Saved Google photo for new user {instance.user.email}"
                    )

        profile.save()
        logger.info("Updated CrushProfile from Google data in post_save")

        # Persist social photo to cache for consistency
        try:
            from .social_photos import refresh_social_photo_cache

            refresh_social_photo_cache(instance)
        except Exception as e:
            logger.warning(f"Failed to persist Google photo cache for new user: {e}")

        # Send welcome email for new OAuth signups
        if profile_created:
            oauth_request = getattr(_thread_local, "oauth_signup_request", None)
            if oauth_request:
                try:
                    from .email_helpers import send_welcome_email

                    result = send_welcome_email(instance.user, oauth_request)
                    logger.info(
                        f"Welcome email sent to Google OAuth user {instance.user.email}: {result}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send welcome email to Google OAuth user {instance.user.email}: {e}",
                        exc_info=True,
                    )
                finally:
                    _thread_local.oauth_signup_request = None

    except Exception as e:
        logger.error(
            f"Error in Google SocialAccount post_save handler: {str(e)}", exc_info=True
        )


# =============================================================================
# MICROSOFT OAUTH PROFILE INTEGRATION
# =============================================================================


def get_microsoft_photo_url(extra_data):
    """
    Get Microsoft profile photo URL.

    Microsoft Graph API provides profile photos via a separate endpoint.
    The extra_data doesn't typically include the photo URL directly.
    We need to use the Graph API to fetch it.

    Args:
        extra_data: Microsoft extra_data dictionary

    Returns:
        str: URL to fetch photo from, or None if unavailable
    """
    # Microsoft doesn't provide photo URL in extra_data like Google/Facebook
    # The photo must be fetched from Graph API: /me/photo/$value
    # For now, we return None - photos would need to be fetched with access token
    return None


@receiver(pre_social_login)
def update_microsoft_profile_on_login(sender, request, sociallogin, **kwargs):
    """
    Update CrushProfile with Microsoft data when user logs in with Microsoft.
    Only processes Microsoft logins for Crush.lu domain.

    This handles EXISTING users logging in again.
    Also sets a thread-local flag for the post_save handler.
    """
    # Reset the flag at the start (only for Microsoft provider)
    if sociallogin.account.provider == "microsoft":
        _thread_local.is_crush_microsoft_login = False

    # Only process Microsoft logins
    if sociallogin.account.provider != "microsoft":
        return

    # Only process for crush.lu domain
    if not _is_crush_domain(request):
        logger.info("Skipping Microsoft login processing for non-Crush domain")
        return

    # Set flag to indicate this is a crush.lu Microsoft login
    _thread_local.is_crush_microsoft_login = True

    # Store request for post_save handler (needed for welcome email)
    if not sociallogin.is_existing:
        _thread_local.oauth_signup_request = request

    # Track Crush.lu consent for OAuth signups (implicit consent via OAuth)
    if not sociallogin.is_existing:
        from crush_lu.oauth_statekit import get_client_ip

        _thread_local.oauth_consent_data = {
            "crushlu_consent": True,  # Implicit consent via OAuth signup
            "crushlu_consent_ip": get_client_ip(request),
        }

    logger.info("pre_social_login signal received for Microsoft provider on crush.lu")

    try:
        extra_data = sociallogin.account.extra_data
        logger.debug(f"Microsoft extra_data keys: {list(extra_data.keys())}")
        logger.info(
            f"Microsoft extra_data: displayName={extra_data.get('displayName')}, "
            f"givenName={extra_data.get('givenName')}, surname={extra_data.get('surname')}, "
            f"mail={extra_data.get('mail')}, userPrincipalName={extra_data.get('userPrincipalName')}"
        )

        # If user exists, update their CrushProfile
        if hasattr(sociallogin.user, "id") and sociallogin.user.id:
            try:
                profile = CrushProfile.objects.get(user=sociallogin.user)
                profile.save()
                logger.info(
                    f"Updated CrushProfile for existing Microsoft user {sociallogin.user.email}"
                )

            except CrushProfile.DoesNotExist:
                logger.info(
                    f"No CrushProfile exists yet for user {sociallogin.user.email}"
                )

        # Refresh persisted social photo cache (fresh token available now)
        # Only for existing users - new users are handled in post_save
        if hasattr(sociallogin.user, "id") and sociallogin.user.id:
            try:
                from .social_photos import refresh_social_photo_cache

                token = sociallogin.token if sociallogin.token else None
                refresh_social_photo_cache(sociallogin.account, token)
            except Exception as e:
                logger.warning(f"Failed to refresh Microsoft photo cache: {e}")

    except Exception as e:
        logger.error(
            f"Error in Microsoft pre_social_login handler: {str(e)}", exc_info=True
        )


@receiver(post_save, sender=SocialAccount)
def create_crush_profile_from_microsoft(sender, instance, created, **kwargs):
    """
    Create CrushProfile when a new Microsoft SocialAccount is created.

    This handles NEW users signing up via Microsoft - creates basic profile.
    Note: Microsoft doesn't provide profile photo in OAuth extra_data like Google/Facebook.
    Only creates profile if the login originated from crush.lu domain.
    """
    if instance.provider != "microsoft":
        return

    if not created:
        return

    # Only create CrushProfile if login was from crush.lu
    # This flag is set by update_microsoft_profile_on_login in pre_social_login
    if not getattr(_thread_local, "is_crush_microsoft_login", False):
        logger.info(
            f"Skipping CrushProfile creation for Microsoft user {instance.user.email} - not a crush.lu login"
        )
        return

    logger.info(
        f"SocialAccount post_save signal for Microsoft on crush.lu (user: {instance.user.email})"
    )

    try:
        # Get or create CrushProfile
        profile, profile_created = CrushProfile.objects.get_or_create(
            user=instance.user
        )

        if profile_created:
            logger.info(
                f"Created new CrushProfile for Microsoft user {instance.user.email}"
            )

        profile.save()
        logger.info("Updated CrushProfile from Microsoft data in post_save")

        # Persist social photo to cache for token-expiry fallback
        try:
            from .social_photos import refresh_social_photo_cache

            refresh_social_photo_cache(instance)
        except Exception as e:
            logger.warning(f"Failed to persist Microsoft photo cache for new user: {e}")

        # Send welcome email for new OAuth signups
        if profile_created:
            oauth_request = getattr(_thread_local, "oauth_signup_request", None)
            if oauth_request:
                try:
                    from .email_helpers import send_welcome_email

                    result = send_welcome_email(instance.user, oauth_request)
                    logger.info(
                        f"Welcome email sent to Microsoft OAuth user {instance.user.email}: {result}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send welcome email to Microsoft OAuth user {instance.user.email}: {e}",
                        exc_info=True,
                    )
                finally:
                    _thread_local.oauth_signup_request = None

    except Exception as e:
        logger.error(
            f"Error in Microsoft SocialAccount post_save handler: {str(e)}",
            exc_info=True,
        )


# =============================================================================
# LUXID OIDC PROVIDER (POST Luxembourg CIAM)
# =============================================================================

# Gender mapping from OIDC standard values to CrushProfile codes
LUXID_GENDER_MAP = {
    "male": "M",
    "female": "F",
    "diverse": "NB",  # OIDC standard spelling
    "divers": "NB",  # French/German spelling used in LuxID UI
}


def _luxid_claims(extra_data):
    """Return a merged claims dict from allauth's nested OIDC envelope.

    Allauth 65.x stores the raw token response as
    ``{"id_token": {...}, "userinfo": {...}}``.  Its internal ``_pick_data()``
    prefers ``userinfo`` and ignores ``id_token`` entirely when ``userinfo``
    is present, so claims released only in the id_token (e.g. ``email``)
    are silently dropped.  We merge both sources — id_token first, userinfo
    on top — so userinfo wins for overlapping keys while id_token-only
    claims remain accessible.
    """
    if not isinstance(extra_data, dict):
        return {}
    id_token = extra_data.get("id_token") or {}
    userinfo = extra_data.get("userinfo") or {}
    merged = {**id_token, **userinfo}  # userinfo wins for overlapping keys
    return merged if merged else extra_data


_LUXID_TRUSTED_PREFIXES = ("+352", "+33", "+32", "+49", "+351")


def _is_trusted_luxid_phone(phone):
    """Return True if ``phone`` is an E.164 number from a LuxID-trusted country.

    Accepted country prefixes:
      +352  Luxembourg
      +33   France
      +32   Belgium
      +49   Germany
      +351  Portugal

    POST Luxembourg's CIAM can return any country's number; this list covers
    the cross-border population that Crush.lu serves.
    """
    return bool(phone) and phone.startswith(_LUXID_TRUSTED_PREFIXES)


def _luxid_phone_available(phone, profile):
    """Return True if ``phone`` can be written to ``profile`` without
    violating the ``unique_non_empty_phone_number`` constraint.

    CrushProfile has a partial-unique index on phone_number, so we must
    not assign a number another profile already owns. If we hit that
    case, log a warning (two accounts sharing the same LuxID-verified
    number is a data-integrity signal worth investigating) and skip
    the phone fields so the rest of the profile still saves.
    """
    if not phone:
        return False
    clash = (
        CrushProfile.objects.filter(phone_number=phone).exclude(pk=profile.pk).exists()
    )
    if clash:
        logger.warning(
            "LuxID phone_number clash detected; skipping phone prefill for "
            "profile pk=%s user_id=%s",
            profile.pk,
            profile.user_id,
        )
        return False
    return True


_PHONE_FIELDS = frozenset(["phone_number", "phone_verified", "phone_verified_at"])


def _luxid_save_profile(profile, updated_fields):
    """Save LuxID-sourced profile updates, bypassing the phone-protection lock.

    CrushProfile.save() prevents overwriting a verified phone to guard against
    accidental resets. LuxID is a higher-trust anchor (government CIAM), so all
    fields are written via a single QuerySet.update() that skips that guard.
    The non-phone fields LuxID provides (date_of_birth, gender, preferred_language)
    have no special save() logic, so bypassing save() for them is safe.

    A single post_save is deferred to on_commit() covering all updated fields,
    so downstream handlers (sync_profile_to_outlook, wallet-pass updates) fire
    exactly once, only after the transaction commits successfully.
    """
    all_fields = frozenset(updated_fields)
    with transaction.atomic():
        CrushProfile.objects.filter(pk=profile.pk).update(
            **{f: getattr(profile, f) for f in all_fields}
        )
        transaction.on_commit(
            lambda: post_save.send(
                sender=CrushProfile,
                instance=profile,
                created=False,
                update_fields=all_fields,
                using="default",
            )
        )


@receiver(pre_social_login)
def update_crush_profile_from_luxid(sender, request, sociallogin, **kwargs):
    """
    Update CrushProfile with LuxID OIDC data when user logs in.
    Only processes LuxID logins for Crush.lu domain.

    LuxID provides standard OIDC claims (Annex C6 of CIAM Agreement):
    - given_name, family_name, email (mapped in adapter populate_user)
    - birthdate, gender, phone_number, locale (mapped here to CrushProfile)
    """
    # Diagnostic — see what provider id allauth actually stores on the
    # SocialAccount for this login. For generic OpenID Connect providers
    # this is the SocialApp's provider_id (e.g. "luxid") but it can also
    # be the bare "openid_connect" id, depending on admin configuration.
    _prov = sociallogin.account.provider
    logger.info("[LUXID-DIAG] pre_social_login entry provider=%r", _prov)

    # Match LuxID in either spelling: the custom provider_id on the
    # SocialApp ("luxid") OR the generic OIDC fallback ("openid_connect").
    # Crush.lu only uses OIDC for LuxID, so both are safe to treat as LuxID.
    _is_luxid = _prov in ("luxid", "openid_connect")

    # Reset the flag at the start (only for LuxID provider)
    if _is_luxid:
        _thread_local.is_crush_luxid_login = False

    # Only process LuxID logins
    if not _is_luxid:
        return

    # Only process for crush.lu domain
    if not _is_crush_domain(request):
        return

    # Set flag for post_save handler
    _thread_local.is_crush_luxid_login = True

    # Store request for welcome email on new signups
    if not sociallogin.is_existing:
        _thread_local.oauth_signup_request = request

    # Track implicit consent for new OAuth signups
    if not sociallogin.is_existing:
        from crush_lu.oauth_statekit import get_client_ip

        _thread_local.oauth_consent_data = {
            "crushlu_consent": True,
            "crushlu_consent_ip": get_client_ip(request),
        }

    logger.info("pre_social_login signal received for LuxID provider on crush.lu")

    try:
        extra_data = sociallogin.account.extra_data

        # Allauth 65.x stores the raw OIDC response as
        # {"id_token": {...}, "userinfo": {...}} in SocialAccount.extra_data.
        # _pick_data() (used internally by allauth) prefers userinfo and drops
        # id_token entirely when userinfo is present.  Use _luxid_claims() to
        # merge both so we capture email/phone whether LuxID releases them via
        # userinfo or only embeds them in the id_token.
        _claims = _luxid_claims(extra_data)

        # Diagnostic: log which source has the email claim to aid debugging.
        try:
            _dbg_userinfo = extra_data.get("userinfo") or {}
            _dbg_id_token = extra_data.get("id_token") or {}
            logger.info(
                "[LUXID] envelope_keys=%s userinfo_has_email=%s id_token_has_email=%s merged_has_email=%s",
                sorted(extra_data.keys()),
                bool(_dbg_userinfo.get("email")),
                bool(_dbg_id_token.get("email")),
                bool(_claims.get("email")),
            )
        except Exception:
            pass

        # NOTE: sociallogin.lookup() (which drives email-based auto-connect)
        # runs BEFORE pre_social_login fires, so changes to email_addresses
        # here do NOT affect the auto-connect decision. The authoritative fix
        # for auto-connect is LuxIDProvider.extract_email_addresses() which
        # forces verified=True before lookup() runs.
        #
        # This block is kept as a belt-and-braces for the SocialSignupForm
        # pre-fill path (new-user signup) and as a guard against unexpected
        # allauth internals that may read email_addresses after this signal.
        if _claims.get("email"):
            from allauth.account.models import EmailAddress as _EmailAddress

            if sociallogin.email_addresses:
                # Ensure any existing addresses are marked verified.
                for _addr in sociallogin.email_addresses:
                    _addr.verified = True
            else:
                sociallogin.email_addresses = [
                    _EmailAddress(
                        email=_claims["email"],
                        verified=True,
                        primary=True,
                    )
                ]

            # Safety net: ensure user.email is populated for the SocialSignupForm.
            # populate_user() runs earlier but may miss the email if allauth's
            # _pick_data() discards the id_token when userinfo is present yet
            # the userinfo endpoint omits the email claim.
            if not getattr(sociallogin.user, "email", None):
                sociallogin.user.email = _claims["email"]
                logger.info("[LUXID] Safety net: set user.email from OIDC claims")

        # Phone-based existing-user lookup.
        # allauth's email auto-connect runs via sociallogin.lookup() BEFORE
        # this signal fires.  For users who registered with a different email
        # (or no email) but whose LuxID-verified phone matches a Crush.lu
        # account, we do a secondary lookup here and redirect the new social
        # login to their existing account by setting sociallogin.user.
        # allauth 65.x derives is_existing from bool(sociallogin.user.pk), so
        # once we point sociallogin.user at an existing DB user, allauth's
        # _authenticate() will call _login() (not process_signup()), saving
        # the new SocialAccount link and logging the user in seamlessly.
        _phone = _claims.get("phone_number")
        _is_connect_flow = getattr(sociallogin, "state", {}).get("process") == "connect"
        if (
            _phone
            and _is_trusted_luxid_phone(_phone)
            and not sociallogin.is_existing
            and not _is_connect_flow
        ):
            try:
                _existing_profile = CrushProfile.objects.select_related("user").get(
                    phone_number=_phone,
                    phone_verified=True,
                )
                _existing_user = _existing_profile.user
                logger.info(
                    "[LUXID] Phone-based lookup: connecting LuxID to existing user pk=%s",
                    _existing_user.pk,
                )
                # Clear signup thread-local flags BEFORE calling connect() so
                # the post_save signal fired by connect().save() sees clean
                # state.  Downstream handlers (create_user_data_consent and
                # the welcome-email branch in create_crush_profile_from_luxid)
                # check these flags and must not treat an existing account as
                # a brand-new registration.
                _thread_local.oauth_signup_request = None
                _thread_local.oauth_consent_data = None
                # Persist the SocialAccount and token now rather than relying
                # on allauth's _login() path to do it.  _login() only saves
                # the SocialAccount when _did_authenticate_by_email is set
                # (the email auto-connect flag); our phone branch never sets
                # that flag, so without an explicit connect() call the LuxID
                # social account would be logged in but never stored, causing
                # the same phone lookup to repeat on every subsequent login.
                sociallogin.connect(request, _existing_user)
            except CrushProfile.DoesNotExist:
                logger.debug(
                    "[LUXID] Phone-based lookup: no existing user for this phone"
                )
            except CrushProfile.MultipleObjectsReturned:
                logger.warning(
                    "[LUXID] Phone-based lookup: multiple profiles share the same phone, skipping"
                )
            except Exception as _phone_err:
                logger.error(
                    "[LUXID] Phone-based lookup error: %s", _phone_err, exc_info=True
                )

        # Update CrushProfile for existing users.
        # During the connect flow (email user linking LuxID for the first
        # time), allauth has not yet associated sociallogin.user with
        # request.user when pre_social_login fires, so sociallogin.user.id
        # is None. Fall back to request.user ONLY when this is explicitly a
        # connect process — never during a normal login, where falling back
        # to request.user could write the new LuxID claims into the current
        # session user's profile before they are logged out (cross-account
        # data corruption).
        _sl_user = sociallogin.user
        _is_connect = getattr(sociallogin, "state", {}).get("process") == "connect"
        if _is_connect and not (hasattr(_sl_user, "id") and _sl_user.id):
            _req_user = getattr(request, "user", None)
            if _req_user is not None and getattr(_req_user, "is_authenticated", False):
                _sl_user = _req_user

        if hasattr(_sl_user, "id") and _sl_user.id:
            try:
                profile = CrushProfile.objects.get(user=_sl_user)
                updated_fields = []

                # Map birthdate (OIDC format: YYYY-MM-DD)
                # For the dedicated 'luxid' provider, treat as authoritative
                # and overwrite any existing value. For the generic
                # 'openid_connect' fallback, only fill if empty — that path
                # matches any OIDC app, so we stay conservative in case a
                # second OIDC provider is ever configured on crush.lu.
                birthdate = _claims.get("birthdate")
                if birthdate and (_prov == "luxid" or not profile.date_of_birth):
                    try:
                        profile.date_of_birth = datetime.strptime(
                            birthdate, "%Y-%m-%d"
                        ).date()
                        updated_fields.append("date_of_birth")
                    except (ValueError, TypeError):
                        logger.warning("Invalid birthdate format from LuxID")

                # Map gender — same overwrite policy as birthdate above.
                # "Je préfère ne pas le dire" sends no gender claim; the
                # empty-string guard below leaves the profile value intact.
                gender = (_claims.get("gender") or "").lower()
                if gender:
                    mapped = LUXID_GENDER_MAP.get(gender, "")
                    if mapped and (_prov == "luxid" or not profile.gender):
                        profile.gender = mapped
                        updated_fields.append("gender")

                # Map phone_number. LuxID only releases verified numbers
                # (POST Luxembourg confirmed `phone_number_verified` is
                # always implicit), so trust it as the verification anchor
                # and skip Crush.lu's Firebase OTP step for LuxID users.
                #
                # We always overwrite with the LuxID number when it differs
                # from what's stored — LuxID is a stronger trust anchor than
                # whatever the user typed manually or verified via Firebase,
                # and this is what powers the "Verify with LuxID" fallback
                # on /create-profile/. Non-Luxembourgish numbers are skipped
                # for now (see _is_trusted_luxid_phone).
                phone = _claims.get("phone_number")
                if phone and not _is_trusted_luxid_phone(phone):
                    logger.info(
                        "LuxID returned unsupported country phone for user_pk=%s; "
                        "skipping phone prefill.",
                        getattr(sociallogin.user, "pk", None),
                    )
                elif (
                    phone
                    # Same number but not yet verified also qualifies: the
                    # user typed their number manually, abandoned the OTP,
                    # then clicked "Verify with LuxID" — LuxID confirming
                    # the same number IS the verification.
                    and (phone != profile.phone_number or not profile.phone_verified)
                    and _luxid_phone_available(phone, profile)
                ):
                    old_phone = profile.phone_number
                    profile.phone_number = phone
                    profile.phone_verified = True
                    profile.phone_verified_at = timezone.now()
                    updated_fields.extend(
                        ["phone_number", "phone_verified", "phone_verified_at"]
                    )
                    if old_phone and old_phone != phone and request is not None:
                        # User-facing notice: they came in via the "Verify
                        # with LuxID" fallback and their stored number was
                        # replaced with the one LuxID has on file.
                        try:
                            from django.contrib import messages as _messages
                            from django.utils.translation import gettext as _

                            _messages.info(
                                request,
                                _(
                                    "Your phone number was updated to the one "
                                    "on file with LuxID (%(new)s)."
                                )
                                % {"new": phone},
                            )
                        except Exception:
                            pass

                # Map locale to preferred_language.
                #
                # Both halves are load-bearing. == "en" keeps this from
                # clobbering a language already inferred at signup from
                # request.LANGUAGE_CODE, which is what it has always guarded.
                # The flag adds the case that value cannot express: a member
                # who ANSWERED English. Overwriting them would not just lose
                # the preference, it would defeat the flag downstream too,
                # since get_onscreen_language reads a stored de/fr as
                # self-evidently chosen and never consults the flag again.
                # Read under a row lock, held until _luxid_save_profile below
                # has written. The guard is only as good as the moment it is
                # evaluated: read off this instance, it can say "nobody has
                # answered" while a language switch commits en/True a moment
                # later, and the save — which names preferred_language in
                # update_fields — then leaves the row on fr/True, the explicit
                # English gone despite the guard having passed honestly. The
                # lock blocks that switch's UPDATE until this commits, so the
                # check and the write see one state.
                locale = _claims.get("locale", "")
                with transaction.atomic():
                    locked = (
                        CrushProfile.objects.select_for_update()
                        .filter(pk=profile.pk)
                        .values("preferred_language", "language_explicitly_set")
                        .first()
                    )
                    if (
                        locale
                        and locked
                        and locked["preferred_language"] == "en"
                        and not locked["language_explicitly_set"]
                    ):
                        lang_code = locale[:2].lower()
                        if lang_code in ("de", "fr"):
                            profile.preferred_language = lang_code
                            updated_fields.append("preferred_language")

                    if updated_fields:
                        _luxid_save_profile(profile, updated_fields)
                        logger.info(
                            f"Updated CrushProfile for LuxID user "
                            f"{sociallogin.user.email}: {updated_fields}"
                        )

            except CrushProfile.DoesNotExist:
                logger.info(
                    f"No CrushProfile exists yet for LuxID user {sociallogin.user.email}"
                )

    except Exception as e:
        logger.error(
            f"Error in LuxID pre_social_login handler: {str(e)}", exc_info=True
        )


@receiver(post_save, sender=SocialAccount)
def create_crush_profile_from_luxid(sender, instance, created, **kwargs):
    """
    Create CrushProfile when a new LuxID SocialAccount is created.
    Populates profile fields from OIDC claims (birthdate, gender, phone, locale).
    """
    if instance.provider not in ("luxid", "openid_connect"):
        return

    if not created:
        return

    if not getattr(_thread_local, "is_crush_luxid_login", False):
        return

    logger.info(
        f"SocialAccount post_save signal for LuxID on crush.lu (user: {instance.user.email})"
    )

    try:
        profile, profile_created = CrushProfile.objects.get_or_create(
            user=instance.user
        )

        if profile_created:
            claims = _luxid_claims(instance.extra_data)
            updated_fields = []

            # Map birthdate
            birthdate = claims.get("birthdate")
            if birthdate:
                try:
                    profile.date_of_birth = datetime.strptime(
                        birthdate, "%Y-%m-%d"
                    ).date()
                    updated_fields.append("date_of_birth")
                except (ValueError, TypeError):
                    pass

            # Map gender
            gender = (claims.get("gender") or "").lower()
            mapped = LUXID_GENDER_MAP.get(gender, "")
            if mapped:
                profile.gender = mapped
                updated_fields.append("gender")

            # Map phone_number. LuxID only releases verified numbers, so
            # trust it as the verification anchor and skip Crush.lu's
            # Firebase OTP step for LuxID users. Non-Luxembourgish numbers
            # are skipped for the initial rollout (see _is_trusted_luxid_phone).
            phone = claims.get("phone_number")
            if (
                phone
                and _is_trusted_luxid_phone(phone)
                and _luxid_phone_available(phone, profile)
            ):
                profile.phone_number = phone
                profile.phone_verified = True
                profile.phone_verified_at = timezone.now()
                updated_fields.extend(
                    ["phone_number", "phone_verified", "phone_verified_at"]
                )

            # Map locale to preferred_language
            locale = claims.get("locale", "")
            if locale:
                lang_code = locale[:2].lower()
                if lang_code in ("de", "fr"):
                    profile.preferred_language = lang_code
                    updated_fields.append("preferred_language")

            if updated_fields:
                profile.save(update_fields=updated_fields)

            logger.info(
                f"Created CrushProfile for LuxID user {instance.user.email} "
                f"with fields: {updated_fields}"
            )

        else:
            # Existing profile (email user connecting LuxID): the
            # pre_social_login handler is the primary path, but it can
            # miss this case when sociallogin.user.id is None at signal
            # time. Mirror the same field-population logic here as a
            # safety net so birthdate, gender, and phone are always
            # populated regardless of allauth's internal association order.
            claims = _luxid_claims(instance.extra_data)
            updated_fields = []

            birthdate = claims.get("birthdate")
            if birthdate and not profile.date_of_birth:
                try:
                    profile.date_of_birth = datetime.strptime(
                        birthdate, "%Y-%m-%d"
                    ).date()
                    updated_fields.append("date_of_birth")
                except (ValueError, TypeError):
                    pass

            gender = (claims.get("gender") or "").lower()
            if gender and not profile.gender:
                mapped = LUXID_GENDER_MAP.get(gender, "")
                if mapped:
                    profile.gender = mapped
                    updated_fields.append("gender")

            phone = claims.get("phone_number")
            if (
                phone
                and _is_trusted_luxid_phone(phone)
                # Same number but not yet verified also qualifies — see the
                # matching condition in update_crush_profile_from_luxid.
                and (phone != profile.phone_number or not profile.phone_verified)
                and _luxid_phone_available(phone, profile)
            ):
                profile.phone_number = phone
                profile.phone_verified = True
                profile.phone_verified_at = timezone.now()
                updated_fields.extend(
                    ["phone_number", "phone_verified", "phone_verified_at"]
                )

            if updated_fields:
                _luxid_save_profile(profile, updated_fields)
                logger.info(
                    "Updated existing CrushProfile via LuxID connect for user %s: %s",
                    instance.user.email,
                    updated_fields,
                )

        # Send welcome email for new OAuth signups
        if profile_created:
            oauth_request = getattr(_thread_local, "oauth_signup_request", None)
            if oauth_request:
                try:
                    from .email_helpers import send_welcome_email

                    result = send_welcome_email(instance.user, oauth_request)
                    logger.info(
                        f"Welcome email sent to LuxID OAuth user {instance.user.email}: {result}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send welcome email to LuxID OAuth user "
                        f"{instance.user.email}: {e}",
                        exc_info=True,
                    )
                finally:
                    _thread_local.oauth_signup_request = None

    except Exception as e:
        logger.error(
            f"Error in LuxID SocialAccount post_save handler: {str(e)}",
            exc_info=True,
        )


# =============================================================================
# EMAIL VERIFICATION — SOCIAL ACCOUNT CONNECT
# =============================================================================


@receiver(social_account_added)
def verify_email_on_social_account_connect(sender, request, sociallogin, **kwargs):
    """
    Mark a user's EmailAddress as verified when they connect a social account
    that authenticates the same email.

    When a user signs up via email/password under ACCOUNT_EMAIL_VERIFICATION="optional"
    their EmailAddress is created with verified=False. If they later connect a social
    account (Google, Facebook, etc.) that authenticates the same email, allauth creates
    the SocialAccount but does NOT retroactively set EmailAddress.verified=True.
    This handler closes that gap so the user is not blocked if verification becomes
    mandatory, and so SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT works correctly
    on future logins.
    """
    from allauth.account.models import EmailAddress

    user = sociallogin.user
    if not (user and getattr(user, "pk", None)):
        return

    # Only collect emails that allauth itself considers provider-verified.
    # cleanup_email_addresses() sets addr.verified=True based on the provider's
    # VERIFIED_EMAIL setting or the email_verified claim in the OAuth response.
    # Skipping unverified entries prevents a generic OIDC provider (without
    # email_verified) from promoting an unconfirmed address to verified status.
    social_emails = {
        addr.email.lower()
        for addr in sociallogin.email_addresses
        if addr.email and addr.verified
    }

    if not social_emails:
        return

    updated = EmailAddress.objects.filter(
        user=user,
        verified=False,
        email__in=social_emails,
    ).update(verified=True)

    if updated:
        logger.info(
            "Verified %d EmailAddress record(s) for user %s after connecting %s",
            updated,
            user.pk,
            sociallogin.account.provider,
        )


def _execute_luxid_direct_verify(user, profile, submission, request):
    """
    Verify a profile directly via LuxId without a ProfileSubmission.

    Used for the free verification path (no coach involved).
    If a submission is passed (e.g. revision path), it is also approved.
    """
    now = timezone.now()
    with transaction.atomic():
        # Keep this local: signals are registered during AppConfig.ready(), so
        # importing the service only when the handler runs avoids an app-load
        # dependency cycle through the models package.
        from .services.profile_verification import claim_profile_verification

        # Conditional claim, matching the coach/check-in verification paths
        # through their shared service. An unconditional save here
        # would let a LuxID callback that read `pending` overwrite a method a
        # door scan had already committed — and both would then emit the
        # approval notification and referral work. Whoever claims the
        # transition owns it; the loser leaves the record alone.
        claimed = claim_profile_verification(
            profile,
            method="luxid",
            approved_at=now,
            claim_from=("incomplete", "pending"),
        )
        if not claimed:
            logger.info(
                "[LUXID-VERIFY] Profile pk=%s was already verified by another "
                "path; leaving it untouched",
                profile.pk,
            )
            return False
        if submission and submission.status == "pending":
            submission.status = "approved"
            submission.reviewed_at = now
            submission.review_call_completed = True
            submission.coach_notes = (
                (submission.coach_notes + "\n" if submission.coach_notes else "")
                + "Auto-approved via LuxID identity verification"
            ).strip()
            submission.save(
                update_fields=[
                    "status",
                    "reviewed_at",
                    "coach_notes",
                    "review_call_completed",
                ]
            )

    logger.info(
        "[LUXID-VERIFY] Verified profile pk=%s for user pk=%s (submission=%s)",
        profile.pk,
        user.pk,
        submission.pk if submission else "none",
    )

    if request is not None and hasattr(request, "session"):
        request.session["luxid_just_auto_approved"] = True

    try:
        from .referrals import check_and_apply_profile_approved_reward

        check_and_apply_profile_approved_reward(profile)
    except Exception as _e:
        logger.error(
            "[LUXID-VERIFY] Reward step failed for profile pk=%s: %s", profile.pk, _e
        )

    try:
        from .notification_service import notify_profile_approved

        notify_profile_approved(
            user=user, profile=profile, coach_notes=None, request=request
        )
    except Exception as _e:
        logger.error(
            "[LUXID-VERIFY] Notification failed for profile pk=%s: %s", profile.pk, _e
        )

    if request is not None:
        try:
            from django.utils.translation import gettext

            messages.success(
                request,
                gettext(
                    "Your identity has been verified via LuxID. Your profile is now active!"
                ),
            )
        except Exception:
            pass


def _execute_luxid_auto_approval(user, profile, submission, request):
    """Kept for backward compatibility — delegates to _execute_luxid_direct_verify."""
    _execute_luxid_direct_verify(user, profile, submission, request)


@receiver(social_account_added)
def auto_approve_profile_on_luxid_connect(sender, request, sociallogin, **kwargs):
    """
    Verify a CrushProfile when an existing user connects their LuxID account.

    No ProfileSubmission is required — verification is now direct for the free
    path. A pending submission is used opportunistically if one exists (e.g. a
    paid coach revision re-submit).

    Guards: LuxID provider, crush.lu domain, authenticated user, has CrushProfile,
    and the profile must be awaiting verification (verification_status="pending").
    Incomplete profiles must submit first; rejected profiles cannot self-clear.
    """
    if sociallogin.account.provider not in ("luxid", "openid_connect"):
        return

    if not getattr(_thread_local, "is_crush_luxid_login", False):
        return

    if not _is_crush_domain(request):
        return

    user = sociallogin.user
    if not (user and getattr(user, "pk", None)):
        return

    try:
        profile = CrushProfile.objects.get(user=user)
    except CrushProfile.DoesNotExist:
        logger.info(
            "[LUXID-VERIFY] No CrushProfile for user pk=%s, skipping",
            getattr(user, "pk", None),
        )
        return

    # Already-verified profiles keep their status AND verification_method (it
    # records the FIRST verification path) — but linking LuxID still matters:
    # it's the ticket into the Crush Connect candidate catalogue, so confirm
    # that to the user instead of silently skipping.
    if profile.verification_status == "verified":
        logger.info(
            "[LUXID-CONNECT] Already-verified profile pk=%s connected LuxID — "
            "now eligible for the Crush Connect catalogue (no status change)",
            profile.pk,
        )
        if request is not None:
            messages.success(
                request,
                _("Your LuxID is now connected — you can join " "Crush Connect."),
            )
        return

    # Only profiles that have been submitted and are awaiting verification
    # ("pending") may be verified by connecting LuxID. Profiles that never
    # completed/submitted ("incomplete") must go through submission first, and
    # "rejected" profiles must not be able to self-clear by connecting LuxID.
    if profile.verification_status != "pending":
        logger.info(
            "[LUXID-VERIFY] Profile pk=%s not pending verification (status=%s), skipping",
            profile.pk,
            profile.verification_status,
        )
        return

    # Use any pending submission opportunistically (paid coach / revision path).
    # For the standard free path there will be none — that is fine. Gated on
    # the expired-latest invariant: when the newest row was closed out by the
    # pivot cleanup (latest_for_profile returns None), the member is the
    # standard free path — never resurrect an older pending row.
    submission = None
    if ProfileSubmission.latest_for_profile(profile) is not None:
        submission = (
            ProfileSubmission.objects.filter(profile=profile, status="pending")
            .order_by("-submitted_at")
            .first()
        )

    _execute_luxid_direct_verify(user, profile, submission, request)


# =============================================================================
# WALLET PASS UPDATE SIGNAL HANDLERS
# =============================================================================


def _delete_passkit_registrations(*serials):
    """Drop PassKit device rows for serials whose owner is going away.

    PasskitDeviceRegistration keys off the pass SERIAL, with no FK to the
    profile or registration, so nothing cascades. Left behind, those rows keep
    the device's APNs push token alive past the account-deletion flow that
    promises to remove Crush.lu data, and they keep answering the device poll
    with a serial nothing can resolve — which the web service then answers 500
    (no expected token) on every fetch and unregister.
    """
    serials = [s for s in serials if s]
    if not serials:
        return 0
    from .models import PasskitDeviceRegistration

    deleted, _ = PasskitDeviceRegistration.objects.filter(
        serial_number__in=serials
    ).delete()
    return deleted


@receiver(post_delete, sender=CrushProfile)
def cleanup_passkit_registrations_on_profile_delete(sender, instance, **kwargs):
    _delete_passkit_registrations(instance.apple_pass_serial)


@receiver(post_delete, sender=EventRegistration)
def cleanup_passkit_registrations_on_registration_delete(sender, instance, **kwargs):
    _delete_passkit_registrations(instance.apple_wallet_ticket_serial)


@receiver(post_delete, sender=EventRegistration)
def refresh_member_pass_on_registration_delete(sender, instance, origin=None, **kwargs):
    """The member card prints the holder's NEXT event, so deleting the row that
    supplied it leaves the card advertising an event they are not going to.

    The post_save receiver covers create and status changes; deletion had no
    equivalent, and used to be picked up by the holder's next bare
    profile.save().

    A direct delete of one registration — an admin object delete — pushes.
    Anything else reaches this per row (an event cascade, a user cascade, a
    bulk queryset), and one push per row is not bounded in aggregate: each
    carries its own budget, so N rows multiply the deadline N times. That is
    the pathology cancel_events was already fixed for by batching into a single
    refresh. Those rows get the marker advance instead — one query, no network,
    and the card still rebuilds on Wallet's next poll rather than advertising
    an event that no longer exists.
    """
    context = f"Registration {instance.pk} deleted"
    profile = CrushProfile.objects.filter(user_id=instance.user_id).first()

    if isinstance(origin, EventRegistration):
        _refresh_member_pass(profile, context)
        return

    _mark_member_pass_stale(profile, f"{context} ({type(origin).__name__})")


# `username` counts: CrushProfile.display_name falls back to it in BOTH
# branches (get_full_name() or username, and first_name or username), so a
# user with no first name has their username printed on the pass and on every
# ticket. Staff can edit it through the Crush user admin.
_USER_NAME_FIELDS = {"first_name", "last_name", "username"}


@receiver(pre_save, sender=User)
def remember_previous_user_name(sender, instance, update_fields=None, **kwargs):
    """Snapshot the stored name so post_save can tell a rename from any save.

    Only queries when a rename is actually possible: login writes
    ``update_fields=["last_login"]`` on every sign-in and must not pay for this.
    """
    instance._previous_name = None
    if not instance.pk:
        return
    if update_fields is not None and not (set(update_fields) & _USER_NAME_FIELDS):
        return
    instance._previous_name = (
        User.objects.filter(pk=instance.pk)
        .values_list("first_name", "last_name", "username")
        .first()
    )


def _normalized_member_pass_values(values):
    """Flatten a member-pass field mapping so a row and an instance compare equal.

    Only ``photo_1`` needs it: ``.values()`` yields the stored file name while
    the instance holds a FieldFile, and an empty photo is NULL on some rows and
    "" on others (the column is both null and blank), which would otherwise read
    as a change on every save.
    """
    if values is None:
        return None
    normalized = dict(values)
    photo = normalized.get("photo_1")
    normalized["photo_1"] = getattr(photo, "name", photo) or ""
    return normalized


@receiver(pre_save, sender=CrushProfile)
def remember_previous_member_pass_fields(
    sender, instance, update_fields=None, **kwargs
):
    """Snapshot the persisted fields both wallet passes are rendered from.

    Mirrors remember_previous_user_name, and for the same reason: a bare
    ``profile.save()`` is not evidence of a change. The Microsoft sign-in path
    saves the profile on every login without touching any of these, and treating
    that as a change refreshed every historical ticket the user owns *and* made
    each of those logins wait on a Google Wallet round trip.

    One indexed primary-key lookup, and only when a relevant field could have
    moved at all: an onboarding step saving ``update_fields=["bio"]`` pays
    nothing.
    """
    instance._previous_member_pass_fields = None
    if not instance.pk:
        return
    if update_fields is not None and not (
        set(update_fields) & WALLET_MEMBER_PASS_FIELDS
    ):
        return
    instance._previous_member_pass_fields = _normalized_member_pass_values(
        CrushProfile.objects.filter(pk=instance.pk)
        .values(*WALLET_MEMBER_PASS_FIELDS)
        .first()
    )


@receiver(post_save, sender=User)
def refresh_wallet_passes_on_user_rename(
    sender, instance, created, update_fields, **kwargs
):
    """Refresh both wallets when the holder's actual name changes.

    The name printed on both the member pass and every event ticket comes from
    ``CrushProfile.display_name``, which reads ``User.first_name`` /
    ``get_full_name()``. So the real edit happens on ``User`` — and the
    CrushProfile receiver below never sees it. Its
    ``WALLET_UPDATE_PROFILE_FIELDS`` even lists ``first_name``/``last_name``,
    but those are *properties* on the profile, not fields, so they can never
    appear in a profile ``update_fields`` either. Renames therefore left stale
    names in Wallet indefinitely.

    Compares the persisted name rather than assuming a full save is a rename:
    ``CrushSetPasswordForm.save()`` calls ``user.save()`` after changing only
    the password, and treating that as a rename made every password change fan
    out APNs pushes. Login writes ``update_fields=["last_login"]``, which the
    pre_save above skips entirely.
    """
    if created:
        return
    previous = getattr(instance, "_previous_name", None)
    if previous is None or previous == (
        instance.first_name,
        instance.last_name,
        instance.username,
    ):
        return

    try:
        from .wallet.passkit_service import refresh_ticket_serials

        serials = list(
            EventRegistration.objects.filter(user_id=instance.pk)
            .exclude(apple_wallet_ticket_serial="")
            .values_list("apple_wallet_ticket_serial", flat=True)
        )
        profile = getattr(instance, "crushprofile", None)
        if profile is not None and profile.apple_pass_serial:
            serials.append(profile.apple_pass_serial)

        refresh_ticket_serials(serials, context=f"User {instance.pk} rename")
    except Exception as e:
        logger.error(f"Error refreshing wallet passes for user {instance.pk}: {e}")

    # Google prints display_name too (google_api._build_generic_object_payload
    # puts it in the subheader), and refresh_ticket_serials above is Apple-only
    # — it pushes PassKit serials. Nothing else will bring the Google object up
    # to date either: display_name is a property, so it can never reach a
    # profile update_fields nor the persisted snapshot the profile receiver
    # compares. This used to ride on the next bare profile.save() reaching
    # trigger_wallet_pass_updates, which is exactly the save that no longer
    # counts as a change, so without this a renamed member's Google card kept
    # the old name indefinitely.
    #
    # Its own try/except: an APNs failure above must not swallow this, and vice
    # versa. The call no-ops for a profile with no Google object.
    try:
        profile = getattr(instance, "crushprofile", None)
        if profile is not None:
            _trigger_google_wallet_object_update(profile)
    except Exception as e:
        logger.error(
            f"Error scheduling Google Wallet rename update for user {instance.pk}: {e}"
        )


def _refresh_member_pass(profile, context):
    """Bounded refresh of ONE member pass on both wallets.

    Apple goes through refresh_ticket_serials rather than the generic trigger
    for the reason AGENTS.md gives: these are request paths, so the work has to
    advance the update marker in one no-network query (guaranteed — the pass
    still refreshes on Wallet's next poll) and cap the push by count and wall
    clock. One try/except per wallet: neither may swallow the other.
    """
    if profile is None:
        return
    if not profile.apple_pass_serial and not profile.google_wallet_object_id:
        return

    try:
        if profile.apple_pass_serial:
            from .wallet.passkit_service import refresh_ticket_serials

            refresh_ticket_serials([profile.apple_pass_serial], context=context)
    except Exception as e:
        logger.error(f"Error refreshing Apple member pass ({context}): {e}")

    try:
        _trigger_google_wallet_object_update(profile)
    except Exception as e:
        logger.error(f"Error refreshing Google member pass ({context}): {e}")


def _mark_member_pass_stale(profile, context):
    """Advance the Apple update marker WITHOUT pushing.

    For bulk and cascade paths, where one refresh per row is not bounded in
    aggregate — each push carries its own budget, so N rows multiply the
    deadline N times. This is the guaranteed half of that work on its own: one
    indexed write, no network, so it cannot fan out, and the pass still
    rebuilds on Wallet's next periodic poll instead of staying wrong forever.

    Google has no marker equivalent — its card is only ever corrected by an
    explicit PATCH — so a bulk path leaves it until the next real refresh.
    """
    if profile is None or not profile.apple_pass_serial:
        return
    pass_type_id = getattr(settings, "WALLET_APPLE_PASS_TYPE_IDENTIFIER", None)
    if not pass_type_id:
        return
    try:
        from .wallet.passkit_apns import mark_passes_updated_bulk

        mark_passes_updated_bulk(pass_type_id, [profile.apple_pass_serial])
        logger.info(
            "%s: marked member pass %s for Wallet's next poll (no push — bulk "
            "path, one push per row is not bounded in aggregate)",
            context,
            profile.apple_pass_serial,
        )
    except Exception as e:
        logger.error(f"Error marking member pass stale ({context}): {e}")


# The QR on the member pass is the referrer's link, and all three of these
# change what it resolves to: the code is the URL, capture filters on
# is_active (referrals.py:52 and :86) so a deactivated code renders a QR that
# scans but attributes nothing, and `referrer` decides WHOSE link it is — staff
# reassigning it leaves the old holder's pass crediting somebody else. All
# three are editable through ReferralCodeAdmin.
_REFERRAL_CODE_PASS_FIELDS = ("code", "is_active", "referrer_id")

# What `update_fields` may legitimately name for those columns. A FK can arrive
# as either the field name or its attname, and save(update_fields=["referrer"])
# is what admin and ordinary code actually write — gating on "referrer_id"
# alone skipped the snapshot and made reassignment a silent no-op.
_REFERRAL_CODE_UPDATE_FIELD_NAMES = frozenset(
    {"code", "is_active", "referrer", "referrer_id"}
)


@receiver(pre_save, sender=ReferralCode)
def remember_previous_referral_code(sender, instance, update_fields=None, **kwargs):
    """Snapshot the code fields the member pass barcode is built from."""
    instance._previous_referral_code = None
    if not instance.pk:
        return
    if update_fields is not None and not (
        set(update_fields) & _REFERRAL_CODE_UPDATE_FIELD_NAMES
    ):
        return
    instance._previous_referral_code = (
        ReferralCode.objects.filter(pk=instance.pk)
        .values_list(*_REFERRAL_CODE_PASS_FIELDS)
        .first()
    )


@receiver(post_save, sender=ReferralCode)
def refresh_member_pass_on_referral_code_change(sender, instance, created, **kwargs):
    """Rebuild the member pass when its QR stops meaning what it did.

    build_wallet_pass_barcode_value() embeds this code, and nothing else
    notices when it moves: the code lives on another model, so it reaches
    neither WALLET_MEMBER_PASS_FIELDS nor any profile save. Deactivating a code
    is the case that actually bites — get_or_create_for_profile only returns
    active ones, so the next build mints a *different* code while every
    installed pass keeps scanning to the dead one, silently attributing
    nothing.

    Deliberately does not fire on `created`: the only thing that creates a code
    is get_or_create_for_profile, which is called *from the pass build itself*,
    so refreshing here would push a notification at the device that just
    downloaded the pass. A code is only ever created when no active one
    remains, and the deactivation that got it there already refreshed.
    """
    if created:
        return
    previous = getattr(instance, "_previous_referral_code", None)
    if previous is None:
        return
    previous_code, previous_active, previous_referrer_id = previous
    if previous == (instance.code, instance.is_active, instance.referrer_id):
        return

    context = f"Referral code {instance.pk} change"
    _refresh_member_pass(
        CrushProfile.objects.filter(pk=instance.referrer_id).first(), context
    )

    # A reassignment has two victims, and the one that actually matters is the
    # OLD holder: their installed QR now credits somebody else entirely.
    if previous_referrer_id != instance.referrer_id:
        _refresh_member_pass(
            CrushProfile.objects.filter(pk=previous_referrer_id).first(),
            f"{context} (previous holder)",
        )


@receiver(post_delete, sender=ReferralCode)
def refresh_member_pass_on_referral_code_delete(
    sender, instance, origin=None, **kwargs
):
    """Deleting the code strands every pass that embeds it.

    capture_referral() needs a matching active row, so once the row is gone the
    QR scans and attributes nothing — and the next build mints a different
    code, so the installed pass never converges on its own.

    A direct delete pushes. The admin's default "Delete selected" action calls
    QuerySet.delete(), so `origin` is the queryset, not each row — pushing per
    row there is not bounded in aggregate, so those rows get the marker advance
    only and rebuild on Wallet's next poll.

    A cascade from anything else is a profile going away underneath its own
    codes. Django deletes children before parents, so the profile row is still
    readable here — refreshing a pass that is about to stop existing is the
    trap, hence the explicit model check rather than "not a queryset".
    """
    context = f"Referral code {instance.pk} deleted"
    profile = CrushProfile.objects.filter(pk=instance.referrer_id).first()

    if isinstance(origin, ReferralCode):
        _refresh_member_pass(profile, context)
        return

    if getattr(origin, "model", None) is ReferralCode:
        _mark_member_pass_stale(profile, f"{context} (bulk)")


def _refresh_user_event_tickets(profile):
    """Refresh this user's installed Apple event tickets.

    The member pass and the event tickets carry different serials, so
    trigger_wallet_pass_updates — which only ever targets
    profile.apple_pass_serial — leaves the tickets showing the old name.
    """
    try:
        from .wallet.passkit_service import refresh_ticket_serials

        serials = list(
            EventRegistration.objects.filter(user_id=profile.user_id)
            .exclude(apple_wallet_ticket_serial="")
            .values_list("apple_wallet_ticket_serial", flat=True)
        )
        refresh_ticket_serials(serials, context=f"Profile {profile.pk} identity change")
    except Exception as e:
        logger.error(f"Error refreshing event tickets for user {profile.user_id}: {e}")


@receiver(post_save, sender=CrushProfile)
def trigger_wallet_pass_update_on_profile_change(
    sender, instance, created, update_fields, **kwargs
):
    """
    Trigger wallet pass updates when relevant profile fields change.

    This signal fires on CrushProfile save and compares the fields the passes
    are rendered from against the pre_save snapshot. If one actually moved, it
    triggers push notifications to Apple Wallet devices and updates Google
    Wallet objects.

    Wallet-relevant fields (WALLET_MEMBER_PASS_FIELDS) are:
    - referral_points, membership_tier (rewards/status)
    - show_photo_on_wallet, photo_1 (appearance)
    - show_full_name (identity; the User half is the rename receiver above)
    """
    # What actually MOVED, from the pre_save snapshot — never "update_fields
    # says so", and never "a full save must mean something changed". A bare
    # profile.save() carries update_fields=None, and the Microsoft sign-in path
    # does exactly that on every login without editing a single field; reading
    # that as a change put an APNs fan-out and a synchronous Google Wallet round
    # trip (two HTTP calls, 30s timeouts) on the critical path of every login by
    # a member who has installed a pass.
    #
    # An empty snapshot (no relevant field in update_fields, or the row is gone)
    # reads as "nothing moved" — same fail-closed shape as the User receiver —
    # and short-circuits before touching the instance, so the saves that skipped
    # the snapshot query pay nothing here either.
    previous = getattr(instance, "_previous_member_pass_fields", None)
    changed = set()
    if previous is not None:
        current = _normalized_member_pass_values(
            {name: getattr(instance, name) for name in WALLET_MEMBER_PASS_FIELDS}
        )
        changed = {name for name, value in current.items() if previous[name] != value}

    # Tickets are gated on a strictly narrower set than the member pass: the
    # only profile input to a ticket is the attendee name, so a tier bump or a
    # photo upload must not refresh every historical ticket the user owns and
    # burn the APNs budget downloading byte-identical packages. The User half of
    # the name is handled by the rename receiver above.
    #
    # `created` counts as a change in its own right: an open-event attendee can
    # install a ticket with no profile at all (the name falls back to the bare
    # username), and creating the profile switches display_name to
    # username.split("@")[0]. There is no previous value to compare on that
    # path, so the persisted-value guard alone would suppress it.
    ticket_identity_changed = created or bool(changed & WALLET_TICKET_IDENTITY_FIELDS)

    # Event tickets print the holder's name and carry their own evt-* serials,
    # so they must be refreshed BEFORE the two guards below — both of which are
    # about the MEMBER pass and neither of which holds for a ticket holder:
    #   * `created` — an open-event attendee can download a ticket with no
    #     profile at all (the name falls back to the username), and creating
    #     the profile afterwards is exactly when that name changes;
    #   * the serial guard — such an attendee has no member pass or Google
    #     object, so every later rename returned here and their installed
    #     ticket kept the old name forever.
    if ticket_identity_changed:
        _refresh_user_event_tickets(instance)

    # Skip if this is a new profile (no pass exists yet)
    if created:
        return

    # Skip if profile has no wallet passes registered
    if not instance.apple_pass_serial and not instance.google_wallet_object_id:
        return

    if not changed:
        return

    logger.debug(
        f"Wallet-relevant fields changed for user {instance.user_id}: {changed}"
    )

    try:
        trigger_wallet_pass_updates(instance)
    except Exception as e:
        # Don't fail the save if wallet update fails
        logger.error(f"Error triggering wallet update for user {instance.user_id}: {e}")


@receiver(pre_save, sender=EventRegistration)
def remember_previous_registration_status(sender, instance, **kwargs):
    """Stash the stored status so post_save receivers can detect a *transition*.

    Without this a receiver keyed on ``status == "cancelled"`` fires again every
    later save of an already-cancelled row (an admin editing a note, a wallet
    field being written), which for waitlist promotion means handing out a seat
    per save rather than per cancellation.

    One indexed primary-key lookup; deliberately fetches only the column.
    """
    if not instance.pk:
        instance._previous_status = None
        return
    instance._previous_status = (
        EventRegistration.objects.filter(pk=instance.pk)
        .values_list("status", flat=True)
        .first()
    )


@receiver(post_save, sender=EventRegistration)
def promote_waitlist_on_cancellation(sender, instance, created, **kwargs):
    """Give the freed seat to the waitlist when a registration is cancelled.

    ``views_events`` promotes explicitly when a *member* cancels through the
    site, but that was the only path that did. A status flipped to ``cancelled``
    anywhere else — Django admin, a shell, a coach acting on someone's behalf —
    freed a seat that nobody was ever offered. The 2026-07-29 mixer finished
    with 23 cancellations and 3 people still waiting.

    Promotes **one** candidate, because one cancellation frees exactly one seat.
    (``promote_waitlist_on_capacity_increase`` loops instead, since a capacity
    change frees an unknown number.) ``_promote_from_waitlist`` applies the
    gender-pool and premium capacity rules, so this never over-fills a pool.

    Runs on commit: the promotion must count seats against a *durable*
    cancellation, and the confirmation email must not go out if the transaction
    later rolls back.

    Note: ``QuerySet.update()`` bypasses signals entirely, so a bulk status
    change (e.g. marking no-shows after an event) deliberately does not promote.
    """
    if created or instance.status != "cancelled":
        return
    # Already cancelled before this save — something else on the row changed.
    if getattr(instance, "_previous_status", None) == "cancelled":
        return
    # The member cancellation view promotes inside its own locked transaction
    # and sends the email itself; without this flag that path promotes twice.
    if getattr(instance, "_waitlist_promotion_handled", False):
        return

    event = instance.event
    cancelled_user = instance.user
    event_pk = event.pk

    def _promote_after_commit():
        from .email_helpers import (
            send_event_payment_pending_notification,
            send_event_registration_confirmation,
        )
        from .services.credits import (
            credit_registration_for_cancelled_event,
            issue_cancellation_credits,
            paid_amount_cents,
        )
        from .views_payments import _send_member_cancellation_safely
        from .views_events import _promote_from_waitlist

        member_notification = None
        try:
            with transaction.atomic():
                locked_event = MeetupEvent.objects.select_for_update().get(pk=event_pk)
                lock_ids = [instance.pk] + list(
                    EventRegistration.objects.filter(
                        event=locked_event, status="waitlist"
                    )
                    .exclude(pk=instance.pk)
                    .values_list("pk", flat=True)
                )
                locked_registrations = {
                    row.pk: row
                    for row in EventRegistration.objects.select_for_update()
                    .select_related("event", "user")
                    .filter(pk__in=lock_ids)
                    .order_by("pk")
                }
                cancelled = locked_registrations.get(instance.pk)
                if cancelled is None:
                    return

                # A cancellation driven from the ADMIN, a coach or the shell
                # releases the seat exactly as the member-facing view does, and
                # used to compensate nobody: this signal only ever reached the
                # resale helper, which returns immediately for a cancellation
                # made more than 48h out. So a coach cancelling a paid seat a
                # week ahead — a supported, ordinary action — left the member
                # with no credit and `payment_confirmed` still set, which then
                # reads as "we still owe them a seat" everywhere else.
                #
                # issue_cancellation_credit owns the timing rule itself, so
                # this is not a second policy: inside 48h it declines and
                # leaves the resale clause below to do its work.
                #
                # `event_cancel` sets `_waitlist_promotion_handled`, so the
                # member path never reaches here and cannot be paid twice.
                if locked_event.is_cancelled:
                    credits = credit_registration_for_cancelled_event(cancelled)
                    if credits:
                        from .views_payments import (
                            _send_organiser_cancellation_safely,
                        )

                        transaction.on_commit(
                            lambda r=cancelled, c=credits: (
                                _send_organiser_cancellation_safely(r, c)
                            )
                        )
                else:
                    credits = issue_cancellation_credits(cancelled)
                    _paid_cents, captured_payment = paid_amount_cents(cancelled)
                    member_notification = (
                        cancelled,
                        credits,
                        (
                            not credits
                            and cancelled.payment_confirmed
                            and captured_payment is not None
                        ),
                    )

                # Compensation is independent of whether this event can
                # promote a waitlist. Promotion is optional; paying an early
                # canceller is not. A late cancellation records the candidate
                # resale on the promoted row, and the share is issued only
                # after that replacement's payment completes.
                promoted = None
                if (
                    not locked_event.is_cancelled
                    and locked_event.accepts_waitlist_promotion
                ):
                    promoted = _promote_from_waitlist(
                        locked_event,
                        cancelled_user,
                        resale_source_registration=cancelled,
                    )
        except Exception:
            logger.exception(
                "Waitlist promotion failed after cancellation of registration %s",
                instance.pk,
            )
            return

        if member_notification:
            cancelled, credits, awaiting_resale = member_notification
            _send_member_cancellation_safely(
                cancelled,
                credits,
                awaiting_resale=awaiting_resale,
            )

        if not promoted:
            return
        try:
            # Paid events admit as "pending" (Pending Payment), so the promoted
            # seat needs the payment ask, not a confirmation it hasn't earned.
            if promoted.status == "pending":
                send_event_payment_pending_notification(promoted)
            else:
                send_event_registration_confirmation(promoted)
        except Exception as e:
            logger.error(
                "Failed to send waitlist promotion email for user %s, event %s: %s",
                promoted.user_id,
                event_pk,
                type(e).__name__,
            )

    transaction.on_commit(_promote_after_commit)


@receiver(post_save, sender=EventRegistration)
def trigger_wallet_pass_update_on_registration_change(
    sender, instance, created, **kwargs
):
    """
    Trigger wallet pass updates when event registrations change.

    The wallet pass displays "Next Event" information, so when a user:
    - Registers for a new event
    - Changes registration status (confirmed, waitlist, cancelled)
    - Registration is deleted

    We need to update the pass to reflect the new "next event" info.
    """
    # Get the user's profile
    try:
        profile = CrushProfile.objects.get(user=instance.user)
    except CrushProfile.DoesNotExist:
        return

    # Skip if profile has no wallet passes registered
    if not profile.apple_pass_serial and not profile.google_wallet_object_id:
        return

    # Only trigger for status changes that affect "next event" display
    # - New confirmed/waitlist registration (shows new event)
    # - Status change to/from confirmed/waitlist (changes next event)
    # A pending (unpaid) seat is still the member's next event.
    relevant_statuses = {*SEAT_HOLDING_STATUSES, "waitlist"}

    if created and instance.status in relevant_statuses:
        # New registration for upcoming event
        logger.debug(
            f"New event registration for user {instance.user_id}, triggering wallet update"
        )
        try:
            trigger_wallet_pass_updates(profile)
        except Exception as e:
            logger.error(
                f"Error triggering wallet update on registration for user {instance.user_id}: {e}"
            )
    elif not created:
        # Registration was updated (status change, etc.)
        # We can't easily check what changed without pre_save tracking,
        # so trigger update for any modification to relevant statuses
        logger.debug(
            f"Event registration updated for user {instance.user_id}, triggering wallet update"
        )
        try:
            trigger_wallet_pass_updates(profile)
        except Exception as e:
            logger.error(
                f"Error triggering wallet update on registration change for user {instance.user_id}: {e}"
            )


@receiver(post_save, sender=EventRegistration)
def handle_event_ticket_on_registration_change(sender, instance, created, **kwargs):
    """
    Update Google Wallet event ticket when registration status changes.

    - cancelled -> expire the ticket
    - attended -> complete the ticket
    - confirmed *and* ``_reactivate_ticket`` set -> activate the ticket
      (an attendance was undone; see the branch for why the flag is needed)
    """
    if not instance.google_wallet_ticket_object_id:
        return

    if not getattr(settings, "WALLET_GOOGLE_EVENT_TICKET_ENABLED", True):
        return

    if created:
        return

    try:
        from .wallet.google_event_ticket_api import (
            activate_event_ticket,
            expire_event_ticket,
            complete_event_ticket,
        )

        _PATCH_FOR_STATUS = {
            "cancelled": (expire_event_ticket, "Expired"),
            "attended": (complete_event_ticket, "Completed"),
            "confirmed": (activate_event_ticket, "Reactivated"),
            # Undo can restore an unpaid seat to "pending", which is just as
            # valid a door pass. Without this entry the branch below schedules
            # the callback and the callback then finds no patch and returns,
            # leaving the wallet ticket stuck on Completed.
            "pending": (activate_event_ticket, "Reactivated"),
        }

        def _after_commit():
            """Reconcile the pass with the row once that row is durable.

            Deferred because these callers save inside a transaction —
            event_checkin_api and coach_undo_checkin both hold a
            select_for_update on the registration — and the PATCH is a
            synchronous Google call that allows 30 seconds. Inline, it
            stretched an open transaction across a network round trip and let
            a rollback leave the pass and the row disagreeing: a rolled-back
            undo would keep the row attended while its used pass went live
            again, i.e. reusable at the door.

            The target state is re-derived from a *fresh* read rather than
            from the status this save happened to see. Committing releases the
            row lock, so two overlapping door actions can have their PATCHes
            in flight at once, and the one that lands last decides what Google
            stores. Reading here means the last writer sends whatever the
            database says now, instead of a decision taken up to 30 seconds
            earlier. That narrows the window to this read-to-request gap; it
            does not close it. Closing it needs the wallet updates serialized
            out of band, and production has no task worker.

            on_commit runs immediately when there is no transaction, so every
            other caller is unaffected.
            """
            try:
                fresh = EventRegistration.objects.filter(pk=instance.pk).first()
                if fresh is None or not fresh.google_wallet_ticket_object_id:
                    return
                patch, verb = _PATCH_FOR_STATUS.get(fresh.status, (None, None))
                if patch is None:
                    return
                result = patch(fresh)
                if result["success"]:
                    logger.info(f"{verb} event ticket for registration {fresh.id}")
            except Exception as e:
                logger.error(
                    f"Error updating event ticket for registration {instance.id}: {e}"
                )

        # Whether to touch the pass at all is decided here, from this save.
        # `confirmed` stays gated on the flag so unrelated saves on a confirmed
        # row do not schedule anything — see below. *What* to send is decided
        # in _after_commit, from committed state.
        if instance.status in ("cancelled", "attended"):
            transaction.on_commit(_after_commit)
        elif instance.status in ("confirmed", "pending") and getattr(
            instance, "_reactivate_ticket", False
        ):
            # Undoing a mis-scan sends the row back to confirmed -- or to
            # "pending" for an unpaid seat, which is equally a valid door pass.
            # Without this the member keeps a wallet pass marked used while
            # still holding a valid registration, so their own ticket stops
            # working at the door.
            #
            # Explicitly flagged by the undo path rather than derived from the
            # status, because plenty of saves leave a row confirmed without any
            # transition having happened: _ensure_ticket_object_id and
            # _generate_checkin_token both write non-status fields. A status-only
            # test would PATCH on each of those — and the first one runs before
            # the JWT has created the Wallet object, so it 404s while still
            # allowing the request its 30 seconds.
            transaction.on_commit(_after_commit)
    except Exception as e:
        logger.error(f"Error updating event ticket for registration {instance.id}: {e}")


@receiver(post_save, sender=EventRegistration)
def refresh_apple_event_ticket_on_registration_change(
    sender, instance, created, update_fields=None, **kwargs
):
    """
    Push an Apple Wallet refresh when an event ticket's meaning changes.

    The Google handler above covers only Google: every repo-wide Apple refresh
    path went through ``profile.apple_pass_serial`` (the MEMBER pass), so an
    event ticket's ``apple_wallet_ticket_serial`` was never pushed. Cancelling
    a seat left the installed ticket looking live.

    Deliberately a separate receiver rather than a branch inside the Google
    one: that handler carries delicate on_commit/fresh-read handling for the
    door's ``select_for_update`` path, the two wallets have independent enable
    flags and ID fields, and an exception in one must not skip the other.

    Deliberately NOT gated identically to Google, and ``attended`` is the
    difference. Google's handler has real work to do there (it completes the
    ticket, a visible change); the Apple payload carries no status beyond
    ``voided``, which only ``cancelled`` sets — so an attendance rebuild is
    byte-identical and the push is pure waste. That waste lands on the worst
    possible path: ``event_checkin_api`` saves ``attended`` for every scan at
    the door, and on_commit runs inside the request/response cycle, so once
    PASSKIT_APNS_* is configured each scan would carry a synchronous APNs round
    trip (httpx timeout 10s) before the coach's scanner gets its answer.

    So: the transitions that actually flip ``voided`` — a seat losing validity
    or regaining it, where validity means ``status in SEAT_HOLDING_STATUSES``.
    That covers ``waitlist`` and ``no_show`` as well as ``cancelled``, matching
    what ``build_apple_event_ticket`` encodes and what the door accepts.

    The previous status comes from ``_previous_status``, set by
    ``remember_previous_registration_status`` (pre_save, above), which re-reads
    it before EVERY save — so it stays correct even when one in-memory instance
    is put through several transitions. Relying on ``_reactivate_ticket`` alone
    would not do: that flag is set only by the scanner's undo, so restoring a
    seat through the admin's ``list_editable`` column would leave the holder
    with a permanently voided ticket. Plenty of saves leave a row confirmed
    with no transition at all, and each would otherwise fire a push.
    """
    if created or not instance.apple_wallet_ticket_serial:
        return

    pass_type_id = getattr(settings, "WALLET_APPLE_PASS_TYPE_IDENTIFIER", None)
    if not pass_type_id:
        return

    # Fire when the pass's VOIDED state flips — in either direction — rather
    # than whenever the row happens to be invalid. Editing any other field on
    # an already-cancelled registration used to push a rebuild that could not
    # differ from the installed pass.
    #
    # Validity tracks SEAT_HOLDING_STATUSES, matching what build_apple_event_
    # ticket encodes, so moving a seat to `waitlist` or `no_show` un-voids or
    # voids the pass exactly like `cancelled` does.
    #
    # _previous_status (remember_previous_registration_status, above) is
    # re-read before EVERY save, so it stays correct when one in-memory
    # instance is put through several transitions — confirmed -> cancelled ->
    # confirmed on the same object would otherwise still report the status the
    # row was first loaded with, and the restoration would emit no refresh.
    previous_status = getattr(instance, "_previous_status", None)
    was_valid = previous_status in SEAT_HOLDING_STATUSES
    is_valid = instance.status in SEAT_HOLDING_STATUSES

    # An account merge reassigns the row (save(update_fields=['user'])), which
    # changes the attendee NAME the rebuild prints while leaving the status
    # untouched — a validity-only test would ignore it entirely.
    owner_changed = update_fields is not None and "user" in set(update_fields)

    if not (was_valid != is_valid or owner_changed):
        return

    serial = instance.apple_wallet_ticket_serial

    def _after_commit():
        # Deferred for the same reason as the Google path: the door's callers
        # save inside a transaction holding a row lock, and this makes a
        # network call. It also guarantees the rebuild that Wallet triggers
        # reads committed state.
        try:
            from .wallet.passkit_service import trigger_pass_refresh

            trigger_pass_refresh(pass_type_id, serial)
        except Exception as e:
            logger.error(
                f"Error refreshing Apple event ticket for registration "
                f"{instance.id}: {e}"
            )

    transaction.on_commit(_after_commit)


@receiver(post_save, sender=EventRegistration)
def assign_coach_on_first_attendance(sender, instance, created, **kwargs):
    """
    Grant a permanent coach the first time a member attends an event.

    Premium gates (``coach_assigned`` events and Crush Connect) depend on
    ``CrushProfile.assigned_coach``. This is the path that earns it: when a
    registration is marked "attended" and the member has no coach yet, the
    coach who scanned them in (``instance._checkin_coach``, set by
    ``views_checkin.event_checkin_api``) becomes their permanent coach —
    the person who actually met them at the door, not whoever happens to be
    first in the event's coach list. Anonymous/admin attendance transitions
    carry no scanner and fall back to the event's first assigned coach.
    Idempotent — once a coach is assigned the member keeps it, and events
    with no coaches simply leave the member unassigned until a coached event
    is attended.

    Records the grant on the registration (``checkin_granted_coach`` +
    ``checkin_granted_coach_at``) so ``coach_undo_checkin`` can take it back
    from a record rather than from a timestamp comparison it has no way of
    attributing to a workflow.
    """
    if instance.status != "attended":
        return

    profile = CrushProfile.objects.filter(user_id=instance.user_id).first()
    if profile is None or profile.assigned_coach_id:
        return

    coach = getattr(instance, "_checkin_coach", None) or instance.event.coaches.first()
    if coach is None:
        return

    granted_at = timezone.now()
    profile.assigned_coach = coach
    profile.assigned_coach_at = granted_at
    profile.save(update_fields=["assigned_coach", "assigned_coach_at"])

    # Written with a queryset update, not `instance.save()`: this IS the
    # post_save handler for EventRegistration and re-saving would re-enter it.
    # The in-memory instance is updated too, so the calling view's response
    # and its own later reads see the grant without a refresh.
    instance.checkin_granted_coach = coach
    instance.checkin_granted_coach_at = granted_at
    EventRegistration.objects.filter(pk=instance.pk).update(
        checkin_granted_coach=coach,
        checkin_granted_coach_at=granted_at,
    )
    logger.info(
        "[COACH-ASSIGN] Assigned coach pk=%s to profile pk=%s on attendance "
        "of event pk=%s",
        coach.pk,
        profile.pk,
        instance.event_id,
    )


# =============================================================================
# COACH STAFF STATUS MANAGEMENT
# =============================================================================


@receiver(post_save, sender=CrushCoach)
def manage_coach_staff_status(sender, instance, created, **kwargs):
    """
    Automatically manage staff status for Crush coaches.

    When a CrushCoach record is created or updated:
    - If coach is_active=True: Grant is_staff=True to allow admin panel access
    - If coach is_active=False: Revoke is_staff=True (unless user is superuser)

    This enables coaches to log into the admin panel via social login (Google, Microsoft)
    without requiring manual staff status assignment.

    Note: Superusers are never affected - their staff status is preserved.
    """
    user = instance.user

    # Never modify superuser accounts
    if user.is_superuser:
        return

    try:
        if instance.is_active:
            # Grant staff status to active coaches
            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=["is_staff"])
                logger.info(f"Granted staff status to coach: {user.email}")
        else:
            # Revoke staff status from inactive coaches
            # Only revoke if they don't have other admin roles
            if user.is_staff:
                user.is_staff = False
                user.save(update_fields=["is_staff"])
                logger.info(f"Revoked staff status from inactive coach: {user.email}")

    except Exception as e:
        logger.error(f"Error managing staff status for coach {user.id}: {e}")


# =============================================================================
# OUTLOOK CONTACT SYNC
# =============================================================================

# Fields that should trigger an Outlook contact update when changed
OUTLOOK_SYNC_PROFILE_FIELDS = {
    "phone_number",
    "phone_verified",
    "location",
    "date_of_birth",
    "gender",
    "photo_1",
}

# User fields that should trigger an Outlook contact update
OUTLOOK_SYNC_USER_FIELDS = {
    "first_name",
    "last_name",
    "email",
}

# Test email domains that should NEVER sync to Outlook
TEST_EMAIL_DOMAINS = [
    "example.com",
    "example.org",
    "test.com",
    "localhost",
    "crush.test",
]


def is_test_user(user):
    """
    Check if user is a test/sample user that should never sync to Outlook.

    This is a defense-in-depth check - is_sync_enabled() should already
    block test execution, but this catches edge cases.

    Args:
        user: Django User instance

    Returns:
        bool: True if user is a test user, False otherwise
    """
    email_domain = user.email.split("@")[-1] if user.email and "@" in user.email else ""
    return email_domain.lower() in TEST_EMAIL_DOMAINS


@receiver(post_save, sender=CrushProfile)
def sync_profile_to_outlook(sender, instance, created, update_fields, **kwargs):
    """
    Automatically sync phone-verified profiles to Outlook contacts (production only).

    Only syncs profiles that:
    1. Have a verified phone number (phone_verified=True, required for caller ID)
    2. Are NOT test users (no example.com, test.com, etc.)

    This enables caller ID recognition when Crush.lu users call, regardless of
    profile approval status.

    Args:
        sender: The model class (CrushProfile)
        instance: The CrushProfile instance being saved
        created: True if this is a new profile
        update_fields: Set of field names being updated (if using update_fields)
        **kwargs: Additional signal arguments
    """
    from crush_lu.services.graph_contacts import (
        INTERACTIVE_RETRY_SLEEP_TOTAL,
        GraphContactsService,
        is_sync_enabled,
    )

    # Skip sync if not in production environment
    if not is_sync_enabled():
        return

    # CRITICAL: Never sync test users (example.com, test.com, localhost)
    if is_test_user(instance.user):
        logger.debug(f"Skipping Outlook sync for test user: {instance.user.email}")
        return

    # This signal runs on the web request thread and every branch below makes
    # blocking Graph calls, so the work is deferred to on_commit: outside a
    # transaction it runs immediately, and inside one it can no longer write a
    # contact for a profile whose transaction later rolls back. The tightened
    # retry budget keeps a throttled Graph call from parking the request.
    def _service():
        return GraphContactsService(retry_budget=INTERACTIVE_RETRY_SLEEP_TOTAL)

    # A contact should exist only for a profile with a *verified* phone number.
    # Both "no number at all" and "number no longer verified" mean any contact
    # we synced earlier has to go. These used to be separate: a cleared number
    # returned early, before the delete branch, stranding the contact in the
    # shared mailbox forever -- the same silent failure this module is being
    # cleaned up for.
    if not instance.phone_number or not instance.phone_verified:
        if instance.outlook_contact_id:
            contact_id = instance.outlook_contact_id

            reason = (
                "phone cleared" if not instance.phone_number else "phone unverified"
            )

            def _delete_stale_contact():
                try:
                    deleted = _service().delete_contact(contact_id)

                    # Only drop the ID once the contact is actually gone.
                    # delete_contact() returns False for a throttle that
                    # outlasted the retry budget, a 5xx, or an auth failure --
                    # clearing on those would discard the only DB handle to a
                    # contact still sitting in the shared mailbox, so neither a
                    # later sync nor the DB-tracked cleanup could remove its PII.
                    if not deleted:
                        logger.warning(
                            f"Failed to delete Outlook contact {contact_id} for "
                            f"profile {instance.pk} ({reason}); keeping the ID so "
                            f"it can be retried"
                        )
                        return

                    # Clear the contact ID using update() to avoid infinite recursion
                    CrushProfile.objects.filter(pk=instance.pk).update(
                        outlook_contact_id=""
                    )
                    logger.info(
                        f"Deleted Outlook contact for profile {instance.pk} ({reason})"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to delete Outlook contact for profile "
                        f"{instance.pk} ({reason}): {e}"
                    )

            transaction.on_commit(_delete_stale_contact)
        return

    # Check if relevant fields were updated (if update_fields provided)
    should_sync = False

    if created:
        # Always sync new profiles with phone numbers
        should_sync = True
    elif update_fields is not None:
        # Check if any Outlook-relevant fields were updated
        updated_fields = set(update_fields)
        if updated_fields & OUTLOOK_SYNC_PROFILE_FIELDS:
            should_sync = True
    else:
        # Full save (no update_fields) - sync to be safe
        should_sync = True

    if should_sync:

        def _sync():
            try:
                _service().sync_profile(instance)
            except Exception as e:
                # Don't fail the save if Outlook sync fails
                logger.warning(f"Failed to sync profile {instance.pk} to Outlook: {e}")

        transaction.on_commit(_sync)


@receiver(post_save, sender=User)
def sync_user_to_outlook_on_name_change(
    sender, instance, created, update_fields, **kwargs
):
    """
    Sync Outlook contact when User name/email changes.

    This handles cases where the User model is updated independently
    of CrushProfile (e.g., admin changes name, user changes email).

    Only runs in production when OUTLOOK_CONTACT_SYNC_ENABLED=true.
    """
    from crush_lu.services.graph_contacts import (
        INTERACTIVE_RETRY_SLEEP_TOTAL,
        GraphContactsService,
        is_sync_enabled,
    )

    # Skip for new users (no profile yet)
    if created:
        return

    # Skip sync if not in production environment
    if not is_sync_enabled():
        return

    # Check if relevant fields were updated
    if update_fields is not None:
        updated_fields = set(update_fields)
        if not (updated_fields & OUTLOOK_SYNC_USER_FIELDS):
            return

    # Check if user has a CrushProfile with Outlook sync
    try:
        profile = instance.crushprofile
    except CrushProfile.DoesNotExist:
        return

    # Only sync if profile has a *verified* phone number and is already synced.
    #
    # phone_verified matters here specifically because a failed delete now keeps
    # outlook_contact_id on purpose, so it can be retried. Without this check, a
    # profile mid-removal still looks synced, and a later name or email change
    # would call sync_profile() -- which does not enforce phone_verified -- and
    # cheerfully update or recreate the very contact the other handler is trying
    # to delete.
    if (
        not profile.phone_number
        or not profile.phone_verified
        or not profile.outlook_contact_id
    ):
        return

    def _sync():
        try:
            GraphContactsService(
                retry_budget=INTERACTIVE_RETRY_SLEEP_TOTAL
            ).sync_profile(profile)
        except Exception as e:
            logger.warning(f"Failed to sync user {instance.pk} to Outlook: {e}")

    transaction.on_commit(_sync)


@receiver(pre_delete, sender=CrushProfile)
def delete_outlook_contact_on_profile_delete(sender, instance, **kwargs):
    """
    Delete Outlook contact when CrushProfile is deleted (production only).

    This handler fires when:
    1. A CrushProfile is deleted directly via admin
    2. A User is deleted (cascades to CrushProfile due to OneToOneField)

    The contact is removed from the shared mailbox to prevent orphaned entries.
    Only runs in production when OUTLOOK_CONTACT_SYNC_ENABLED=true.

    Args:
        sender: The model class (CrushProfile)
        instance: The CrushProfile instance being deleted
        **kwargs: Additional signal arguments
    """
    from crush_lu.services.graph_contacts import (
        INTERACTIVE_RETRY_SLEEP_TOTAL,
        GraphContactsService,
        is_sync_enabled,
    )

    # Skip deletion if not in production environment
    if not is_sync_enabled():
        return

    # Only delete if profile has an Outlook contact
    if not instance.outlook_contact_id:
        logger.debug(
            f"Profile {instance.pk} has no Outlook contact ID - skipping deletion"
        )
        return

    # Capture what we need now: this is pre_delete, so by the time the callback
    # runs the row is gone and instance.user would re-query a deleted user.
    contact_id = instance.outlook_contact_id
    profile_pk = instance.pk
    user_email = instance.user.email

    def _delete():
        try:
            success = GraphContactsService(
                retry_budget=INTERACTIVE_RETRY_SLEEP_TOTAL
            ).delete_contact(contact_id)

            if success:
                logger.info(
                    f"Deleted Outlook contact {contact_id} for profile {profile_pk} "
                    f"(user: {user_email})"
                )
            else:
                logger.warning(
                    f"Failed to delete Outlook contact {contact_id} for profile {profile_pk}"
                )
        except Exception as e:
            # Don't fail the deletion if Outlook sync fails
            # The profile will be deleted regardless, but log critically for manual cleanup
            logger.error(
                f"CRITICAL: Failed to delete Outlook contact {contact_id} "
                f"for profile {profile_pk}. MANUAL CLEANUP REQUIRED. Error: {e}"
            )

    # Deferred to on_commit so a rolled-back delete doesn't destroy the Outlook
    # contact whose ID we would then have lost.
    transaction.on_commit(_delete)


# =============================================================================
# CRUSH SPARK - SENDER REVEAL ON JOURNEY COMPLETION
# =============================================================================


@receiver(post_save, sender=JourneyProgress)
def reveal_spark_sender_on_journey_completion(sender, instance, **kwargs):
    """
    When a journey linked to a CrushSpark is completed (Chapter 6 done),
    reveal the sender's identity by updating the spark status.
    """
    if not instance.is_completed:
        return

    # Check if this journey is linked to a spark
    try:
        spark = CrushSpark.objects.get(
            journey=instance.journey,
            status=CrushSpark.Status.DELIVERED,
        )
    except CrushSpark.DoesNotExist:
        return

    spark.is_sender_revealed = True
    spark.revealed_at = timezone.now()
    spark.status = CrushSpark.Status.COMPLETED
    spark.completed_at = timezone.now()
    spark.save(
        update_fields=[
            "is_sender_revealed",
            "revealed_at",
            "status",
            "completed_at",
        ]
    )
    logger.info(
        f"Crush Spark {spark.pk}: Sender revealed to recipient "
        f"(sender={spark.sender_id}, recipient={spark.recipient_id})"
    )


# =============================================================================
# HYBRID COACH REVIEW — SLA TIMER (Phase 3)
# =============================================================================


@receiver(post_save, sender=ProfileSubmission)
def start_sla_on_coach_assignment(sender, instance, created, **kwargs):
    """Stamp assigned_at and sla_deadline when a submission first gets a coach.

    Uses an idempotent UPDATE with an assigned_at__isnull=True guard so re-saves
    (e.g. admin edits, status transitions) never reset the clock. Values are set
    unconditionally — the fields drive the coach-side sla_state bucket and are
    informational; the Phase 3 SLA task gates its own actions on
    coach.hybrid_features_enabled.
    """
    from datetime import timedelta

    if instance.coach_id is None or instance.assigned_at is not None:
        return

    now = timezone.now()
    ProfileSubmission.objects.filter(pk=instance.pk, assigned_at__isnull=True).update(
        assigned_at=now,
        sla_deadline=now + timedelta(hours=48),
    )


@receiver(email_confirmation_sent)
def stash_pending_verification_email(sender, request, confirmation, signup, **kwargs):
    """Stash the pending email in session so the unauthenticated verification-sent
    page can offer a working "resend" without an authenticated lookup.

    Fires whenever allauth sends a confirmation email — fresh signup (custom
    crush_lu:signup view, allauth's stock account_signup used by gift_landing,
    social signup), login attempts by existing-but-unverified users under
    ACCOUNT_EMAIL_VERIFICATION="mandatory", and the resend endpoint itself.
    Using email_confirmation_sent (rather than user_signed_up) ensures the
    session key is populated on the login path too, so blocked users can
    request another link from the verification-sent page.
    """
    if request is None or confirmation is None:
        return
    email = getattr(getattr(confirmation, "email_address", None), "email", None)
    if email:
        request.session["pending_verification_email"] = email


@receiver(email_confirmed)
def stash_confirmed_email_for_login_prefill(sender, request, email_address, **kwargs):
    """After email confirmation, stash the address in session so the next page
    (the login form) can prefill it. Also surface a friendly success message.

    Only fires when the user is NOT already authenticated — when they ARE
    authenticated, allauth uses ACCOUNT_EMAIL_CONFIRMATION_AUTHENTICATED_REDIRECT_URL
    instead and prefilling is irrelevant.
    """
    if request is None:
        return
    if getattr(request, "user", None) and request.user.is_authenticated:
        return
    request.session["login_prefill_email"] = email_address.email
    messages.success(
        request,
        _("Email verified — please sign in to continue."),
    )


# ---------------------------------------------------------------------------
# Quiz media cleanup
# ---------------------------------------------------------------------------
@receiver(post_delete, sender="crush_lu.QuizQuestion")
def delete_quiz_question_media(sender, instance, **kwargs):
    """Remove the uploaded media Blob when a QuizQuestion is deleted.

    Django's FileField does not delete the underlying storage object on row
    deletion, so without this the public crush_media container accumulates
    orphaned uploads whenever a question, round, or quiz is removed (the
    coach delete endpoint and admin both hit this path via CASCADE).
    Best-effort: storage errors must not block the delete.
    """
    field = instance.media_file
    name = field.name if field else ""
    if not name:
        return
    try:
        field.storage.delete(name)
    except Exception:
        logger.exception("Failed to delete quiz media Blob: %s", name)
