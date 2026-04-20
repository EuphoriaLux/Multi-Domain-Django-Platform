# crush_lu/tasks.py
"""
Django 6.0 Background Tasks for Crush.lu.

Wraps email-sending and notification functions as background tasks using
Django's native @task decorator. Tasks are enqueued from views/signals and
executed by the configured backend (ImmediateBackend for dev, DatabaseBackend
for production).

IMPORTANT: Task functions must accept only serializable arguments (no request
objects). Use user_id + host instead of request for domain detection.

Usage in views:
    from crush_lu.tasks import send_welcome_email_task
    send_welcome_email_task.enqueue(user_id=user.id, host=request.get_host())

See: https://docs.djangoproject.com/en/6.0/topics/tasks/
"""
import logging

from django.tasks import task

logger = logging.getLogger(__name__)


def _build_fake_request(host, is_secure=True):
    """
    Build a minimal request-like object for email URL generation.

    Background tasks don't have access to the HTTP request, so we create
    a lightweight stand-in with just the attributes email helpers need:
    - get_host() -> domain string
    - is_secure() -> bool for https://
    """
    class FakeRequest:
        def __init__(self, host, secure):
            self._host = host
            self._secure = secure
            self.META = {"HTTP_HOST": host, "SERVER_PORT": "443" if secure else "80"}

        def get_host(self):
            return self._host

        def is_secure(self):
            return self._secure

        def get_port(self):
            return "443" if self._secure else "80"

    return FakeRequest(host, is_secure)


@task(priority=10)
def send_welcome_email_task(user_id, host, is_secure=True):
    """Send welcome email as a background task."""
    from django.contrib.auth import get_user_model
    from .email_helpers import send_welcome_email

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
        request = _build_fake_request(host, is_secure)
        send_welcome_email(user, request)
        logger.info(f"[TASK] Welcome email sent to user {user_id}")
    except User.DoesNotExist:
        logger.warning(f"[TASK] User {user_id} not found for welcome email")
    except Exception as e:
        logger.error(f"[TASK] Failed to send welcome email to user {user_id}: {e}")


@task(priority=10)
def send_profile_submission_email_task(user_id, host, is_secure=True):
    """Send profile submission confirmation as a background task."""
    from django.contrib.auth import get_user_model
    from .email_helpers import send_profile_submission_confirmation

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
        request = _build_fake_request(host, is_secure)
        send_profile_submission_confirmation(user, request)
        logger.info(f"[TASK] Profile submission email sent to user {user_id}")
    except User.DoesNotExist:
        logger.warning(f"[TASK] User {user_id} not found for submission email")
    except Exception as e:
        logger.error(f"[TASK] Failed to send submission email to user {user_id}: {e}")


@task(priority=5)
def send_event_registration_email_task(registration_id, host, is_secure=True):
    """Send event registration confirmation as a background task."""
    from .models import EventRegistration
    from .email_helpers import send_event_registration_confirmation

    try:
        registration = EventRegistration.objects.select_related(
            "user", "event"
        ).get(pk=registration_id)
        request = _build_fake_request(host, is_secure)
        send_event_registration_confirmation(registration, request)
        logger.info(
            f"[TASK] Registration email sent for registration {registration_id}"
        )
    except EventRegistration.DoesNotExist:
        logger.warning(
            f"[TASK] Registration {registration_id} not found for email"
        )
    except Exception as e:
        logger.error(
            f"[TASK] Failed to send registration email {registration_id}: {e}"
        )


@task(priority=8)
def send_connection_notification_task(
    recipient_id, connection_id, requester_id, host, is_secure=True
):
    """Send new connection request notification as a background task."""
    from django.contrib.auth import get_user_model
    from .models import EventConnection
    from .email_helpers import send_new_connection_request_notification

    User = get_user_model()
    try:
        recipient = User.objects.get(pk=recipient_id)
        connection = EventConnection.objects.get(pk=connection_id)
        requester = User.objects.get(pk=requester_id)
        request = _build_fake_request(host, is_secure)
        send_new_connection_request_notification(
            recipient, connection, requester, request
        )
        logger.info(
            f"[TASK] Connection notification sent to user {recipient_id}"
        )
    except (User.DoesNotExist, EventConnection.DoesNotExist) as e:
        logger.warning(f"[TASK] Object not found for connection notification: {e}")
    except Exception as e:
        logger.error(
            f"[TASK] Failed to send connection notification to {recipient_id}: {e}"
        )


@task(priority=3)
def send_profile_reminder_task(user_id, reminder_type, host, is_secure=True):
    """Send profile completion reminder as a background task."""
    from django.contrib.auth import get_user_model
    from .email_helpers import send_profile_incomplete_reminder

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
        request = _build_fake_request(host, is_secure)
        send_profile_incomplete_reminder(user, reminder_type, request)
        logger.info(
            f"[TASK] Profile reminder ({reminder_type}) sent to user {user_id}"
        )
    except User.DoesNotExist:
        logger.warning(f"[TASK] User {user_id} not found for profile reminder")
    except Exception as e:
        logger.error(
            f"[TASK] Failed to send profile reminder to user {user_id}: {e}"
        )


@task(priority=5)
def send_coach_push_notification_task(coach_user_id, title, body, url=None):
    """Send push notification to a coach as a background task."""
    from .coach_notifications import _send_push_to_coach

    try:
        _send_push_to_coach(coach_user_id, title, body, url)
        logger.info(f"[TASK] Push notification sent to coach {coach_user_id}")
    except Exception as e:
        logger.error(
            f"[TASK] Failed to send push to coach {coach_user_id}: {e}"
        )


# --------------------------------------------------------------------------
# Pre-screening questionnaire tasks (Phase 5)
# --------------------------------------------------------------------------

@task(priority=5)
def send_pre_screening_invite_task(submission_id, host, is_secure=True, reminder=False):
    """Send the pre-screening invite or reminder email for one submission."""
    from .models import ProfileSubmission
    from .pre_screening_notifications import send_pre_screening_invite_email

    try:
        submission = ProfileSubmission.objects.select_related(
            "profile__user"
        ).get(pk=submission_id)
    except ProfileSubmission.DoesNotExist:
        logger.warning(
            f"[TASK] ProfileSubmission {submission_id} not found for pre-screening invite"
        )
        return
    request = _build_fake_request(host, is_secure)
    try:
        send_pre_screening_invite_email(submission, reminder=reminder, request=request)
    except Exception as e:
        logger.error(
            f"[TASK] Failed to send pre-screening {'reminder' if reminder else 'invite'} "
            f"for submission {submission_id}: {e}"
        )


@task(priority=7)
def send_pre_screening_user_push_task(submission_id):
    """Push nudge to the user to fill in pre-screening."""
    from .models import ProfileSubmission
    from .pre_screening_notifications import send_pre_screening_user_push

    try:
        submission = ProfileSubmission.objects.select_related(
            "profile__user"
        ).get(pk=submission_id)
    except ProfileSubmission.DoesNotExist:
        return
    try:
        send_pre_screening_user_push(submission)
    except Exception as e:
        logger.error(
            f"[TASK] Failed to send pre-screening user push for {submission_id}: {e}"
        )
