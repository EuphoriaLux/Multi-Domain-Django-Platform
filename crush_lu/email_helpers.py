# crush_lu/email_helpers.py
"""
Email helper functions specific to Crush.lu platform.
Handles profile submissions, coach notifications, event registrations, etc.
"""
import logging
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.urls import reverse
from django.utils.translation import override
from azureproject.email_utils import send_domain_email, get_domain_from_email

logger = logging.getLogger(__name__)


def get_user_language_url(user, url_name, request, **kwargs):
    """
    Get a language-prefixed URL for a user's preferred language.

    Uses override() context manager to ensure thread-safety in production
    (Gunicorn workers handle multiple requests per thread).

    Args:
        user: Django User object with crushprofile
        url_name: The URL name to reverse (e.g., 'crush_lu:dashboard')
        request: Django request object for building absolute URL
        **kwargs: Additional arguments for reverse()

    Returns:
        str: Full URL with language prefix (e.g., 'https://crush.lu/de/events/')
    """
    # Get user's preferred language
    lang = 'en'  # Default
    if user and hasattr(user, 'crushprofile') and user.crushprofile:
        profile_lang = getattr(user.crushprofile, 'preferred_language', None)
        if profile_lang and profile_lang in ['en', 'de', 'fr']:
            lang = profile_lang
        elif profile_lang:
            logger.warning(f"User {user.id} has invalid language: {profile_lang}, using 'en'")

    # Use override() context manager for thread-safety
    # This ensures language state is reset after the block
    with override(lang):
        url_path = reverse(url_name, **kwargs)

    # Build absolute URL
    protocol = 'https' if request.is_secure() else 'http'
    domain = request.get_host()
    return f"{protocol}://{domain}{url_path}"


def get_unsubscribe_url(user, request):
    """
    Generate the unsubscribe URL for a user.

    Args:
        user: User object
        request: Django request object for domain detection

    Returns:
        str: Full unsubscribe URL with token, or None if user has no email preferences
    """
    try:
        from .models import EmailPreference
        email_prefs = EmailPreference.get_or_create_for_user(user)

        protocol = 'https' if request.is_secure() else 'http'
        domain = request.get_host()
        return f"{protocol}://{domain}/unsubscribe/{email_prefs.unsubscribe_token}/"
    except Exception as e:
        logger.warning(f"Could not generate unsubscribe URL for user {user.id}: {e}")
        return None


def get_email_context_with_unsubscribe(user, request, **extra_context):
    """
    Create email context with unsubscribe URL.

    Args:
        user: User object
        request: Django request object
        **extra_context: Additional context to include

    Returns:
        dict: Context dictionary with unsubscribe_url and all extra context
    """
    context = {
        'unsubscribe_url': get_unsubscribe_url(user, request),
        **extra_context
    }
    return context


def can_send_email(user, email_type):
    """
    Check if we can send a specific type of email to a user.

    Args:
        user: User object
        email_type: Type of email (profile_updates, event_reminders, new_connections, new_messages, marketing)

    Returns:
        bool: True if we can send, False if user has unsubscribed
    """
    try:
        from .models import EmailPreference
        email_prefs = EmailPreference.get_or_create_for_user(user)
        return email_prefs.can_send(email_type)
    except Exception as e:
        logger.warning(f"Could not check email preferences for user {user.id}: {e}")
        # Default to sending if we can't check preferences
        return True


def send_welcome_email(user, request):
    """
    Send welcome email immediately after account creation.
    Guides user to complete their profile.

    Args:
        user: User object
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent (1 on success, 0 on failure)
    """
    subject = "Welcome to Crush.lu! 🎉 Complete Your Profile"

    # Build profile URL
    protocol = 'https' if request.is_secure() else 'http'
    domain = request.get_host()
    profile_url = f"{protocol}://{domain}/create-profile/"

    context = get_email_context_with_unsubscribe(user, request,
        first_name=user.first_name,
        profile_url=profile_url,
    )

    html_message = render_to_string('crush_lu/emails/welcome.html', context)
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[user.email],
        request=request,
        fail_silently=False,
    )


def send_profile_submission_confirmation(user, request):
    """
    Send confirmation email to user after FULL profile submission (Step 4).

    Args:
        user: User object
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent (1 on success, 0 on failure)
    """
    # Check email preferences
    if not can_send_email(user, 'profile_updates'):
        logger.info(f"Skipping profile submission email to {user.email} - user unsubscribed")
        return 0

    subject = "Profile Submitted for Review - Crush.lu"

    # Build language-prefixed URLs
    events_url = get_user_language_url(user, 'crush_lu:event_list', request)
    how_it_works_url = get_user_language_url(user, 'crush_lu:how_it_works', request)

    context = get_email_context_with_unsubscribe(user, request,
        first_name=user.first_name,
        events_url=events_url,
        how_it_works_url=how_it_works_url,
    )

    html_message = render_to_string('crush_lu/emails/profile_submission_confirmation.html', context)
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[user.email],
        request=request,
        fail_silently=False,
    )


def send_coach_assignment_notification(coach, profile_submission, request):
    """
    Notify coach about new profile assignment.

    Args:
        coach: CrushCoach object
        profile_submission: ProfileSubmission object
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent
    """
    subject = f"New Profile Review Assignment - {profile_submission.profile.user.get_full_name()}"

    # Build language-prefixed URLs (coach's preferred language)
    review_url = get_user_language_url(
        coach.user, 'crush_lu:coach_review_profile', request,
        kwargs={'submission_id': profile_submission.id}
    )

    html_message = render_to_string('crush_lu/emails/coach_assignment.html', {
        'coach': coach,
        'submission': profile_submission,
        'profile': profile_submission.profile,
        'review_url': review_url,
    })
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[coach.user.email],
        request=request,
        fail_silently=False,
    )


def send_profile_approved_notification(profile, request, coach_notes=None):
    """
    Notify user that their profile has been approved.

    Args:
        profile: CrushProfile object
        request: Django request object for domain detection
        coach_notes: Optional feedback from coach

    Returns:
        int: Number of emails sent
    """
    # Check email preferences
    if not can_send_email(profile.user, 'profile_updates'):
        logger.info(f"Skipping profile approved email to {profile.user.email} - user unsubscribed")
        return 0

    subject = "Welcome to Crush.lu - Your Profile is Approved! 🎉"

    # Build language-prefixed URLs
    events_url = get_user_language_url(profile.user, 'crush_lu:event_list', request)

    context = get_email_context_with_unsubscribe(profile.user, request,
        first_name=profile.user.first_name,
        coach_notes=coach_notes,
        events_url=events_url,
    )

    html_message = render_to_string('crush_lu/emails/profile_approved.html', context)
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[profile.user.email],
        request=request,
        fail_silently=False,
    )


def send_profile_revision_request(profile, request, feedback):
    """
    Notify user that their profile needs revisions.

    Args:
        profile: CrushProfile object
        request: Django request object for domain detection
        feedback: Feedback message from coach

    Returns:
        int: Number of emails sent
    """
    subject = "Profile Review Feedback - Crush.lu"

    # Build language-prefixed URLs
    edit_profile_url = get_user_language_url(profile.user, 'crush_lu:edit_profile', request)

    html_message = render_to_string('crush_lu/emails/profile_revision_request.html', {
        'user': profile.user,
        'first_name': profile.user.first_name,
        'feedback': feedback,
        'edit_profile_url': edit_profile_url,
    })
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[profile.user.email],
        request=request,
        fail_silently=False,
    )


def send_profile_rejected_notification(profile, request, reason):
    """
    Notify user that their profile has been rejected.

    Args:
        profile: CrushProfile object
        request: Django request object for domain detection
        reason: Rejection reason from coach

    Returns:
        int: Number of emails sent
    """
    subject = "Profile Review Update - Crush.lu"

    html_message = render_to_string('crush_lu/emails/profile_rejected.html', {
        'user': profile.user,
        'first_name': profile.user.first_name,
        'reason': reason,
    })
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[profile.user.email],
        request=request,
        fail_silently=False,
    )


def send_event_registration_confirmation(registration, request):
    """
    Send confirmation email for event registration.

    Args:
        registration: EventRegistration object
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent
    """
    # Check email preferences
    if not can_send_email(registration.user, 'event_reminders'):
        logger.info(f"Skipping event registration email to {registration.user.email} - user unsubscribed")
        return 0

    subject = f"Event Registration Confirmed - {registration.event.title}"

    # Build language-prefixed URLs
    event_url = get_user_language_url(
        registration.user, 'crush_lu:event_detail', request,
        kwargs={'event_id': registration.event.id}
    )
    cancel_url = get_user_language_url(
        registration.user, 'crush_lu:event_cancel', request,
        kwargs={'event_id': registration.event.id}
    )

    context = get_email_context_with_unsubscribe(registration.user, request,
        registration=registration,
        event=registration.event,
        event_url=event_url,
        cancel_url=cancel_url,
    )

    html_message = render_to_string('crush_lu/emails/event_registration_confirmation.html', context)
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[registration.user.email],
        request=request,
        fail_silently=False,
    )


def send_event_waitlist_notification(registration, request):
    """
    Notify user they've been added to event waitlist.

    Args:
        registration: EventRegistration object
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent
    """
    subject = f"Added to Waitlist - {registration.event.title}"

    # Build language-prefixed URLs
    events_url = get_user_language_url(registration.user, 'crush_lu:event_list', request)

    html_message = render_to_string('crush_lu/emails/event_waitlist.html', {
        'user': registration.user,
        'registration': registration,
        'event': registration.event,
        'events_url': events_url,
    })
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[registration.user.email],
        request=request,
        fail_silently=False,
    )


def send_event_cancellation_confirmation(user, event, request):
    """
    Send confirmation email for event cancellation.

    Args:
        user: User object
        event: MeetupEvent object
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent
    """
    subject = f"Event Cancellation Confirmed - {event.title}"

    # Build language-prefixed URLs
    events_url = get_user_language_url(user, 'crush_lu:event_list', request)

    html_message = render_to_string('crush_lu/emails/event_cancellation.html', {
        'user': user,
        'event': event,
        'events_url': events_url,
    })
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[user.email],
        request=request,
        fail_silently=False,
    )


def send_event_reminder(registration, request, days_until_event):
    """
    Send event reminder email.

    Args:
        registration: EventRegistration object
        request: Django request object for domain detection
        days_until_event: Number of days until event

    Returns:
        int: Number of emails sent
    """
    # Check email preferences
    if not can_send_email(registration.user, 'event_reminders'):
        logger.info(f"Skipping event reminder email to {registration.user.email} - user unsubscribed")
        return 0

    subject = f"Event Reminder - {registration.event.title} in {days_until_event} days"

    # Build language-prefixed URLs
    event_url = get_user_language_url(
        registration.user, 'crush_lu:event_detail', request,
        kwargs={'event_id': registration.event.id}
    )
    cancel_url = get_user_language_url(
        registration.user, 'crush_lu:event_cancel', request,
        kwargs={'event_id': registration.event.id}
    )

    context = get_email_context_with_unsubscribe(registration.user, request,
        registration=registration,
        event=registration.event,
        days_until_event=days_until_event,
        event_url=event_url,
        cancel_url=cancel_url,
    )

    html_message = render_to_string('crush_lu/emails/event_reminder.html', context)
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[registration.user.email],
        request=request,
        fail_silently=False,
    )


def send_profile_submission_notifications(submission, request, add_message_func=None):
    """
    Send all notifications for a new profile submission.

    This is a convenience function that handles:
    1. Sending confirmation email to the user
    2. Sending assignment notification to the coach (if assigned)

    Used by both create_profile and edit_profile views to avoid code duplication.

    Args:
        submission: ProfileSubmission object
        request: Django request object
        add_message_func: Optional function to add messages (e.g., messages.warning)
                         Called with (message_text) if email fails

    Returns:
        dict: {'user_email_sent': bool, 'coach_email_sent': bool}
    """
    result = {'user_email_sent': False, 'coach_email_sent': False}
    user = submission.profile.user

    # Send confirmation email to user
    try:
        email_result = send_profile_submission_confirmation(user, request)
        result['user_email_sent'] = email_result > 0
        logger.info(f"✅ Profile submission email sent to {user.email}: {email_result}")
    except Exception as e:
        logger.error(f"❌ Failed to send profile submission confirmation to {user.email}: {e}", exc_info=True)
        if add_message_func:
            add_message_func('Profile submitted! (Email notification may have failed - check your spam folder)')

    # Send notification to assigned coach if one was assigned
    if submission.coach:
        try:
            email_result = send_coach_assignment_notification(submission.coach, submission, request)
            result['coach_email_sent'] = email_result > 0
            logger.info(f"✅ Coach assignment email sent to {submission.coach.user.email}: {email_result}")
        except Exception as e:
            logger.error(f"❌ Failed to send coach assignment notification: {e}", exc_info=True)

    return result


def send_new_connection_request_notification(recipient, connection, requester, request):
    """
    Notify user that someone wants to connect with them.

    Args:
        recipient: User object (who receives the connection request)
        connection: EventConnection object
        requester: User object (who sent the request)
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent
    """
    # Check email preferences
    if not can_send_email(recipient, 'new_connections'):
        logger.info(f"Skipping connection request email to {recipient.email} - user unsubscribed")
        return 0

    subject = "Someone wants to connect with you! 💕"

    # Get requester display name
    if hasattr(requester, 'crushprofile'):
        requester_name = requester.crushprofile.display_name
    else:
        requester_name = requester.first_name

    # Get event info
    event = connection.event
    event_title = event.title if event else "a Crush.lu event"
    event_date = event.date_time.strftime('%B %d, %Y') if event and event.date_time else ""

    # Build connections URL with language prefix
    connections_url = get_user_language_url(recipient, 'crush_lu:my_connections', request)

    context = get_email_context_with_unsubscribe(recipient, request,
        first_name=recipient.first_name,
        requester_name=requester_name,
        event_title=event_title,
        event_date=event_date,
        connections_url=connections_url,
    )

    html_message = render_to_string('crush_lu/emails/new_connection_request.html', context)
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[recipient.email],
        request=request,
        fail_silently=False,
    )


def send_connection_accepted_notification(recipient, connection, accepter, request):
    """
    Notify user that their connection request was accepted.

    Args:
        recipient: User object (who sent the original request)
        connection: EventConnection object
        accepter: User object (who accepted the request)
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent
    """
    # Check email preferences
    if not can_send_email(recipient, 'new_connections'):
        logger.info(f"Skipping connection accepted email to {recipient.email} - user unsubscribed")
        return 0

    subject = "Your connection request was accepted! 🎉"

    # Get accepter display name
    if hasattr(accepter, 'crushprofile'):
        accepter_name = accepter.crushprofile.display_name
    else:
        accepter_name = accepter.first_name

    # Get event info
    event = connection.event
    event_title = event.title if event else "a Crush.lu event"

    # Build connection detail URL with language prefix
    connection_url = get_user_language_url(
        recipient, 'crush_lu:connection_detail', request,
        kwargs={'connection_id': connection.id}
    )

    context = get_email_context_with_unsubscribe(recipient, request,
        first_name=recipient.first_name,
        accepter_name=accepter_name,
        event_title=event_title,
        connection_url=connection_url,
    )

    html_message = render_to_string('crush_lu/emails/connection_accepted.html', context)
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[recipient.email],
        request=request,
        fail_silently=False,
    )


def send_new_message_notification(recipient, message, request):
    """
    Notify user that they received a new message.

    Args:
        recipient: User object (who receives the message)
        message: ConnectionMessage object
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent
    """
    # Check email preferences
    if not can_send_email(recipient, 'new_messages'):
        logger.info(f"Skipping new message email to {recipient.email} - user unsubscribed")
        return 0

    # Get sender display name
    sender = message.sender
    if hasattr(sender, 'crushprofile'):
        sender_name = sender.crushprofile.display_name
    else:
        sender_name = sender.first_name

    subject = f"New message from {sender_name} 💬"

    # Truncate message for preview
    message_preview = message.message[:100]
    if len(message.message) > 100:
        message_preview += "..."

    # Build connection URL with language prefix
    connection_url = get_user_language_url(
        recipient, 'crush_lu:connection_detail', request,
        kwargs={'connection_id': message.connection.id}
    )

    context = get_email_context_with_unsubscribe(recipient, request,
        first_name=recipient.first_name,
        sender_name=sender_name,
        message_preview=message_preview,
        connection_url=connection_url,
    )

    html_message = render_to_string('crush_lu/emails/new_message.html', context)
    plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[recipient.email],
        request=request,
        fail_silently=False,
    )
