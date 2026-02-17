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
from .email_helpers import get_user_language_url, get_email_base_urls, get_social_links
from .utils.i18n import build_absolute_url, get_user_preferred_language

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
            # Use user's preferred language for URLs
            lang = get_user_preferred_language(user=user, default='en')
            event_url = build_absolute_url(
                'crush_lu:event_detail', lang=lang, kwargs={'event_id': event.id}
            )
            dashboard_url = build_absolute_url('crush_lu:dashboard', lang=lang)
            base_urls = {}

        # Get user's preferred language
        from django.utils import translation
        from django.utils.translation import gettext as _

        lang = get_user_preferred_language(user=user, default='en')

        # Prepare context for email template
        context = {
            'user': user,
            'event': event,
            'event_url': event_url,
            'dashboard_url': dashboard_url,
            'LANGUAGE_CODE': lang,
            'social_links': get_social_links(),
            **base_urls,
        }

        # Render email in user's preferred language
        with translation.override(lang):
            subject = _("You're Invited to {title}!").format(title=event.title)
            html_message = render_to_string(
                'crush_lu/emails/existing_user_invitation.html',
                context
            )
            plain_message = strip_tags(html_message)

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
            # Use English for external guests (they can change language on the page)
            invitation_url = build_absolute_url(
                'crush_lu:invitation_landing',
                lang='en',
                kwargs={'code': event_invitation.invitation_code}
            )

        # External guests use English by default (they can change language on the page)
        from django.utils import translation
        from django.utils.translation import gettext as _

        lang = 'en'

        # Prepare context for email template
        context = {
            'invitation': event_invitation,
            'guest_first_name': event_invitation.guest_first_name,
            'event': event_invitation.event,
            'invitation_url': invitation_url,
            'invited_by': event_invitation.invited_by,
            'LANGUAGE_CODE': lang,
            'social_links': get_social_links(),
        }

        # Render email in English for external guests
        with translation.override(lang):
            subject = _("You're Invited to {title} on Crush.lu").format(title=event_invitation.event.title)
            html_message = render_to_string(
                'crush_lu/emails/external_guest_invitation.html',
                context
            )
            plain_message = strip_tags(html_message)

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
            # Use user's preferred language for URLs
            lang = get_user_preferred_language(user=user, default='en')
            event_url = build_absolute_url(
                'crush_lu:event_detail', lang=lang, kwargs={'event_id': event.id}
            )
            dashboard_url = build_absolute_url('crush_lu:dashboard', lang=lang)
            base_urls = {}

        # Get user's preferred language
        from django.utils import translation
        from django.utils.translation import gettext as _

        lang = get_user_preferred_language(user=user, default='en')

        # Prepare context for email template
        context = {
            'invitation': event_invitation,
            'user': user,
            'event': event,
            'event_url': event_url,
            'dashboard_url': dashboard_url,
            'LANGUAGE_CODE': lang,
            'social_links': get_social_links(),
            **base_urls,
        }

        # Render email in user's preferred language
        with translation.override(lang):
            subject = _("Your Invitation to {title} Has Been Approved!").format(title=event.title)
            html_message = render_to_string(
                'crush_lu/emails/invitation_approved.html',
                context
            )
            plain_message = strip_tags(html_message)

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
            # Use user's preferred language for URLs
            lang = get_user_preferred_language(user=user, default='en')
            events_url = build_absolute_url('crush_lu:event_list', lang=lang)
            base_urls = {}

        # Get user's preferred language
        from django.utils import translation
        from django.utils.translation import gettext as _

        lang = get_user_preferred_language(user=user, default='en')

        # Prepare context for email template
        context = {
            'invitation': event_invitation,
            'user': user,
            'event': event,
            'feedback': event_invitation.approval_notes or _("No specific feedback provided."),
            'events_url': events_url,  # Used by template for "Browse Other Events" button
            'LANGUAGE_CODE': lang,
            'social_links': get_social_links(),
            **base_urls,
        }

        # Render email in user's preferred language
        with translation.override(lang):
            subject = _("Update on Your Invitation to {title}").format(title=event.title)
            html_message = render_to_string(
                'crush_lu/emails/invitation_rejected.html',
                context
            )
            plain_message = strip_tags(html_message)

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
