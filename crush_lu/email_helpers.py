# crush_lu/email_helpers.py
"""
Email helper functions specific to Crush.lu platform.
Handles profile submissions, coach notifications, event registrations, etc.
"""
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from azureproject.email_utils import send_domain_email, get_domain_from_email


def send_profile_submission_confirmation(user, request):
    """
    Send confirmation email to user after profile submission.

    Args:
        user: User object
        request: Django request object for domain detection

    Returns:
        int: Number of emails sent (1 on success, 0 on failure)
    """
    subject = "Profile Submitted for Review - Crush.lu"

    html_message = render_to_string('crush_lu/emails/profile_submission_confirmation.html', {
        'user': user,
        'first_name': user.first_name,
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

    html_message = render_to_string('crush_lu/emails/coach_assignment.html', {
        'coach': coach,
        'submission': profile_submission,
        'profile': profile_submission.profile,
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
    subject = "Welcome to Crush.lu - Your Profile is Approved! 🎉"

    html_message = render_to_string('crush_lu/emails/profile_approved.html', {
        'user': profile.user,
        'first_name': profile.user.first_name,
        'coach_notes': coach_notes,
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

    html_message = render_to_string('crush_lu/emails/profile_revision_request.html', {
        'user': profile.user,
        'first_name': profile.user.first_name,
        'feedback': feedback,
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
    subject = f"Event Registration Confirmed - {registration.event.title}"

    html_message = render_to_string('crush_lu/emails/event_registration_confirmation.html', {
        'user': registration.user,
        'registration': registration,
        'event': registration.event,
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

    html_message = render_to_string('crush_lu/emails/event_waitlist.html', {
        'user': registration.user,
        'registration': registration,
        'event': registration.event,
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

    html_message = render_to_string('crush_lu/emails/event_cancellation.html', {
        'user': user,
        'event': event,
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
    subject = f"Event Reminder - {registration.event.title} in {days_until_event} days"

    html_message = render_to_string('crush_lu/emails/event_reminder.html', {
        'user': registration.user,
        'registration': registration,
        'event': registration.event,
        'days_until_event': days_until_event,
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
