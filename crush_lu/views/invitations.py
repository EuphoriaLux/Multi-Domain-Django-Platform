"""
Private invitation system views for Crush.lu

Handles external guest invitations to events with age verification and account creation.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import logging
import uuid
import secrets
import string

from ..models import EventInvitation, CrushProfile
from ..decorators import ratelimit

logger = logging.getLogger(__name__)


def invitation_landing(request, code):
    """
    PUBLIC ACCESS: Landing page for invitation link.
    Accessible without login - shows event details and invitation info.
    """
    invitation = get_object_or_404(EventInvitation, invitation_code=code)

    # Check expiration
    if invitation.is_expired:
        invitation.status = 'expired'
        invitation.save()
        return render(request, 'crush_lu/invitations/invitation_expired.html', {'invitation': invitation})

    # Check if already accepted
    if invitation.status == 'accepted':
        messages.info(request, _('You have already accepted this invitation. Please log in to continue.'))
        return redirect('crush_lu:crush_login')

    # Show invitation details
    context = {
        'invitation': invitation,
        'event': invitation.event,
    }
    return render(request, 'crush_lu/invitations/invitation_landing.html', context)


@ratelimit(key='ip', rate='10/h', method='POST')
def invitation_accept(request, code):
    """
    PUBLIC ACCESS: Accept invitation and create guest account with age verification.

    Security Requirements:
    - Validates 18+ age requirement
    - Captures actual date of birth (no hardcoded ages)
    - Creates profile pending coach approval (no auto-approval)
    """
    from ..forms import InvitationAcceptanceForm

    invitation = get_object_or_404(EventInvitation, invitation_code=code)

    # Check if already accepted
    if invitation.status == 'accepted':
        messages.info(request, _('This invitation has already been accepted.'))
        return redirect('crush_lu:crush_login')

    # Check expiration
    if invitation.is_expired:
        messages.error(request, _('This invitation has expired.'))
        return redirect('crush_lu:invitation_landing', code=code)

    if request.method == 'POST':
        form = InvitationAcceptanceForm(request.POST, invitation=invitation)

        if form.is_valid():
            try:
                date_of_birth = form.cleaned_data['date_of_birth']

                # Create user account with random password
                username = f"guest_{invitation.guest_email.split('@')[0]}_{uuid.uuid4().hex[:6]}"
                # Generate secure random password
                alphabet = string.ascii_letters + string.digits + string.punctuation
                random_password = ''.join(secrets.choice(alphabet) for _ in range(16))

                user = User.objects.create_user(
                    username=username,
                    email=invitation.guest_email,
                    first_name=invitation.guest_first_name,
                    last_name=invitation.guest_last_name,
                    password=random_password
                )

                # Create minimal profile with actual date of birth
                # NOTE: Profile requires coach approval - no auto-approval for security
                from ..utils.i18n import validate_language
                preferred_lang = validate_language(
                    getattr(request, 'LANGUAGE_CODE', 'en'), default='en'
                )

                profile = CrushProfile.objects.create(
                    user=user,
                    date_of_birth=date_of_birth,  # SECURITY: Use actual DOB from form
                    is_approved=False,  # SECURITY: Requires coach approval
                    completion_status='completed',
                    preferred_language=preferred_lang,
                )

                # Update invitation
                invitation.created_user = user
                invitation.status = 'accepted'
                invitation.accepted_at = timezone.now()
                invitation.save()

                # Log the user in
                user.backend = 'django.contrib.auth.backends.ModelBackend'
                login(request, user)

                logger.info(
                    f"Guest account created with age verification: {user.email} "
                    f"(age: {profile.age}) for event {invitation.event.title}"
                )

                messages.success(request,
                    _('Welcome! Your invitation has been accepted. '
                      'You will receive an email once your attendance is approved by our team.'))

                return render(request, 'crush_lu/invitations/invitation_pending_approval.html', {
                    'invitation': invitation,
                    'event': invitation.event,
                })

            except Exception as e:
                logger.error(f"Error creating guest account: {e}")
                messages.error(request, _('An error occurred while accepting your invitation. Please try again.'))
                return redirect('crush_lu:invitation_landing', code=code)
        # If form is invalid, fall through to re-render with errors
    else:
        form = InvitationAcceptanceForm(invitation=invitation)

    context = {
        'invitation': invitation,
        'event': invitation.event,
        'form': form,
    }
    return render(request, 'crush_lu/invitations/invitation_accept_form.html', context)
