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
from django.core.cache import cache
from azureproject.email_utils import send_domain_email
from .utils.i18n import get_user_preferred_language

logger = logging.getLogger(__name__)


def get_user_language_url(user, url_name, request, **kwargs):
    """
    Get a language-prefixed URL for a user's preferred language.

    Uses override() context manager to ensure thread-safety in production
    (Gunicorn workers handle multiple requests per thread).

    Args:
        user: Django User object with crushprofile
        url_name: The URL name to reverse (e.g., 'crush_lu:dashboard')
        request: Django request object for building absolute URL. May be None
            for batch senders (management commands) — falls back to the
            canonical crush.lu domain via utils.i18n.build_absolute_url.
        **kwargs: Additional arguments for reverse()

    Returns:
        str: Full URL with language prefix (e.g., 'https://crush.lu/de/events/')
    """
    # Get user's preferred language using centralized utility
    lang = get_user_preferred_language(user=user, request=request, default="en")

    # Batch senders (e.g. send_event_recaps) have no request; build against
    # the canonical domain instead of crashing on request.is_secure().
    if request is None:
        from .utils.i18n import build_absolute_url

        return build_absolute_url(url_name, lang=lang, **kwargs)

    # Use override() context manager for thread-safety
    # This ensures language state is reset after the block
    with override(lang):
        url_path = reverse(url_name, **kwargs)

    # Build absolute URL
    protocol = "https" if request.is_secure() else "http"
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

        # Use i18n-aware URL generation (unsubscribe is inside i18n_patterns)
        return get_user_language_url(
            user,
            "crush_lu:email_unsubscribe",
            request,
            kwargs={"token": email_prefs.unsubscribe_token},
        )
    except Exception as e:
        logger.warning(f"Could not generate unsubscribe URL for user {user.id}: {e}")
        return None


def get_email_base_urls(user, request):
    """
    Generate common footer URLs for email templates.

    These are the standard links that appear in the footer of all emails:
    - Home, About, Events, Account Settings

    Args:
        user: User object with crushprofile
        request: Django request object for domain detection

    Returns:
        dict: Dictionary of URL names to full URLs with language prefix
    """
    return {
        "home_url": get_user_language_url(user, "crush_lu:home", request),
        "about_url": get_user_language_url(user, "crush_lu:about", request),
        "events_url": get_user_language_url(user, "crush_lu:event_list", request),
        "settings_url": get_user_language_url(
            user, "crush_lu:account_settings", request
        ),
    }


def get_social_links():
    """Get social media links from CrushSiteConfig for email templates."""
    from .models import CrushSiteConfig

    try:
        config = CrushSiteConfig.get_config()
        platforms = [
            ("Instagram", config.social_instagram_url, "instagram"),
            ("Facebook", config.social_facebook_url, "facebook"),
            ("LinkedIn", config.social_linkedin_url, "linkedin"),
            ("Google Business", config.social_google_business_url, "google"),
            ("Reddit", config.social_reddit_url, "reddit"),
        ]
        return [{"name": n, "url": u, "icon_id": i} for n, u, i in platforms if u]
    except Exception:
        return []


def get_email_context_with_unsubscribe(user, request, **extra_context):
    """
    Create email context with unsubscribe URL and footer links.

    Includes common footer URLs (home, about, events, settings) that appear
    in the base email template. These are generated with proper language
    prefixes based on the user's preferred language.

    Args:
        user: User object
        request: Django request object
        **extra_context: Additional context to include

    Returns:
        dict: Context dictionary with unsubscribe_url, footer URLs, LANGUAGE_CODE, and all extra context
    """
    # Get base footer URLs with proper language prefix
    base_urls = get_email_base_urls(user, request)

    # Get user's preferred language for email templates
    lang = get_user_preferred_language(user=user, request=request, default="en")

    context = {
        "user": user,  # Include user object for templates that need it
        "unsubscribe_url": get_unsubscribe_url(user, request),
        "LANGUAGE_CODE": lang,  # For email templates that need language-aware rendering
        "social_links": get_social_links(),
        **base_urls,  # home_url, about_url, events_url, settings_url
        **extra_context,
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Get user's preferred language
    lang = get_user_preferred_language(user=user, request=request, default="en")

    # Build journey entry URL with language prefix. Points at /onboarding/
    # (smart-resume) so the CTA lands users on their current step — a fresh
    # user on /welcome/, a returning user further along. Linking directly to
    # /create-profile/ would skip the journey entirely.
    profile_url = get_user_language_url(user, "crush_lu:onboarding_entry", request)

    # Build how it works URL with language prefix
    how_it_works_url = get_user_language_url(user, "crush_lu:how_it_works", request)

    context = get_email_context_with_unsubscribe(
        user,
        request,
        first_name=user.first_name,
        profile_url=profile_url,
        how_it_works_url=how_it_works_url,
    )

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Welcome to Crush.lu! Complete Your Profile")
        html_message = render_to_string("crush_lu/emails/welcome.html", context)
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Check email preferences
    if not can_send_email(user, "profile_updates"):
        logger.info(
            f"Skipping profile submission email to {user.email} - user unsubscribed"
        )
        return 0

    # Get user's preferred language
    lang = get_user_preferred_language(user=user, request=request, default="en")

    # Build language-prefixed URLs
    events_url = get_user_language_url(user, "crush_lu:event_list", request)
    how_it_works_url = get_user_language_url(user, "crush_lu:how_it_works", request)

    # Intent probe: captured on /welcome/ (step 1 of the onboarding journey).
    # Templates branch on this to match the tone the user chose at the outset —
    # an "events-first" user gets a different teaser than a "curious" one.
    intent_probe = ""
    try:
        intent_probe = user.crushprofile.intent_probe or ""
    except Exception:
        pass

    context = get_email_context_with_unsubscribe(
        user,
        request,
        first_name=user.first_name,
        events_url=events_url,
        how_it_works_url=how_it_works_url,
        intent_probe=intent_probe,
    )

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Profile Submitted for Review - Crush.lu")
        html_message = render_to_string(
            "crush_lu/emails/profile_submission_confirmation.html", context
        )
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Get coach's preferred language
    lang = get_user_preferred_language(user=coach.user, request=request, default="en")

    # Build language-prefixed URLs (coach's preferred language)
    review_url = get_user_language_url(
        coach.user,
        "crush_lu:coach_review_profile",
        request,
        kwargs={"submission_id": profile_submission.id},
    )

    # Get base footer URLs for coach
    base_urls = get_email_base_urls(coach.user, request)

    context = {
        "coach": coach,
        "submission": profile_submission,
        "profile": profile_submission.profile,
        "review_url": review_url,
        "LANGUAGE_CODE": lang,
        "social_links": get_social_links(),
        **base_urls,
    }

    # Render email in coach's preferred language
    with translation.override(lang):
        user_name = profile_submission.profile.user.get_full_name()
        subject = _("New Profile Review Assignment - {name}").format(name=user_name)
        html_message = render_to_string(
            "crush_lu/emails/coach_assignment.html", context
        )
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Check email preferences
    if not can_send_email(profile.user, "profile_updates"):
        logger.info(
            f"Skipping profile approved email to {profile.user.email} - user unsubscribed"
        )
        return 0

    # Get user's preferred language
    lang = get_user_preferred_language(user=profile.user, request=request, default="en")

    # Build language-prefixed URLs
    events_url = get_user_language_url(profile.user, "crush_lu:event_list", request)

    context = get_email_context_with_unsubscribe(
        profile.user,
        request,
        first_name=profile.user.first_name,
        coach_notes=coach_notes,
        events_url=events_url,
    )

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Welcome to Crush.lu - Your Profile is Approved!")
        html_message = render_to_string(
            "crush_lu/emails/profile_approved.html", context
        )
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Get user's preferred language
    lang = get_user_preferred_language(user=profile.user, request=request, default="en")

    # Build language-prefixed URLs
    edit_profile_url = get_user_language_url(
        profile.user, "crush_lu:edit_profile", request
    )

    # Get base footer URLs
    base_urls = get_email_base_urls(profile.user, request)

    context = {
        "user": profile.user,
        "first_name": profile.user.first_name,
        "feedback": feedback,
        "edit_profile_url": edit_profile_url,
        "LANGUAGE_CODE": lang,
        "social_links": get_social_links(),
        **base_urls,
    }

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Profile Review Feedback - Crush.lu")
        html_message = render_to_string(
            "crush_lu/emails/profile_revision_request.html", context
        )
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Get user's preferred language
    lang = get_user_preferred_language(user=profile.user, request=request, default="en")

    # Get base footer URLs
    base_urls = get_email_base_urls(profile.user, request)

    context = {
        "user": profile.user,
        "first_name": profile.user.first_name,
        "reason": reason,
        "LANGUAGE_CODE": lang,
        "social_links": get_social_links(),
        **base_urls,
    }

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Profile Not Approved - Crush.lu")
        html_message = render_to_string(
            "crush_lu/emails/profile_rejected.html", context
        )
        plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[profile.user.email],
        request=request,
        fail_silently=False,
    )


def send_profile_recontact_notification(profile, coach, request):
    """
    Notify user that their coach needs them to recontact for screening call.

    Args:
        profile: CrushProfile object
        coach: CrushCoach object
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent
    """
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Get user's preferred language
    lang = get_user_preferred_language(user=profile.user, request=request, default="en")

    # Get base footer URLs
    base_urls = get_email_base_urls(profile.user, request)

    # Determine coach contact phone: per-coach or global fallback
    if coach.phone_number:
        coach_contact_phone = coach.whatsapp_number
        coach_phone_available = True
    else:
        from .models import CrushSiteConfig

        try:
            config = CrushSiteConfig.get_config()
            coach_contact_phone = config.whatsapp_number
            coach_phone_available = config.whatsapp_enabled and bool(
                coach_contact_phone
            )
        except Exception:
            coach_contact_phone = ""
            coach_phone_available = False

    context = {
        "user": profile.user,
        "first_name": profile.user.first_name,
        "coach": coach,
        "coach_contact_phone": coach_contact_phone,
        "coach_phone_available": coach_phone_available,
        "LANGUAGE_CODE": lang,
        "social_links": get_social_links(),
        **base_urls,
    }

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Your Crush Coach Needs to Speak With You")
        html_message = render_to_string(
            "crush_lu/emails/profile_recontact.html", context
        )
        plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[profile.user.email],
        request=request,
        fail_silently=False,
    )


def send_event_registration_confirmation(registration, request=None):
    """
    Send confirmation email for event registration.

    Args:
        registration: EventRegistration object
        request: Optional Django request object for domain detection.
                 If None, uses build_absolute_url() for URL generation
                 (e.g. when called from signal handlers).

    Returns:
        int: Number of emails sent
    """
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Check email preferences
    if not can_send_email(registration.user, "event_reminders"):
        logger.info(
            f"Skipping event registration email to {registration.user.email} - user unsubscribed"
        )
        return 0

    # Get user's preferred language
    lang = get_user_preferred_language(
        user=registration.user, request=request, default="en"
    )

    if request:
        # Build language-prefixed URLs using request context
        event_url = get_user_language_url(
            registration.user,
            "crush_lu:event_detail",
            request,
            kwargs={"event_id": registration.event.id},
        )
        cancel_url = get_user_language_url(
            registration.user,
            "crush_lu:event_cancel",
            request,
            kwargs={"event_id": registration.event.id},
        )

        context = get_email_context_with_unsubscribe(
            registration.user,
            request,
            registration=registration,
            event=registration.event,
            event_url=event_url,
            cancel_url=cancel_url,
        )
    else:
        # No request context — build URLs without request (batch/signal usage)
        from .utils.i18n import build_absolute_url
        from .models import EmailPreference

        event_url = build_absolute_url(
            "crush_lu:event_detail",
            lang=lang,
            kwargs={"event_id": registration.event.id},
        )
        cancel_url = build_absolute_url(
            "crush_lu:event_cancel",
            lang=lang,
            kwargs={"event_id": registration.event.id},
        )

        email_prefs = EmailPreference.get_or_create_for_user(registration.user)
        unsubscribe_url = build_absolute_url(
            "crush_lu:email_unsubscribe",
            lang=lang,
            kwargs={"token": email_prefs.unsubscribe_token},
        )

        context = {
            "user": registration.user,
            "registration": registration,
            "event": registration.event,
            "event_url": event_url,
            "cancel_url": cancel_url,
            "unsubscribe_url": unsubscribe_url,
            "home_url": build_absolute_url("crush_lu:home", lang=lang),
            "about_url": build_absolute_url("crush_lu:about", lang=lang),
            "events_url": build_absolute_url("crush_lu:event_list", lang=lang),
            "settings_url": build_absolute_url("crush_lu:account_settings", lang=lang),
            "social_links": get_social_links(),
            "LANGUAGE_CODE": lang,
        }

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Event Registration Confirmed - {title}").format(
            title=registration.event.title
        )
        html_message = render_to_string(
            "crush_lu/emails/event_registration_confirmation.html", context
        )
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Get user's preferred language
    lang = get_user_preferred_language(
        user=registration.user, request=request, default="en"
    )

    # Build language-prefixed URLs
    events_url = get_user_language_url(
        registration.user, "crush_lu:event_list", request
    )

    # Get base footer URLs
    base_urls = get_email_base_urls(registration.user, request)

    # Calculate actual waitlist position (queue order by registration time)
    from crush_lu.models import EventRegistration

    waitlist_position = (
        EventRegistration.objects.filter(
            event=registration.event,
            status="waitlist",
            registered_at__lt=registration.registered_at,
        ).count()
        + 1
    )

    # Use display_name for privacy (Luxembourg community)
    profile = getattr(registration.user, "crushprofile", None)
    display_name = profile.display_name if profile else registration.user.first_name

    context = {
        "user": registration.user,
        "registration": registration,
        "event": registration.event,
        "display_name": display_name,
        "waitlist_position": waitlist_position,
        "events_url": events_url,
        "LANGUAGE_CODE": lang,
        "social_links": get_social_links(),
        **base_urls,
    }

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Added to Waitlist - {title}").format(
            title=registration.event.title
        )
        html_message = render_to_string("crush_lu/emails/event_waitlist.html", context)
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Get user's preferred language
    lang = get_user_preferred_language(user=user, request=request, default="en")

    # Build language-prefixed URLs
    events_url = get_user_language_url(user, "crush_lu:event_list", request)

    # Get base footer URLs
    base_urls = get_email_base_urls(user, request)

    context = {
        "user": user,
        "event": event,
        "events_url": events_url,
        "LANGUAGE_CODE": lang,
        "social_links": get_social_links(),
        **base_urls,
    }

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Event Cancellation Confirmed - {title}").format(title=event.title)
        html_message = render_to_string(
            "crush_lu/emails/event_cancellation.html", context
        )
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
    from django.utils import translation
    from django.utils.translation import gettext as _, ngettext

    # Check email preferences
    if not can_send_email(registration.user, "event_reminders"):
        logger.info(
            f"Skipping event reminder email to {registration.user.email} - user unsubscribed"
        )
        return 0

    # Get user's preferred language
    lang = get_user_preferred_language(
        user=registration.user, request=request, default="en"
    )

    # Build language-prefixed URLs
    event_url = get_user_language_url(
        registration.user,
        "crush_lu:event_detail",
        request,
        kwargs={"event_id": registration.event.id},
    )
    cancel_url = get_user_language_url(
        registration.user,
        "crush_lu:event_cancel",
        request,
        kwargs={"event_id": registration.event.id},
    )

    context = get_email_context_with_unsubscribe(
        registration.user,
        request,
        registration=registration,
        event=registration.event,
        days_until_event=days_until_event,
        event_url=event_url,
        cancel_url=cancel_url,
    )

    # Render email in user's preferred language
    with translation.override(lang):
        days_text = ngettext("%(days)d day", "%(days)d days", days_until_event) % {
            "days": days_until_event
        }
        subject = _("Event Reminder - {title} in {days}").format(
            title=registration.event.title, days=days_text
        )
        html_message = render_to_string("crush_lu/emails/event_reminder.html", context)
        plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[registration.user.email],
        request=request,
        fail_silently=False,
    )


def send_event_recap(registration, request=None):
    """
    Send a 24h post-event recap email to an attendee.

    Highlights mutual matches and outstanding incoming connection requests,
    and nudges the user back to the attendees page before the connection
    deadline. Idempotent via EventRegistration.recap_sent_at on the caller side.

    Args:
        registration: EventRegistration with status='attended'
        request: Optional Django request

    Returns:
        int: Number of emails sent (0 if skipped due to opt-out)
    """
    from django.utils import translation
    from django.utils.translation import gettext as _
    from .models import EventConnection

    if not can_send_email(registration.user, "event_reminders"):
        logger.info(
            f"Skipping event recap email to {registration.user.email} - user unsubscribed"
        )
        return 0

    user = registration.user
    event = registration.event

    # Compute counts. Use annotate_is_mutual to avoid N+1.
    outgoing_qs = EventConnection.objects.filter(event=event, requester=user).exclude(
        status="declined"
    )
    outgoing_qs = outgoing_qs.annotate_is_mutual()
    outgoing_count = outgoing_qs.count()
    mutual_match_count = sum(1 for c in outgoing_qs if c.is_mutual_annotated)

    incoming_count = EventConnection.objects.filter(
        event=event, recipient=user, status="pending"
    ).count()

    has_action = mutual_match_count > 0 or incoming_count > 0

    lang = get_user_preferred_language(user=user, request=request, default="en")

    attendees_url = get_user_language_url(
        user,
        "crush_lu:event_attendees",
        request,
        kwargs={"event_id": event.id},
    )
    events_url = get_user_language_url(
        user,
        "crush_lu:my_events",
        request,
    )

    # Event Lobby branch (decision 2026-07-18): a lobby participant gets the
    # "confirm who you met" CTA while the 48h recap is open; a checked-in,
    # LuxID-capable non-member (§5.3's may-learn guest) gets a "Finish Crush
    # Connect" nudge for next time. Everyone keeps the attendees link —
    # named connections stay open to all attendees.
    from .services.event_lobby import (
        GATE_NO_MEMBERSHIP,
        GATE_NOT_ONBOARDED,
        PHASE_RECAP,
        event_lobby_phase,
        lobby_feature_enabled,
        may_learn_lobby_exists,
        participant_gate,
        viewer_participation,
    )

    lobby_recap_url = ""
    connect_nudge_url = ""
    if lobby_feature_enabled():
        in_recap = event_lobby_phase(event) == PHASE_RECAP
        if in_recap and viewer_participation(user, event) is not None:
            lobby_recap_url = get_user_language_url(
                user,
                "crush_lu:event_lobby",
                request,
                kwargs={"event_id": event.id},
            )
        else:
            # Nudge only genuinely not-onboarded, LuxID-capable guests. Any
            # other gate denial (coach exclusion, revoked consent, lost
            # verification) must render as if no lobby state existed at all.
            gate_ok, gate_reason = participant_gate(user)
            if (
                not gate_ok
                and gate_reason in (GATE_NO_MEMBERSHIP, GATE_NOT_ONBOARDED)
                and may_learn_lobby_exists(user)
            ):
                connect_nudge_url = get_user_language_url(
                    user,
                    "crush_lu:crush_connect_onboarding",
                    request,
                )

    context = get_email_context_with_unsubscribe(
        user,
        request,
        registration=registration,
        event=event,
        mutual_match_count=mutual_match_count,
        outgoing_count=outgoing_count,
        incoming_count=incoming_count,
        has_action=has_action,
        attendees_url=attendees_url,
        events_url=events_url,
        lobby_recap_url=lobby_recap_url,
        connect_nudge_url=connect_nudge_url,
    )

    with translation.override(lang):
        subject = _("Recap of {title}").format(title=event.title)
        html_message = render_to_string("crush_lu/emails/event_recap.html", context)
        plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[user.email],
        request=request,
        # Batch sends (send_event_recaps) have no request: without an explicit
        # domain the config falls back to the PowerUp sender — recaps must
        # always leave from the Crush brand.
        domain="crush.lu",
        fail_silently=False,
    )


def send_event_feedback_request(registration, request=None):
    """
    Send a post-event feedback survey email to an attendee.

    Args:
        registration: EventRegistration with status='attended'
        request: Optional Django request (None when called from mgmt commands)

    Returns:
        int: Number of emails sent (0 if skipped due to opt-out)
    """
    from django.utils import translation
    from django.utils.translation import gettext as _

    if not can_send_email(registration.user, "event_reminders"):
        logger.info(
            f"Skipping feedback request email to {registration.user.email} - user unsubscribed"
        )
        return 0

    lang = get_user_preferred_language(
        user=registration.user, request=request, default="en"
    )

    feedback_url = get_user_language_url(
        registration.user,
        "crush_lu:event_feedback",
        request,
        kwargs={"event_id": registration.event.id},
    )

    context = get_email_context_with_unsubscribe(
        registration.user,
        request,
        registration=registration,
        event=registration.event,
        feedback_url=feedback_url,
    )

    with translation.override(lang):
        subject = _("How was {title}?").format(title=registration.event.title)
        html_message = render_to_string(
            "crush_lu/emails/event_feedback_request.html", context
        )
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
    result = {"user_email_sent": False, "coach_email_sent": False}
    user = submission.profile.user

    # Send confirmation email to user
    try:
        email_result = send_profile_submission_confirmation(user, request)
        result["user_email_sent"] = email_result > 0
        logger.info(f"✅ Profile submission email sent to {user.email}: {email_result}")
    except Exception as e:
        logger.error(
            f"❌ Failed to send profile submission confirmation to {user.email}: {e}",
            exc_info=True,
        )
        if add_message_func:
            add_message_func(
                "Profile submitted! (Email notification may have failed - check your spam folder)"
            )

    # Send notification to assigned coach if one was assigned
    if submission.coach:
        try:
            email_result = send_coach_assignment_notification(
                submission.coach, submission, request
            )
            result["coach_email_sent"] = email_result > 0
            logger.info(
                f"✅ Coach assignment email sent to {submission.coach.user.email}: {email_result}"
            )
        except Exception as e:
            logger.error(
                f"❌ Failed to send coach assignment notification: {e}", exc_info=True
            )

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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Check email preferences
    if not can_send_email(recipient, "new_connections"):
        logger.info(
            f"Skipping connection request email to {recipient.email} - user unsubscribed"
        )
        return 0

    # Get user's preferred language
    lang = get_user_preferred_language(user=recipient, request=request, default="en")

    # Get requester display name
    if hasattr(requester, "crushprofile"):
        requester_name = requester.crushprofile.display_name
    else:
        requester_name = requester.first_name

    # Get event info
    event = connection.event
    event_title = event.title if event else "a Crush.lu event"
    event_date = (
        event.date_time.strftime("%B %d, %Y") if event and event.date_time else ""
    )

    # Build connections URL with language prefix
    connections_url = get_user_language_url(
        recipient, "crush_lu:my_connections", request
    )

    context = get_email_context_with_unsubscribe(
        recipient,
        request,
        first_name=recipient.first_name,
        requester_name=requester_name,
        event_title=event_title,
        event_date=event_date,
        connections_url=connections_url,
    )

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Someone wants to connect with you!")
        html_message = render_to_string(
            "crush_lu/emails/new_connection_request.html", context
        )
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Check email preferences
    if not can_send_email(recipient, "new_connections"):
        logger.info(
            f"Skipping connection accepted email to {recipient.email} - user unsubscribed"
        )
        return 0

    # Get user's preferred language
    lang = get_user_preferred_language(user=recipient, request=request, default="en")

    # Get accepter display name
    if hasattr(accepter, "crushprofile"):
        accepter_name = accepter.crushprofile.display_name
    else:
        accepter_name = accepter.first_name

    # Get event info
    event = connection.event
    event_title = event.title if event else "a Crush.lu event"

    # Build connection detail URL with language prefix
    connection_url = get_user_language_url(
        recipient,
        "crush_lu:connection_detail",
        request,
        kwargs={"connection_id": connection.id},
    )

    context = get_email_context_with_unsubscribe(
        recipient,
        request,
        first_name=recipient.first_name,
        accepter_name=accepter_name,
        event_title=event_title,
        connection_url=connection_url,
    )

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("Your connection request was accepted!")
        html_message = render_to_string(
            "crush_lu/emails/connection_accepted.html", context
        )
        plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[recipient.email],
        request=request,
        fail_silently=False,
    )


def send_mutual_match_email(recipient, connection, other_user, request):
    """
    Notify a user that an event connection became mutual ("It's a Match!").

    Both attendees independently requested each other; the backend has already
    fast-tracked the connection (same-gender → ``shared``, cross-gender →
    ``accepted`` with a coach assigned). This email differs from the generic
    "your request was accepted" notification in copy and CTA.

    Args:
        recipient: User receiving the email
        connection: EventConnection from recipient's perspective
        other_user: the other half of the match
        request: Django request for domain detection
    """
    from django.utils import translation
    from django.utils.translation import gettext as _

    if not can_send_email(recipient, "new_connections"):
        logger.info(
            f"Skipping mutual match email to {recipient.email} - user unsubscribed"
        )
        return 0

    lang = get_user_preferred_language(user=recipient, request=request, default="en")

    if hasattr(other_user, "crushprofile"):
        other_name = other_user.crushprofile.display_name
    else:
        other_name = other_user.first_name

    event = connection.event
    event_title = event.title if event else "a Crush.lu event"

    is_same_gender = connection.is_same_gender
    coach = connection.assigned_coach
    coach_name = coach.user.first_name if coach and coach.user else ""

    connection_url = get_user_language_url(
        recipient,
        "crush_lu:connection_detail",
        request,
        kwargs={"connection_id": connection.id},
    )

    context = get_email_context_with_unsubscribe(
        recipient,
        request,
        first_name=recipient.first_name,
        other_name=other_name,
        event_title=event_title,
        connection_url=connection_url,
        is_same_gender=is_same_gender,
        coach_name=coach_name,
    )

    with translation.override(lang):
        subject = _("It's a match! 🎉")
        html_message = render_to_string("crush_lu/emails/mutual_match.html", context)
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
    from django.utils import translation
    from django.utils.translation import gettext as _

    # Check email preferences
    if not can_send_email(recipient, "new_messages"):
        logger.info(
            f"Skipping new message email to {recipient.email} - user unsubscribed"
        )
        return 0

    # Get user's preferred language
    lang = get_user_preferred_language(user=recipient, request=request, default="en")

    # Get sender display name
    sender = message.sender
    if hasattr(sender, "crushprofile"):
        sender_name = sender.crushprofile.display_name
    else:
        sender_name = sender.first_name

    # Truncate message for preview
    message_preview = message.message[:100]
    if len(message.message) > 100:
        message_preview += "..."

    # Build connection URL with language prefix
    connection_url = get_user_language_url(
        recipient,
        "crush_lu:connection_detail",
        request,
        kwargs={"connection_id": message.connection.id},
    )

    context = get_email_context_with_unsubscribe(
        recipient,
        request,
        first_name=recipient.first_name,
        sender_name=sender_name,
        message_preview=message_preview,
        connection_url=connection_url,
    )

    # Render email in user's preferred language
    with translation.override(lang):
        subject = _("New message from {name}").format(name=sender_name)
        html_message = render_to_string("crush_lu/emails/new_message.html", context)
        plain_message = strip_tags(html_message)

    return send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[recipient.email],
        request=request,
        fail_silently=False,
    )


# =============================================================================
# PROFILE COMPLETION REMINDER EMAILS
# =============================================================================


def get_users_needing_reminder(reminder_type):
    """
    Get users who need a specific profile completion reminder.

    Uses configurable timing from settings.PROFILE_REMINDER_TIMING.

    Logic (windows are 48h wide so one missed daily tick is retried, not
    dropped — see settings.PROFILE_REMINDER_TIMING):
    - 24h: Created 24-72h ago, completion_status incomplete, no 24h reminder sent
    - 72h: Created 72-120h ago, incomplete, has 24h but no 72h reminder
    - 7d: Created 7-9d ago, incomplete, has 72h but no 7d reminder

    Args:
        reminder_type: One of '24h', '72h', '7d'

    Returns:
        QuerySet of User objects
    """
    from django.conf import settings
    from django.utils import timezone
    from django.contrib.auth.models import User

    # Get timing config
    timing = getattr(settings, "PROFILE_REMINDER_TIMING", {})
    if reminder_type not in timing:
        logger.error(f"Unknown reminder type: {reminder_type}")
        return User.objects.none()

    min_hours = timing[reminder_type]["min_hours"]
    max_hours = timing[reminder_type]["max_hours"]

    now = timezone.now()
    min_created = now - timezone.timedelta(hours=max_hours)
    max_created = now - timezone.timedelta(hours=min_hours)

    # Incomplete statuses that need reminders
    incomplete_statuses = ["not_started", "step1", "step2", "step3", "step4"]

    # Get users with incomplete profiles in the time window. Exclude anyone we
    # must not contact: a ban keeps the CrushProfile but sets user.is_active
    # False and data_consent.crushlu_banned True, and a profile deactivation
    # sets crushprofile.is_active False — can_send_email() only checks
    # EmailPreference, so those users would otherwise still be emailed. (Codex P1)
    #
    # Also require crushlu_consent_given: create_crush_profile_on_login lazily
    # creates an incomplete CrushProfile for an existing cross-domain account on
    # its first crush.lu login, while ConsentMiddleware redirects it to confirm
    # consent. If the user never confirms, crushlu_consent_given stays False (the
    # signal default) and we must not send profile-completion outreach for a
    # profile layer they haven't consented to. Requiring =True also drops users
    # with no data_consent row at all, which is correct — no consent recorded.
    # (Codex P1)
    users = User.objects.filter(
        is_active=True,
        crushprofile__completion_status__in=incomplete_statuses,
        crushprofile__is_active=True,
        crushprofile__created_at__gte=min_created,
        crushprofile__created_at__lte=max_created,
        data_consent__crushlu_consent_given=True,
    ).exclude(
        # Exclude users who already got this reminder type
        profile_reminders__reminder_type=reminder_type
    ).exclude(
        data_consent__crushlu_banned=True,
    )

    # For 72h and 7d, require the previous reminder to have been sent AND to
    # have been sent long enough ago to preserve the intended cadence. Checking
    # only that the prior row exists is not enough: with the widened drain
    # windows a backlog-recovered profile (e.g. a 6-day-old signup that only
    # gets its 24h nudge today) already satisfies every later stage's created_at
    # window, so it would receive 24h, then 72h, then 7d on three consecutive
    # daily sweeps. Gate the next stage on the prior reminder's sent_at plus the
    # same spacing an on-time profile sees — the difference of the two stages'
    # min_hours (24h->72h = 48h, 72h->7d = 96h). (Codex P2)
    prior_type = {"72h": "24h", "7d": "72h"}.get(reminder_type)
    if prior_type:
        min_gap_hours = min_hours - timing[prior_type]["min_hours"]
        prior_sent_cutoff = now - timezone.timedelta(hours=min_gap_hours)
        users = users.filter(
            profile_reminders__reminder_type=prior_type,
            profile_reminders__sent_at__lte=prior_sent_cutoff,
        )

    # Oldest PROFILES first so that when a run's send limit / time budget can't
    # clear the whole backlog, the users closest to ageing out of max_hours are
    # served first. Order by crushprofile.created_at — the same timestamp
    # eligibility and expiry use — NOT User.date_joined: create_crush_profile_
    # on_login (crush_lu/signals.py) lazily creates a CrushProfile when an old
    # cross-domain account first logs into crush.lu, so date_joined can be far
    # older than the profile and would push a genuinely near-expiry profile
    # behind a freshly-created one. Annotate the profile timestamp and order by
    # the annotation so SELECT DISTINCT ... ORDER BY stays valid on PostgreSQL
    # (the ordering expression is then part of the select list); pk is a stable
    # tiebreaker. (Codex P2)
    from django.db.models import F

    return (
        users.annotate(_reminder_profile_created=F("crushprofile__created_at"))
        .distinct()
        .order_by("_reminder_profile_created", "pk")
    )


# =============================================================================
# JOURNEY GIFT EMAIL NOTIFICATIONS
# =============================================================================


def send_journey_gift_notification(gift, request):
    """
    Send notification email to gift recipient when a journey gift is created.

    The email includes the QR code, gift details, and a direct claim link.
    Only sends if recipient_email is provided on the gift.

    Args:
        gift: JourneyGift instance
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent (1 on success, 0 if no email or failure)
    """
    from django.utils import translation
    from django.utils.translation import gettext as _

    if not gift.recipient_email:
        logger.info(
            f"No recipient email for gift {gift.gift_code}, skipping notification"
        )
        return 0

    # Get sender's preferred language (fall back to English)
    sender_lang = "en"
    try:
        if hasattr(gift.sender, "crushprofile") and gift.sender.crushprofile:
            sender_lang = gift.sender.crushprofile.preferred_language or "en"
    except Exception:
        pass

    # Get sender name
    sender_name = gift.sender.first_name or gift.sender.username

    # Build claim URL - gift landing page with language prefix
    protocol = "https" if request.is_secure() else "http"
    domain = request.get_host()

    # Use reverse() with override() for proper language-prefixed URLs
    with translation.override(sender_lang):
        claim_url_path = reverse(
            "crush_lu:gift_landing", kwargs={"gift_code": gift.gift_code}
        )
        home_url_path = reverse("crush_lu:home")
        about_url_path = reverse("crush_lu:about")
        events_url_path = reverse("crush_lu:event_list")

    claim_url = f"{protocol}://{domain}{claim_url_path}"
    home_url = f"{protocol}://{domain}{home_url_path}"
    about_url = f"{protocol}://{domain}{about_url_path}"
    events_url = f"{protocol}://{domain}{events_url_path}"

    # Get QR code URL (may be None if not generated yet)
    qr_code_url = None
    if gift.qr_code_image:
        try:
            qr_code_url = gift.qr_code_image.url
            # Make absolute URL if needed
            if qr_code_url and not qr_code_url.startswith("http"):
                qr_code_url = f"{protocol}://{domain}{qr_code_url}"
        except Exception as e:
            logger.warning(f"Could not get QR code URL for gift {gift.gift_code}: {e}")

    context = {
        "recipient_name": gift.recipient_name,
        "sender_name": sender_name,
        "sender_message": gift.sender_message,
        "claim_url": claim_url,
        "qr_code_url": qr_code_url,
        "gift_code": gift.gift_code,
        "home_url": home_url,
        "about_url": about_url,
        "events_url": events_url,
        "social_links": get_social_links(),
    }

    # Render email template in sender's preferred language
    with translation.override(sender_lang):
        subject = _("{sender_name} has created a magical journey for you!").format(
            sender_name=sender_name
        )
        html_message = render_to_string(
            "crush_lu/emails/journey_gift_notification.html", context
        )
        plain_message = strip_tags(html_message)

    try:
        return send_domain_email(
            subject=subject,
            message=plain_message,
            html_message=html_message,
            recipient_list=[gift.recipient_email],
            request=request,
            fail_silently=False,
        )
    except Exception as e:
        logger.error(
            f"Failed to send gift notification email to {gift.recipient_email}: {e}",
            exc_info=True,
        )
        return 0


# =============================================================================
# PROFILE COMPLETION REMINDER EMAILS
# =============================================================================


def send_profile_incomplete_reminder(user, reminder_type, request=None):
    """
    Send a profile completion reminder email.

    Args:
        user: User object
        reminder_type: One of '24h', '72h', '7d'
        request: Optional Django request object. If None, creates a mock request.

    Returns:
        bool: True if email was sent successfully
    """
    from django.utils import timezone, translation
    from django.utils.translation import gettext as _
    from .models import CrushProfile, ProfileReminder, UserActivity, EmailPreference

    # Check email preferences
    if not can_send_email(user, "profile_updates"):
        logger.info(
            f"Skipping profile reminder to {user.email} - user unsubscribed from profile_updates"
        )
        return False

    # Get user's profile
    try:
        profile = user.crushprofile
    except CrushProfile.DoesNotExist:
        logger.warning(f"User {user.id} has no CrushProfile, skipping reminder")
        return False

    # Don't send reminders for profiles that are already submitted or verified
    if profile.verification_status in ("pending", "verified"):
        logger.info(
            f"User {user.email} already has verification_status={profile.verification_status}, skipping"
        )
        return False

    # Select template based on reminder type
    template_map = {
        "24h": "crush_lu/emails/profile_incomplete_24h.html",
        "72h": "crush_lu/emails/profile_incomplete_72h.html",
        "7d": "crush_lu/emails/profile_incomplete_final.html",
    }
    template = template_map.get(reminder_type)
    if not template:
        logger.error(f"Unknown reminder type: {reminder_type}")
        return False

    # Concurrency guard (Codex P2). This helper is invoked by both the daily
    # timer sweep and the superuser panel (crush_lu/admin/profile_reminders.py),
    # and it delivers the email *before* recording the ProfileReminder row — so
    # two paths that both pass eligibility could each send before either writes
    # the row. First skip if a staggered concurrent path already recorded it;
    # then take a short-lived per-user cache claim to close the send-before-
    # insert window for a truly simultaneous path. The row is still written only
    # on success, so a crashed run leaves no false "sent" record — the claim
    # (5 min TTL) simply expires and the reminder is retried on the next run.
    if ProfileReminder.objects.filter(
        user=user, reminder_type=reminder_type
    ).exists():
        logger.info(
            f"{reminder_type} reminder already recorded for {user.email}; skipping"
        )
        return False

    claim_key = f"profile_reminder_claim:{user.id}:{reminder_type}"
    claim = cache.add(claim_key, "1", 300)
    if claim is False:
        # Key present -> another path is delivering this reminder right now.
        logger.info(
            f"{reminder_type} reminder for {user.email} is already in flight; "
            "skipping"
        )
        return False
    # claim is True (we hold it) or None (cache unavailable — Redis down with
    # IGNORE_EXCEPTIONS returns None, not raise). On an outage, fail open: the
    # ProfileReminder row check above still dedups staggered paths, so proceed
    # rather than skip the send. Only release a claim we actually took.
    claim_held = claim is True

    # Get user's preferred language for email content
    lang = getattr(profile, "preferred_language", "en") or "en"

    # Build profile URL - need to handle case where request is None
    if request:
        profile_url = get_user_language_url(user, "crush_lu:create_profile", request)
        context = get_email_context_with_unsubscribe(
            user,
            request,
            completion_status=profile.completion_status,
            profile_url=profile_url,
        )
    else:
        # Create minimal context for batch sending without request
        # Use reverse() with override() for proper language-prefixed URLs
        from .utils.i18n import build_absolute_url

        profile_url = build_absolute_url("crush_lu:create_profile", lang=lang)

        # Get or create email preferences for unsubscribe URL
        email_prefs = EmailPreference.get_or_create_for_user(user)
        unsubscribe_url = build_absolute_url(
            "crush_lu:email_unsubscribe",
            lang=lang,
            kwargs={"token": email_prefs.unsubscribe_token},
        )

        context = {
            "user": user,
            "completion_status": profile.completion_status,
            "profile_url": profile_url,
            "unsubscribe_url": unsubscribe_url,
            "home_url": build_absolute_url("crush_lu:home", lang=lang),
            "about_url": build_absolute_url("crush_lu:about", lang=lang),
            "events_url": build_absolute_url("crush_lu:event_list", lang=lang),
            "settings_url": build_absolute_url("crush_lu:account_settings", lang=lang),
            "social_links": get_social_links(),
        }

    # Render email and send in user's preferred language
    # Use translation.override() for thread-safety
    with translation.override(lang):
        # Subject lines - translated based on user's language
        subject_map = {
            "24h": _("Complete Your Profile - Crush.lu"),
            "72h": _("Don't Miss Out - Your Profile is Waiting"),
            "7d": _("Last Chance to Complete Your Profile - Crush.lu"),
        }
        subject = subject_map[reminder_type]

        # Render email template in user's language
        html_message = render_to_string(template, context)
        plain_message = strip_tags(html_message)

    # Send email
    try:
        result = send_domain_email(
            subject=subject,
            message=plain_message,
            html_message=html_message,
            recipient_list=[user.email],
            request=request,
            # Batch runs (management command, admin panel) pass request=None;
            # without an explicit domain the config falls back to powerup.lu.
            domain="crush.lu",
            fail_silently=False,
        )

        if result > 0:
            # Record reminder sent
            ProfileReminder.objects.create(
                user=user,
                reminder_type=reminder_type,
            )
            # Durable dedup now exists — release the in-flight claim.
            if claim_held:
                cache.delete(claim_key)

            # Update UserActivity if it exists
            try:
                activity = user.activity
                activity.last_reminder_sent = timezone.now()
                activity.reminders_sent_count += 1
                activity.save(
                    update_fields=["last_reminder_sent", "reminders_sent_count"]
                )
            except UserActivity.DoesNotExist:
                pass

            logger.info(
                f"Sent {reminder_type} profile reminder to {user.email} (lang={lang})"
            )
            return True
        else:
            # Nothing delivered — release the claim so it retries next run.
            if claim_held:
                cache.delete(claim_key)
            logger.warning(f"Email send returned 0 for {user.email}")
            return False

    except Exception as e:
        # Send failed — release the claim so the reminder isn't dropped.
        if claim_held:
            cache.delete(claim_key)
        logger.error(
            f"Failed to send {reminder_type} reminder to {user.email}: {e}",
            exc_info=True,
        )
        return False


def send_crush_connect_catalogue_welcome(user, request):
    """
    Confirm catalogue entry to a candidate-track member (LuxID, no Premium)
    right after they finish Crush Connect onboarding.

    They won't receive Drops, so without this email being "discoverable"
    would be invisible to them. Failures never block the opt-in flow.

    Returns:
        int: Number of emails sent (1 on success, 0 on failure)
    """
    from django.utils import translation
    from django.utils.translation import gettext as _

    try:
        lang = get_user_preferred_language(user=user, request=request, default="en")

        premium_url = get_user_language_url(
            user, "crush_lu:premium_choose_coach", request
        )
        dashboard_url = get_user_language_url(user, "crush_lu:dashboard", request)

        context = get_email_context_with_unsubscribe(
            user,
            request,
            first_name=user.first_name,
            premium_url=premium_url,
            dashboard_url=dashboard_url,
        )

        with translation.override(lang):
            subject = _("You're in Crush Connect")
            html_message = render_to_string(
                "crush_lu/emails/crush_connect_catalogue_welcome.html", context
            )
            plain_message = strip_tags(html_message)

        return send_domain_email(
            subject=subject,
            message=plain_message,
            html_message=html_message,
            recipient_list=[user.email],
            request=request,
            fail_silently=True,
        )
    except Exception as e:
        logger.error(
            f"Failed to send Crush Connect catalogue welcome to {user.email}: {e}",
            exc_info=True,
        )
        return 0


def send_connect_spark_received_email(spark, request):
    """
    Tell a catalogue member someone sent them a Curiosity Spark.

    Candidates never receive Drops, so this email (plus the bell) is their
    only way to learn a Premium member is curious. Failures never block the
    send flow.
    """
    from django.utils import translation
    from django.utils.translation import gettext as _

    user = spark.recipient
    try:
        lang = get_user_preferred_language(user=user, request=request, default="en")
        sparks_url = get_user_language_url(
            user, "crush_lu:crush_connect_sparks_received", request
        )
        context = get_email_context_with_unsubscribe(
            user,
            request,
            first_name=user.first_name,
            sender_first_name=spark.sender.first_name,
            spark_message=spark.message,
            sparks_url=sparks_url,
        )
        with translation.override(lang):
            subject = _("Someone is curious about you — Crush Connect")
            html_message = render_to_string(
                "crush_lu/emails/crush_connect_spark_received.html", context
            )
            plain_message = strip_tags(html_message)
        return send_domain_email(
            subject=subject,
            message=plain_message,
            html_message=html_message,
            recipient_list=[user.email],
            request=request,
            fail_silently=True,
        )
    except Exception as e:
        logger.error(
            f"Failed to send spark-received email to {user.email}: {e}",
            exc_info=True,
        )
        return 0


def send_connect_spark_accepted_email(spark, request):
    """
    Tell the sender their Spark was accepted — the coach arranges the date.
    Declines are silent by design; acceptance is the only event that travels
    back to the sender.
    """
    from django.utils import translation
    from django.utils.translation import gettext as _

    user = spark.sender
    try:
        lang = get_user_preferred_language(user=user, request=request, default="en")
        connect_url = get_user_language_url(
            user, "crush_lu:crush_connect_home", request
        )
        context = get_email_context_with_unsubscribe(
            user,
            request,
            first_name=user.first_name,
            recipient_first_name=spark.recipient.first_name,
            connect_url=connect_url,
        )
        with translation.override(lang):
            subject = _("It's mutual! — Crush Connect")
            html_message = render_to_string(
                "crush_lu/emails/crush_connect_spark_accepted.html", context
            )
            plain_message = strip_tags(html_message)
        return send_domain_email(
            subject=subject,
            message=plain_message,
            html_message=html_message,
            recipient_list=[user.email],
            request=request,
            fail_silently=True,
        )
    except Exception as e:
        logger.error(
            f"Failed to send spark-accepted email to {user.email}: {e}",
            exc_info=True,
        )
        return 0
