# crush_lu/email_notifications.py
"""
Email notification utilities for Crush.lu event invitations.
Handles sending emails for:
1. Existing user invitations (invited_users M2M)
2. External guest invitations (EventInvitation creation)
3. Coach approval notifications (EventInvitation approval)
"""
import logging
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.urls import reverse
from django.utils.translation import override
from azureproject.email_utils import send_domain_email
from .email_helpers import get_user_language_url, get_email_base_urls

logger = logging.getLogger(__name__)


def send_existing_user_invitation_email(event, user, request=None):
    """
    Send email to existing user when they're invited to a private event.

    Args:
        event: MeetupEvent instance
        user: User instance (invited user)
        request: Django request object (optional, for domain detection)

    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        # Build i18n-aware URLs
        if request:
            event_url = get_user_language_url(
                user, 'crush_lu:event_detail', request,
                kwargs={'event_id': event.id}
            )
            dashboard_url = get_user_language_url(user, 'crush_lu:dashboard', request)
            base_urls = get_email_base_urls(user, request)
        else:
            # Fallback for when request is not available (e.g., management commands)
            event_url = f'https://crush.lu/en/events/{event.id}/'
            dashboard_url = 'https://crush.lu/en/dashboard/'
            base_urls = {}

        # Prepare context for email template
        context = {
            'user': user,
            'event': event,
            'event_url': event_url,
            'dashboard_url': dashboard_url,
            **base_urls,
        }

        # Render HTML email from template
        html_message = render_to_string(
            'crush_lu/emails/existing_user_invitation.html',
            context
        )

        # Create plain text version
        plain_message = strip_tags(html_message)

        # Send email
        subject = f'🎉 You\'re Invited to {event.title}!'

        result = send_domain_email(
            subject=subject,
            message=plain_message,
            recipient_list=[user.email],
            request=request,
            domain='crush.lu',
            html_message=html_message,
            fail_silently=False,
        )

        logger.info(f"Sent invitation email to existing user {user.email} for event {event.title}")
        return result > 0

    except Exception as e:
        logger.error(f"Failed to send invitation email to {user.email}: {str(e)}")
        return False


def send_external_guest_invitation_email(event_invitation, request=None):
    """
    Send email to external guest with invitation link.

    Args:
        event_invitation: EventInvitation instance
        request: Django request object (optional, for domain detection)

    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        # Build invitation URL
        # Note: For external guests, we use English (default) since they don't have a profile yet
        # The invitation landing page is inside i18n_patterns
        if request:
            protocol = 'https' if request.is_secure() else 'http'
            domain = request.get_host()
            # Use English for external guests (they can change language on the page)
            with override('en'):
                url_path = reverse('crush_lu:invitation_landing', kwargs={'code': event_invitation.invitation_code})
            invitation_url = f"{protocol}://{domain}{url_path}"
        else:
            # Fallback when request is not available
            with override('en'):
                url_path = reverse('crush_lu:invitation_landing', kwargs={'code': event_invitation.invitation_code})
            invitation_url = f"https://crush.lu{url_path}"

        # Prepare context for email template
        context = {
            'invitation': event_invitation,
            'guest_first_name': event_invitation.guest_first_name,
            'event': event_invitation.event,
            'invitation_url': invitation_url,
            'invited_by': event_invitation.invited_by,
        }

        # Render HTML email from template
        html_message = render_to_string(
            'crush_lu/emails/external_guest_invitation.html',
            context
        )

        # Create plain text version
        plain_message = strip_tags(html_message)

        # Send email
        subject = f'💌 You\'re Invited to {event_invitation.event.title} on Crush.lu'

        result = send_domain_email(
            subject=subject,
            message=plain_message,
            recipient_list=[event_invitation.guest_email],
            request=request,
            domain='crush.lu',
            html_message=html_message,
            fail_silently=False,
        )

        logger.info(f"Sent external guest invitation to {event_invitation.guest_email} for event {event_invitation.event.title}")
        return result > 0

    except Exception as e:
        logger.error(f"Failed to send external guest invitation to {event_invitation.guest_email}: {str(e)}")
        return False


def send_invitation_approval_email(event_invitation, request=None):
    """
    Send email to external guest when their invitation is approved by coach.

    Args:
        event_invitation: EventInvitation instance (must have created_user)
        request: Django request object (optional, for domain detection)

    Returns:
        bool: True if email sent successfully, False otherwise
    """
    if not event_invitation.created_user:
        logger.warning(f"Cannot send approval email - no user account created yet for {event_invitation.guest_email}")
        return False

    user = event_invitation.created_user
    event = event_invitation.event

    try:
        # Build i18n-aware URLs
        if request:
            event_url = get_user_language_url(
                user, 'crush_lu:event_detail', request,
                kwargs={'event_id': event.id}
            )
            dashboard_url = get_user_language_url(user, 'crush_lu:dashboard', request)
            base_urls = get_email_base_urls(user, request)
        else:
            # Fallback for when request is not available
            event_url = f'https://crush.lu/en/events/{event.id}/'
            dashboard_url = 'https://crush.lu/en/dashboard/'
            base_urls = {}

        # Prepare context for email template
        context = {
            'invitation': event_invitation,
            'user': user,
            'event': event,
            'event_url': event_url,
            'dashboard_url': dashboard_url,
            **base_urls,
        }

        # Render HTML email from template
        html_message = render_to_string(
            'crush_lu/emails/invitation_approved.html',
            context
        )

        # Create plain text version
        plain_message = strip_tags(html_message)

        # Send email
        subject = f'✅ Your Invitation to {event.title} Has Been Approved!'

        result = send_domain_email(
            subject=subject,
            message=plain_message,
            recipient_list=[user.email],
            request=request,
            domain='crush.lu',
            html_message=html_message,
            fail_silently=False,
        )

        logger.info(f"Sent approval email to {user.email} for event {event.title}")
        return result > 0

    except Exception as e:
        logger.error(f"Failed to send approval email to {user.email}: {str(e)}")
        return False


def send_invitation_rejection_email(event_invitation, request=None):
    """
    Send email to external guest when their invitation is rejected by coach.

    Args:
        event_invitation: EventInvitation instance (must have created_user)
        request: Django request object (optional, for domain detection)

    Returns:
        bool: True if email sent successfully, False otherwise
    """
    if not event_invitation.created_user:
        logger.warning(f"Cannot send rejection email - no user account created yet for {event_invitation.guest_email}")
        return False

    user = event_invitation.created_user
    event = event_invitation.event

    try:
        # Build i18n-aware URLs
        if request:
            events_url = get_user_language_url(user, 'crush_lu:event_list', request)
            base_urls = get_email_base_urls(user, request)
        else:
            # Fallback for when request is not available
            events_url = 'https://crush.lu/en/events/'
            base_urls = {}

        # Prepare context for email template
        context = {
            'invitation': event_invitation,
            'user': user,
            'event': event,
            'feedback': event_invitation.approval_notes or "No specific feedback provided.",
            'events_url': events_url,  # Used by template for "Browse Other Events" button
            **base_urls,
        }

        # Render HTML email from template
        html_message = render_to_string(
            'crush_lu/emails/invitation_rejected.html',
            context
        )

        # Create plain text version
        plain_message = strip_tags(html_message)

        # Send email
        subject = f'Update on Your Invitation to {event.title}'

        result = send_domain_email(
            subject=subject,
            message=plain_message,
            recipient_list=[user.email],
            request=request,
            domain='crush.lu',
            html_message=html_message,
            fail_silently=False,
        )

        logger.info(f"Sent rejection email to {user.email} for event {event.title}")
        return result > 0

    except Exception as e:
        logger.error(f"Failed to send rejection email to {user.email}: {str(e)}")
        return False
