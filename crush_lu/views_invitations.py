from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import uuid
import logging

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile,
    CrushCoach,
    MeetupEvent,
    EventInvitation,
)
from .decorators import crush_login_required, coach_required, ratelimit


def invitation_landing(request, code):
    """
    PUBLIC ACCESS: Landing page for invitation link.
    Accessible without login - shows event details and invitation info.
    """
    invitation = get_object_or_404(EventInvitation, invitation_code=code)

    # Check expiration
    if invitation.is_expired:
        invitation.status = "expired"
        invitation.save()
        return render(
            request, "crush_lu/invitation_expired.html", {"invitation": invitation}
        )

    # Check if already accepted
    if invitation.status == "accepted":
        messages.info(
            request,
            _("You have already accepted this invitation. Please log in to continue."),
        )
        return redirect("crush_lu:crush_login")

    # Show invitation details
    context = {
        "invitation": invitation,
        "event": invitation.event,
    }
    return render(request, "crush_lu/invitation_landing.html", context)


@ratelimit(key="ip", rate="10/h", method="POST")
def invitation_accept(request, code):
    """
    PUBLIC ACCESS: Accept invitation and create guest account with age verification.

    Security Requirements:
    - Validates 18+ age requirement
    - Captures actual date of birth (no hardcoded ages)
    - Creates profile pending coach approval (no auto-approval)
    """
    from .forms import InvitationAcceptanceForm

    invitation = get_object_or_404(EventInvitation, invitation_code=code)

    # Check if already accepted
    if invitation.status == "accepted":
        messages.info(request, _("This invitation has already been accepted."))
        return redirect("crush_lu:crush_login")

    # Check expiration
    if invitation.is_expired:
        messages.error(request, _("This invitation has expired."))
        return redirect("crush_lu:invitation_landing", code=code)

    if request.method == "POST":
        form = InvitationAcceptanceForm(request.POST, invitation=invitation)

        if form.is_valid():
            try:
                date_of_birth = form.cleaned_data["date_of_birth"]

                # Create user account with random password
                username = f"guest_{invitation.guest_email.split('@')[0]}_{uuid.uuid4().hex[:6]}"
                from django.contrib.auth.hashers import make_password
                import secrets
                import string

                # Generate secure random password
                alphabet = string.ascii_letters + string.digits + string.punctuation
                random_password = "".join(secrets.choice(alphabet) for _ in range(16))

                user = User.objects.create_user(
                    username=username,
                    email=invitation.guest_email,
                    first_name=invitation.guest_first_name,
                    last_name=invitation.guest_last_name,
                    password=random_password,
                )

                # Create minimal profile with actual date of birth
                # NOTE: Profile requires coach approval - no auto-approval for security
                from .utils.i18n import validate_language

                preferred_lang = validate_language(
                    getattr(request, "LANGUAGE_CODE", "en"), default="en"
                )

                profile = CrushProfile.objects.create(
                    user=user,
                    date_of_birth=date_of_birth,  # SECURITY: Use actual DOB from form
                    is_approved=False,  # SECURITY: Requires coach approval
                    completion_status="completed",
                    preferred_language=preferred_lang,
                )

                # Update invitation
                invitation.created_user = user
                invitation.status = "accepted"
                invitation.accepted_at = timezone.now()
                invitation.save()

                # Log the user in
                user.backend = "django.contrib.auth.backends.ModelBackend"
                login(request, user)

                logger.info(
                    f"Guest account created with age verification: {user.email} "
                    f"(age: {profile.age}) for event {invitation.event.title}"
                )

                messages.success(
                    request,
                    _(
                        "Welcome! Your invitation has been accepted. "
                        "You will receive an email once your attendance is approved by our team."
                    ),
                )

                return render(
                    request,
                    "crush_lu/invitation_pending_approval.html",
                    {
                        "invitation": invitation,
                        "event": invitation.event,
                    },
                )

            except Exception as e:
                logger.error(f"Error creating guest account: {e}")
                messages.error(
                    request,
                    _(
                        "An error occurred while accepting your invitation. Please try again."
                    ),
                )
                return redirect("crush_lu:invitation_landing", code=code)
        # If form is invalid, fall through to re-render with errors
    else:
        form = InvitationAcceptanceForm(invitation=invitation)

    context = {
        "invitation": invitation,
        "event": invitation.event,
        "form": form,
    }
    return render(request, "crush_lu/invitation_accept_form.html", context)


@coach_required
def coach_manage_invitations(request, event_id):
    """
    COACH ONLY: Dashboard for managing event invitations.
    Send invitations, approve guests, and track invitation status.
    """
    coach = request.coach

    event = get_object_or_404(MeetupEvent, id=event_id, is_private_invitation=True)

    # Verify coach is assigned to this event (or is a superuser)
    if not request.user.is_superuser and not event.coaches.filter(pk=coach.pk).exists():
        messages.error(request, _("You are not assigned to manage this event."))
        return redirect("crush_lu:coach_dashboard")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "send_invitation":
            # Send new invitation
            email = request.POST.get("email", "").strip()
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()

            if not email or not first_name or not last_name:
                messages.error(
                    request, _("Please provide email, first name, and last name.")
                )
            else:
                # Check if invitation already exists
                existing = EventInvitation.objects.filter(
                    event=event, guest_email=email
                ).first()

                if existing:
                    messages.warning(
                        request, f"An invitation for {email} already exists."
                    )
                else:
                    invitation = EventInvitation.objects.create(
                        event=event,
                        guest_email=email,
                        guest_first_name=first_name,
                        guest_last_name=last_name,
                        invited_by=request.user,
                    )

                    # Send email invitation
                    from .email_notifications import (
                        send_external_guest_invitation_email,
                    )

                    email_sent = send_external_guest_invitation_email(
                        invitation, request
                    )
                    if email_sent:
                        messages.success(request, _("Invitation sent to %(email)s.") % {"email": email})
                    else:
                        messages.warning(
                            request,
                            _("Invitation created for %(email)s, but email could not be sent. Code: %(code)s") % {"email": email, "code": invitation.invitation_code},
                        )
                    logger.info(
                        f"Invitation created for {email} to event {event.title}"
                    )

        elif action == "approve_guest":
            invitation_id = request.POST.get("invitation_id")
            try:
                invitation = EventInvitation.objects.get(id=invitation_id, event=event)
                invitation.approval_status = "approved"
                invitation.approved_at = timezone.now()
                invitation.save()

                # Send approval email with login instructions
                from .email_notifications import send_invitation_approval_email

                email_sent = send_invitation_approval_email(invitation, request)
                if email_sent:
                    messages.success(
                        request,
                        f"Guest {invitation.guest_first_name} {invitation.guest_last_name} approved and notified!",
                    )
                else:
                    messages.success(
                        request,
                        f"Guest {invitation.guest_first_name} {invitation.guest_last_name} approved! (Email notification could not be sent)",
                    )
                logger.info(
                    f"Guest approved: {invitation.guest_email} for event {event.title}"
                )
            except EventInvitation.DoesNotExist:
                messages.error(request, _("Invitation not found."))

        elif action == "reject_guest":
            invitation_id = request.POST.get("invitation_id")
            notes = request.POST.get("rejection_notes", "")
            try:
                invitation = EventInvitation.objects.get(id=invitation_id, event=event)
                invitation.approval_status = "rejected"
                invitation.approval_notes = notes
                invitation.save()

                # Send rejection email
                from .email_notifications import send_invitation_rejection_email

                email_sent = send_invitation_rejection_email(invitation, request)
                if email_sent:
                    messages.info(
                        request,
                        f"Guest {invitation.guest_first_name} {invitation.guest_last_name} rejected and notified.",
                    )
                else:
                    messages.info(
                        request,
                        f"Guest {invitation.guest_first_name} {invitation.guest_last_name} rejected. (Email notification could not be sent)",
                    )
                logger.info(
                    f"Guest rejected: {invitation.guest_email} for event {event.title}"
                )
            except EventInvitation.DoesNotExist:
                messages.error(request, _("Invitation not found."))

    # Get all invitations for this event
    invitations = EventInvitation.objects.filter(event=event).order_by(
        "-invitation_sent_at"
    )

    # Separate by status
    pending_approvals = invitations.filter(
        status="accepted", approval_status="pending_approval"
    )
    approved_guests = invitations.filter(approval_status="approved")
    rejected_guests = invitations.filter(approval_status="rejected")
    pending_invitations = invitations.filter(status="pending")

    context = {
        "event": event,
        "invitations": invitations,
        "pending_approvals": pending_approvals,
        "approved_guests": approved_guests,
        "rejected_guests": rejected_guests,
        "pending_invitations": pending_invitations,
        "coach": coach,
    }
    return render(request, "crush_lu/coach_invitation_dashboard.html", context)
