"""
Newsletter send service for Crush.lu.

Handles audience resolution, rate-limited sending via Microsoft Graph API,
and per-recipient tracking for resumability.
"""
import logging
import time

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone, translation

from azureproject.email_utils import html_to_plain_text, send_domain_email
from .email_helpers import can_send_email, get_social_links
from .models.newsletter import NewsletterRecipient
from .utils.i18n import build_absolute_url, get_user_preferred_language

from crush_lu.models.events import SEAT_HOLDING_STATUSES
logger = logging.getLogger(__name__)

# Rate limiting: 25 emails per batch, 62s pause (Graph API limit is 30/min)
BATCH_SIZE = 25
BATCH_PAUSE_SECONDS = 62


def resolve_audience(audience, segment_key=''):
    """
    Resolve an audience choice (Newsletter.AUDIENCE_CHOICES) to a User queryset.

    Shared by newsletter sending and the multi-channel campaign service
    (crush_lu/services/campaigns.py) so both target audiences identically.

    Only users with a recorded Crush.lu consent
    (``UserDataConsent.crushlu_consent_given``) are returned. Profiles are
    lazily created for cross-domain accounts on their first crush.lu login and
    ConsentMiddleware only then asks them to consent, so a profile alone is not
    consent. Users without a consent row are dropped too (no consent recorded).
    """
    users = _resolve_audience_unfiltered(audience, segment_key)
    return users.filter(data_consent__crushlu_consent_given=True)


def _resolve_audience_unfiltered(audience, segment_key=''):
    if audience == 'all_users':
        return User.objects.filter(is_active=True, crushprofile__isnull=False)
    elif audience == 'all_profiles':
        return User.objects.filter(is_active=True, crushprofile__isnull=False)
    elif audience == 'approved_profiles':
        return User.objects.filter(
            is_active=True, crushprofile__verification_status="verified"
        )
    elif audience == 'pending_review':
        from .models.profiles import ProfileSubmission
        pending_user_ids = ProfileSubmission.objects.filter(
            status='pending',
        ).values_list('profile__user_id', flat=True)
        return User.objects.filter(is_active=True, id__in=pending_user_ids)
    elif audience == 'segment':
        return _get_segment_users(segment_key)

    logger.error(f"Unknown audience type: {audience}")
    return User.objects.none()


def has_current_consent(user):
    """True while the user still has Crush.lu consent and is not banned.

    Audience querysets are evaluated once, but a send run can pause for
    minutes between batches; account deletion (consent revoked, ban set) can
    land in between. Every per-recipient send loop re-checks this right before
    it sends, using a fresh read of the consent row.
    """
    from .models.profiles import UserDataConsent

    return UserDataConsent.objects.filter(
        user_id=user.pk, crushlu_consent_given=True, crushlu_banned=False
    ).exists()


def is_on_break(user):
    """Fresh read of the self-service break state (see exclude_on_break_users)."""
    from .models.profiles import CrushProfile

    return CrushProfile.objects.filter(
        user_id=user.pk, on_break_at__isnull=False
    ).exists()


class ConsentRevokedBeforeSend(Exception):
    """Raised by the final pre-send check; a privacy skip, never a failure."""


def final_send_address(user):
    """Last check before handing a message to the provider (no work after it).

    Returns the CURRENT address, or raises ConsentRevokedBeforeSend if consent
    is gone or the user is banned. Locks are deliberately not held across the
    provider call (it can take tens of seconds); a message already accepted by
    the provider cannot be recalled, so the residual window is the time between
    this read and the provider accepting the request.
    """
    if not has_current_consent(user):
        raise ConsentRevokedBeforeSend()
    address = (
        User.objects.filter(pk=user.pk).values_list('email', flat=True).first()
    )
    if not address:
        raise ConsentRevokedBeforeSend()
    return address


def anonymize_newsletter_receipts(user):
    """Blank the address and error text on a user's NewsletterRecipient rows.

    Called by account deletion after the consent revocation has committed.
    Together with write_receipt() this closes the race with an in-flight send:
    a receipt written before the revocation is blanked here, and one written
    after it is stored blank. Rows and statuses are kept for send counts.
    """
    return NewsletterRecipient.objects.filter(user=user).update(
        email='', error_message=''
    )


def locked_consent_holds(user):
    """Re-read consent under a row lock. Call inside transaction.atomic().

    select_for_update serialises with account deletion on Postgres (SQLite
    ignores it). True only while consent is given and the user is not banned.
    """
    from .models.profiles import UserDataConsent

    return (
        UserDataConsent.objects.select_for_update()
        .filter(user_id=user.pk, crushlu_consent_given=True, crushlu_banned=False)
        .exists()
    )


SUPPRESSED_CONSENT_REVOKED = 'suppressed: consent revoked'


class ReceiptResult(tuple):
    """(row, created) plus ``allowed``: whether consent held at the locked read.

    Callers MUST NOT send when ``allowed`` is False (consent revoked or user
    banned between their earlier check and this write).
    """

    def __new__(cls, row, created, allowed):
        obj = super().__new__(cls, (row, created))
        obj.allowed = allowed
        return obj


def write_receipt(newsletter, user, defaults):
    """Write a NewsletterRecipient receipt, storing the address only while the
    user still has consent, and report whether sending is still allowed.

    The consent row is locked and re-read in the same transaction as the
    write, so the write either sees the revocation or commits first and is
    blanked by anonymize_newsletter_receipts() when deletion runs it after the
    revocation commits. When consent is gone the stored email is blank and any
    caller-supplied error text (which may contain the address) is replaced by
    a generic marker; the returned ``allowed`` is False and the caller must not
    send.
    """
    with transaction.atomic():
        allowed = locked_consent_holds(user)
        values = dict(defaults)
        if allowed:
            values['email'] = (
                User.objects.filter(pk=user.pk)
                .values_list('email', flat=True)
                .first()
                or ''
            )
        else:
            values['email'] = ''
            if 'error_message' in values or values.get('status') == 'failed':
                values['error_message'] = SUPPRESSED_CONSENT_REVOKED
        row, created = NewsletterRecipient.objects.update_or_create(
            newsletter=newsletter, user=user, defaults=values,
        )
        return ReceiptResult(row, created, allowed)


def exclude_banned_users(users):
    """Exclude users who deleted their profile or are banned from Crush.lu."""
    from .models.profiles import UserDataConsent

    banned_user_ids = UserDataConsent.objects.filter(
        crushlu_banned=True
    ).values_list('user_id', flat=True)
    return users.exclude(id__in=banned_user_ids)


def exclude_on_break_users(users):
    """Exclude members who self-served a "Take a break" (UX Wave 3 · WP13).

    Applied everywhere `exclude_banned_users` is (newsletter recipients and
    the shared campaign audience resolver in services/campaigns.py), so every
    marketing/event-announcement channel honours the break identically.
    Transactional and security email never goes through these resolvers, so
    it is unaffected.
    """
    from .models.profiles import CrushProfile

    on_break_user_ids = CrushProfile.objects.filter(
        on_break_at__isnull=False
    ).values_list("user_id", flat=True)
    return users.exclude(id__in=on_break_user_ids)


def apply_language_filter(users, language):
    """Restrict a User queryset to a preferred language ('all' = no filter)."""
    if not language or language == 'all':
        return users
    if language == 'en':
        # English includes users without a profile (they default to English)
        return users.filter(
            Q(crushprofile__preferred_language='en')
            | Q(crushprofile__isnull=True)
        )
    return users.filter(crushprofile__preferred_language=language)


def get_newsletter_recipients(newsletter):
    """
    Resolve the audience for a newsletter into a User queryset.

    Filters:
    - email_newsletter preference must be True (or no preference record yet)
    - unsubscribed_all must be False
    - Excludes users already sent/skipped for this newsletter (resumability)

    Args:
        newsletter: Newsletter instance

    Returns:
        QuerySet of User objects
    """
    from .models import EmailPreference

    users = resolve_audience(newsletter.audience, newsletter.segment_key)

    # Exclude users who opted out of newsletters
    opted_out_user_ids = EmailPreference.objects.filter(
        Q(email_newsletter=False) | Q(unsubscribed_all=True)
    ).values_list('user_id', flat=True)
    users = users.exclude(id__in=opted_out_user_ids)

    users = exclude_banned_users(users)
    users = exclude_on_break_users(users)
    users = apply_language_filter(users, newsletter.language)

    # For event announcements, exclude users already registered for the event
    if newsletter.event_id:
        from .models.events import EventRegistration
        registered_user_ids = EventRegistration.objects.filter(
            event=newsletter.event,
            status__in=[*SEAT_HOLDING_STATUSES, 'waitlist'],
        ).values_list('user_id', flat=True)
        users = users.exclude(id__in=registered_user_ids)

    # Exclude users already processed for this newsletter (resumability)
    # Skip this filter for unsaved newsletters (e.g. estimate-only calls)
    if newsletter.pk:
        already_processed_ids = NewsletterRecipient.objects.filter(
            newsletter=newsletter,
            status__in=['sent', 'skipped'],
        ).values_list('user_id', flat=True)
        users = users.exclude(id__in=already_processed_ids)

    return users.distinct()


# Segment keys retired when their bucket was split into finer segments.
# Existing newsletters may still reference the old key, so we expand it to
# the union of the current keys it became (issue #190: age 40+ -> 5 bands).
LEGACY_SEGMENT_ALIASES = {
    "age_40_plus": (
        "age_40_44",
        "age_45_49",
        "age_50_54",
        "age_55_59",
        "age_60_plus",
    ),
}


def _get_segment_users(segment_key):
    """
    Resolve a segment key to a User queryset.

    Uses get_segment_definitions() from user_segments.py to find the queryset,
    then maps profile/activity querysets to User objects. Retired keys are
    resolved via LEGACY_SEGMENT_ALIASES so older newsletters still reach
    recipients.
    """
    from .admin.user_segments import get_segment_definitions

    segments = get_segment_definitions()

    # Search through all segment groups for matching key
    for group in segments.values():
        for segment in group.get('segments', []):
            if segment['key'] == segment_key:
                qs = segment['queryset']
                # The queryset may be CrushProfile, UserActivity, etc.
                # We need to map it to User objects.
                model_name = qs.model.__name__
                if model_name == 'User':
                    return qs.filter(is_active=True)
                elif hasattr(qs.model, 'user'):
                    # CrushProfile, UserActivity, etc. have a user FK
                    return User.objects.filter(
                        is_active=True,
                        id__in=qs.values_list('user_id', flat=True),
                    )
                elif hasattr(qs.model, 'profile'):
                    # ProfileSubmission has profile -> user
                    return User.objects.filter(
                        is_active=True,
                        crushprofile__submissions__in=qs,
                    )
                else:
                    logger.warning(
                        f"Segment '{segment_key}' queryset model "
                        f"'{model_name}' has no user mapping"
                    )
                    return User.objects.none()

    alias_keys = LEGACY_SEGMENT_ALIASES.get(segment_key)
    if alias_keys:
        users = User.objects.none()
        for alias_key in alias_keys:
            users = users | _get_segment_users(alias_key)
        return users.distinct()

    logger.error(f"Segment key not found: {segment_key}")
    return User.objects.none()


def send_newsletter(newsletter, dry_run=False, limit=None, stdout=None,
                    link_rewriter=None, should_abort=None):
    """
    Send a newsletter to its audience with rate limiting and resumability.

    Args:
        newsletter: Newsletter instance (must be 'draft' or 'sending')
        dry_run: If True, preview recipients without sending
        limit: Maximum number of emails to send (None = no limit)
        stdout: Optional output stream for progress (management command)
        link_rewriter: Optional callable(html, user) applied to the rendered
            HTML body just before sending — used by campaign sends for click
            tracking. None (the default) keeps output byte-identical.
        should_abort: Optional zero-arg callable checked before each send;
            returning True stops the run without finalizing (used by campaign
            sends so a cancellation halts the batch mid-flight).

    Returns:
        dict: {'sent': int, 'failed': int, 'skipped': int}
    """
    def log(msg, style=None):
        if stdout:
            if style:
                stdout.write(style(msg))
            else:
                stdout.write(msg)
        logger.info(msg)

    if newsletter.status not in ('draft', 'sending'):
        raise ValueError(
            f"Newsletter {newsletter.pk} has status '{newsletter.status}', "
            f"expected 'draft' or 'sending'"
        )

    recipients = get_newsletter_recipients(newsletter)
    if limit is not None:
        # Bounded (dispatcher) runs never retry previously-failed recipients
        # (a permanently bouncing address would head every batch and the send
        # could never converge) nor unresolved pre-send claims (a crashed
        # worker may already have delivered — at-most-once wins). Unlimited
        # manual runs keep retrying both.
        recipients = recipients.exclude(
            id__in=_unresumable_recipient_ids(newsletter)
        )
    total_eligible = recipients.count()

    if limit:
        recipients = recipients[:limit]

    recipient_count = min(total_eligible, limit) if limit else total_eligible

    log(f"Newsletter #{newsletter.pk}: '{newsletter.subject}'")
    log(f"Audience: {newsletter.get_audience_display()}")
    log(f"Eligible recipients: {total_eligible}")
    if limit:
        log(f"Limit: {limit} (sending to {recipient_count})")

    if dry_run:
        log(f"\n[DRY RUN] Would send to {recipient_count} recipients:")
        for user in recipients:
            try:
                log(f"  {user.email} ({user.first_name} {user.last_name})")
            except UnicodeEncodeError:
                log(f"  {user.email}")
        return {'sent': 0, 'failed': 0, 'skipped': 0}

    # Set status to sending
    newsletter.status = 'sending'
    newsletter.total_recipients = total_eligible
    newsletter.save(update_fields=['status', 'total_recipients', 'updated_at'])

    sent = 0
    failed = 0
    skipped = 0
    batch_count = 0

    # Materialize the queryset to avoid issues with batching
    user_ids = list(recipients.values_list('id', flat=True))

    aborted = False
    for i, user_id in enumerate(user_ids):
        if should_abort is not None and should_abort():
            log("  Send aborted by caller signal")
            aborted = True
            break

        # Rate limiting: pause between batches
        if batch_count > 0 and batch_count % BATCH_SIZE == 0:
            log(f"  Batch pause ({BATCH_PAUSE_SECONDS}s) after {batch_count} emails...")
            time.sleep(BATCH_PAUSE_SECONDS)

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            skipped += 1
            continue

        # Double-check consent and preference (may have changed since the
        # queryset was evaluated, e.g. account deletion during a batch pause)
        consented = has_current_consent(user)
        if (
            not consented
            or is_on_break(user)
            or not can_send_email(user, 'newsletter')
        ):
            write_receipt(
                newsletter, user, defaults={
                    'status': 'skipped',
                    'error_message': 'User opted out of newsletters',
                },
            )
            skipped += 1
            continue

        # Durable pre-send claim: a crash after the Graph send but before the
        # receipt write must not cause a duplicate email on the next bounded
        # run (stale claims are swept to 'failed' below).
        claim = write_receipt(
            newsletter, user, defaults={'status': 'pending'},
        )
        if not claim.allowed:
            # Consent was revoked between the early check and the locked
            # write: a privacy skip. Never send, never retry.
            write_receipt(
                newsletter, user,
                defaults={
                    'status': 'skipped',
                    'error_message': SUPPRESSED_CONSENT_REVOKED,
                },
            )
            skipped += 1
            continue

        try:
            delivery_count = _send_newsletter_to_user(
                newsletter, user, link_rewriter
            )
            if delivery_count == 0:
                write_receipt(
                    newsletter, user, defaults={
                        'status': 'skipped',
                        'sent_at': None,
                        'error_message': 'Active hard-bounce suppression',
                    },
                )
                skipped += 1
                continue
            write_receipt(
                newsletter, user, defaults={
                    'status': 'sent',
                    'sent_at': timezone.now(),
                },
            )
            sent += 1
            batch_count += 1
            if stdout and (sent % 10 == 0):
                log(f"  Sent {sent}/{recipient_count}...")

        except ConsentRevokedBeforeSend:
            write_receipt(
                newsletter, user,
                defaults={
                    'status': 'skipped',
                    'error_message': SUPPRESSED_CONSENT_REVOKED,
                },
            )
            skipped += 1
            continue
        except Exception as e:
            error_msg = str(e)[:500]
            write_receipt(
                newsletter, user, defaults={
                    'status': 'failed',
                    'error_message': error_msg,
                },
            )
            failed += 1
            logger.error(
                f"Failed to send newsletter #{newsletter.pk} to {user.email}: {e}",
                exc_info=True,
            )

    # Any recipient still 'pending' now is a stale claim from an interrupted
    # earlier run — the outcome is unknown, so count it as failed instead of
    # letting the newsletter (and its campaign) finalize as a clean 'sent'.
    unresolved = NewsletterRecipient.objects.filter(
        newsletter=newsletter, status='pending',
    ).exclude(user_id__in=user_ids).update(
        status='failed',
        error_message='Unresolved send claim — outcome unknown '
                      '(worker interrupted mid-send)',
    )
    if unresolved:
        log(f"  Marked {unresolved} unresolved send claim(s) as failed")

    # Update newsletter stats
    newsletter.total_sent = (
        NewsletterRecipient.objects.filter(
            newsletter=newsletter, status='sent'
        ).count()
    )
    newsletter.total_failed = (
        NewsletterRecipient.objects.filter(
            newsletter=newsletter, status='failed'
        ).count()
    )
    newsletter.total_skipped = (
        NewsletterRecipient.objects.filter(
            newsletter=newsletter, status='skipped'
        ).count()
    )

    if limit is not None or aborted:
        remaining = (
            get_newsletter_recipients(newsletter)
            .exclude(id__in=_unresumable_recipient_ids(newsletter))
            .count()
        )
        if remaining > 0 or aborted:
            # Bounded batch with recipients still eligible (or an aborted
            # run): stay 'sending' so a later run continues where this one
            # stopped instead of prematurely finalizing the newsletter.
            newsletter.save(update_fields=[
                'total_sent', 'total_failed', 'total_skipped', 'updated_at',
            ])
            log(
                f"\nBatch done. Sent: {sent}, Failed: {failed}, "
                f"Skipped: {skipped} ({remaining} eligible remaining)"
            )
            return {
                'sent': sent,
                'failed': failed,
                'skipped': skipped,
                'complete': False,
                'remaining': remaining,
                'aborted': aborted,
            }

    # Bounded runs must finalize from the persisted per-recipient failures:
    # a failure from an earlier tick is excluded from later batches, so this
    # run's local counter can be 0 while total_failed is not. Unlimited runs
    # keep the historical this-run semantics.
    terminal_failed = newsletter.total_failed if limit is not None else failed
    newsletter.status = 'sent' if terminal_failed == 0 else 'failed'
    newsletter.sent_at = timezone.now()
    newsletter.save(update_fields=[
        'total_sent', 'total_failed', 'total_skipped',
        'status', 'sent_at', 'updated_at',
    ])

    log(f"\nDone! Sent: {sent}, Failed: {failed}, Skipped: {skipped}")
    return {
        'sent': sent,
        'failed': failed,
        'skipped': skipped,
        'complete': True,
        'remaining': 0,
    }


def _unresumable_recipient_ids(newsletter):
    """User ids bounded runs must not retry.

    'failed' is terminal; 'pending' is an unresolved pre-send claim from a
    crashed worker whose outcome is unknown (possibly delivered).
    """
    return NewsletterRecipient.objects.filter(
        newsletter=newsletter, status__in=['failed', 'pending'],
    ).values_list('user_id', flat=True)


def render_event_announcement(event, user, lang):
    """
    Render the event announcement email template for a specific user and language.

    Uses translation.override() so that event.title/description return the
    translated version, and all {% trans %} tags render in the user's language.

    Returns:
        tuple: (subject, html_message)
    """
    from .models import EmailPreference

    email_prefs = EmailPreference.get_or_create_for_user(user)
    unsubscribe_url = build_absolute_url(
        'crush_lu:email_unsubscribe',
        lang=lang,
        kwargs={'token': email_prefs.unsubscribe_token},
    )
    event_url = build_absolute_url(
        'crush_lu:event_detail',
        lang=lang,
        kwargs={'event_id': event.pk},
    )

    # Get event image URL if available
    event_image_url = None
    if event.image:
        try:
            event_image_url = event.image.url
        except Exception:
            pass

    with translation.override(lang):
        subject = translation.gettext("New Event: %(title)s") % {
            'title': event.title,
        }

        context = {
            'user': user,
            'first_name': user.first_name,
            'event': event,
            'event_title': event.title,
            'event_description': event.description,
            'event_image_url': event_image_url,
            'event_url': event_url,
            'spots_remaining': event.spots_remaining,
            'unsubscribe_url': unsubscribe_url,
            'home_url': build_absolute_url('crush_lu:home', lang=lang),
            'about_url': build_absolute_url('crush_lu:about', lang=lang),
            'events_url': build_absolute_url('crush_lu:event_list', lang=lang),
            'settings_url': build_absolute_url(
                'crush_lu:edit_profile', lang=lang
            ) + '?section=account&sub=notifications',
            'social_links': get_social_links(),
            'LANGUAGE_CODE': lang,
        }

        html_message = render_to_string(
            'crush_lu/emails/event_announcement.html', context
        )

    return subject, html_message


NEWSLETTER_TEMPLATE_MAP = {
    'standard': 'crush_lu/emails/newsletter.html',
    'patch_notes': 'crush_lu/emails/patch_notes.html',
}


def _send_newsletter_to_user(newsletter, user, link_rewriter=None):
    """
    Render and send a newsletter email to a single user.

    Uses the user's preferred language for template rendering and URL generation.
    For event announcements, renders event_announcement.html with translated content.
    For standard/patch_notes newsletters, selects the template based on newsletter_type
    and reads translated fields inside translation.override() for correct language.
    Sends from love@crush.lu via Graph API.
    """
    from .models import EmailPreference

    lang = get_user_preferred_language(user=user, default='en')

    if newsletter.event_id:
        # Event announcement: auto-generate content per-user in their language
        subject, html_message = render_event_announcement(
            newsletter.event, user, lang
        )
        plain_message = html_to_plain_text(html_message)
    else:
        email_prefs = EmailPreference.get_or_create_for_user(user)
        unsubscribe_url = build_absolute_url(
            'crush_lu:email_unsubscribe',
            lang=lang,
            kwargs={'token': email_prefs.unsubscribe_token},
        )

        # Select template based on newsletter type
        template_name = NEWSLETTER_TEMPLATE_MAP.get(
            newsletter.newsletter_type, 'crush_lu/emails/newsletter.html'
        )

        # Read translated fields and render template inside translation.override()
        # so that modeltranslation returns the correct language variant
        with translation.override(lang):
            subject = newsletter.subject
            body_html = newsletter.body_html
            body_text = newsletter.body_text

            context = {
                'user': user,
                'first_name': user.first_name,
                'body_html': body_html,
                'unsubscribe_url': unsubscribe_url,
                'home_url': build_absolute_url('crush_lu:home', lang=lang),
                'about_url': build_absolute_url('crush_lu:about', lang=lang),
                'events_url': build_absolute_url(
                    'crush_lu:event_list', lang=lang
                ),
                'settings_url': build_absolute_url(
                    'crush_lu:edit_profile', lang=lang
                ) + '?section=account&sub=notifications',
                'social_links': get_social_links(),
                'LANGUAGE_CODE': lang,
            }

            html_message = render_to_string(template_name, context)

        if body_text:
            plain_message = body_text
        else:
            plain_message = html_to_plain_text(html_message)

    if link_rewriter is not None:
        # After plain_message is derived, so the text part keeps direct URLs.
        html_message = link_rewriter(html_message, user)

    # Final consent check and fresh address, immediately before the handoff.
    address = final_send_address(user)
    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[address],
        from_email=settings.CRUSH_NEWSLETTER_FROM_EMAIL,
        domain='crush.lu',
        fail_silently=False,
    )
