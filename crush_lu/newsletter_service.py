"""
Newsletter send service for Crush.lu.

Handles audience resolution, rate-limited sending via Microsoft Graph API,
and per-recipient tracking for resumability.
"""
import logging
import time

from django.contrib.auth.models import User
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.html import strip_tags

from azureproject.email_utils import send_domain_email
from .email_helpers import can_send_email
from .models.newsletter import Newsletter, NewsletterRecipient
from .utils.i18n import build_absolute_url, get_user_preferred_language

logger = logging.getLogger(__name__)

# Rate limiting: 25 emails per batch, 62s pause (Graph API limit is 30/min)
BATCH_SIZE = 25
BATCH_PAUSE_SECONDS = 62


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

    # Base queryset depends on audience
    if newsletter.audience == 'all_users':
        users = User.objects.filter(is_active=True)
    elif newsletter.audience == 'all_profiles':
        users = User.objects.filter(is_active=True, crushprofile__isnull=False)
    elif newsletter.audience == 'approved_profiles':
        users = User.objects.filter(is_active=True, crushprofile__is_approved=True)
    elif newsletter.audience == 'segment':
        users = _get_segment_users(newsletter.segment_key)
    else:
        logger.error(f"Unknown audience type: {newsletter.audience}")
        return User.objects.none()

    # Exclude users who opted out of newsletters
    opted_out_user_ids = EmailPreference.objects.filter(
        Q(email_newsletter=False) | Q(unsubscribed_all=True)
    ).values_list('user_id', flat=True)
    users = users.exclude(id__in=opted_out_user_ids)

    # Exclude users already processed for this newsletter (resumability)
    already_processed_ids = NewsletterRecipient.objects.filter(
        newsletter=newsletter,
        status__in=['sent', 'skipped'],
    ).values_list('user_id', flat=True)
    users = users.exclude(id__in=already_processed_ids)

    return users.distinct()


def _get_segment_users(segment_key):
    """
    Resolve a segment key to a User queryset.

    Uses get_segment_definitions() from user_segments.py to find the queryset,
    then maps profile/activity querysets to User objects.
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

    logger.error(f"Segment key not found: {segment_key}")
    return User.objects.none()


def send_newsletter(newsletter, dry_run=False, limit=None, stdout=None):
    """
    Send a newsletter to its audience with rate limiting and resumability.

    Args:
        newsletter: Newsletter instance (must be 'draft' or 'sending')
        dry_run: If True, preview recipients without sending
        limit: Maximum number of emails to send (None = no limit)
        stdout: Optional output stream for progress (management command)

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

    for i, user_id in enumerate(user_ids):
        # Rate limiting: pause between batches
        if batch_count > 0 and batch_count % BATCH_SIZE == 0:
            log(f"  Batch pause ({BATCH_PAUSE_SECONDS}s) after {batch_count} emails...")
            time.sleep(BATCH_PAUSE_SECONDS)

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            skipped += 1
            continue

        # Double-check preference (may have changed since queryset evaluation)
        if not can_send_email(user, 'newsletter'):
            NewsletterRecipient.objects.update_or_create(
                newsletter=newsletter,
                user=user,
                defaults={
                    'email': user.email,
                    'status': 'skipped',
                    'error_message': 'User opted out of newsletters',
                },
            )
            skipped += 1
            continue

        try:
            _send_newsletter_to_user(newsletter, user)
            NewsletterRecipient.objects.update_or_create(
                newsletter=newsletter,
                user=user,
                defaults={
                    'email': user.email,
                    'status': 'sent',
                    'sent_at': timezone.now(),
                },
            )
            sent += 1
            batch_count += 1
            if stdout and (sent % 10 == 0):
                log(f"  Sent {sent}/{recipient_count}...")

        except Exception as e:
            error_msg = str(e)[:500]
            NewsletterRecipient.objects.update_or_create(
                newsletter=newsletter,
                user=user,
                defaults={
                    'email': user.email,
                    'status': 'failed',
                    'error_message': error_msg,
                },
            )
            failed += 1
            logger.error(
                f"Failed to send newsletter #{newsletter.pk} to {user.email}: {e}",
                exc_info=True,
            )

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
    newsletter.status = 'sent' if failed == 0 else 'failed'
    newsletter.sent_at = timezone.now()
    newsletter.save(update_fields=[
        'total_sent', 'total_failed', 'total_skipped',
        'status', 'sent_at', 'updated_at',
    ])

    log(f"\nDone! Sent: {sent}, Failed: {failed}, Skipped: {skipped}")
    return {'sent': sent, 'failed': failed, 'skipped': skipped}


def _send_newsletter_to_user(newsletter, user):
    """
    Render and send a newsletter email to a single user.

    Uses the user's preferred language for template rendering and URL generation.
    Sends from love@crush.lu via Graph API.
    """
    from .models import EmailPreference

    lang = get_user_preferred_language(user=user, default='en')

    # Build unsubscribe URL
    email_prefs = EmailPreference.get_or_create_for_user(user)
    unsubscribe_url = build_absolute_url(
        'crush_lu:email_unsubscribe',
        lang=lang,
        kwargs={'token': email_prefs.unsubscribe_token},
    )

    context = {
        'user': user,
        'first_name': user.first_name,
        'body_html': newsletter.body_html,
        'unsubscribe_url': unsubscribe_url,
        'home_url': build_absolute_url('crush_lu:home', lang=lang),
        'about_url': build_absolute_url('crush_lu:about', lang=lang),
        'events_url': build_absolute_url('crush_lu:event_list', lang=lang),
        'settings_url': build_absolute_url('crush_lu:account_settings', lang=lang),
        'LANGUAGE_CODE': lang,
    }

    with translation.override(lang):
        html_message = render_to_string(
            'crush_lu/emails/newsletter.html', context
        )

    # Plain text fallback
    if newsletter.body_text:
        plain_message = newsletter.body_text
    else:
        plain_message = strip_tags(html_message)

    send_domain_email(
        subject=newsletter.subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[user.email],
        from_email='love@crush.lu',
        domain='crush.lu',
        fail_silently=False,
    )
