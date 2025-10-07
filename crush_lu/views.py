from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import (
    CrushProfile, CrushCoach, ProfileSubmission,
    MeetupEvent, EventRegistration, CoachSession,
    EventConnection, ConnectionMessage,
    EventActivityOption, EventActivityVote, EventVotingSession,
    PresentationQueue, PresentationRating, SpeedDatingPair
)
from .forms import (
    CrushSignupForm, CrushProfileForm, ProfileReviewForm,
    CoachSessionForm, EventRegistrationForm
)
from .decorators import crush_login_required


# Authentication views
def crush_login(request):
    """Crush.lu specific login view"""
    if request.user.is_authenticated:
        return redirect('crush_lu:dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f'Welcome back, {user.first_name or user.username}!')
                # Redirect to next parameter or dashboard
                next_url = request.GET.get('next', 'crush_lu:dashboard')
                return redirect(next_url)
            else:
                messages.error(request, 'Invalid username or password.')
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = AuthenticationForm()

    return render(request, 'crush_lu/login.html', {'form': form})


def crush_logout(request):
    """Crush.lu specific logout view"""
    logout(request)
    messages.info(request, 'You have been logged out successfully.')
    return redirect('crush_lu:home')


# Public pages
def home(request):
    """Landing page"""
    upcoming_events = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        date_time__gte=timezone.now()
    )[:3]

    context = {
        'upcoming_events': upcoming_events,
    }
    return render(request, 'crush_lu/home.html', context)


def about(request):
    """About page"""
    return render(request, 'crush_lu/about.html')


def how_it_works(request):
    """How it works page"""
    return render(request, 'crush_lu/how_it_works.html')


# Onboarding
def signup(request):
    """User registration"""
    if request.method == 'POST':
        form = CrushSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, 'Account created! Please complete your profile.')
            # Log the user in
            from django.contrib.auth import login
            login(request, user)
            return redirect('crush_lu:create_profile')
    else:
        form = CrushSignupForm()

    return render(request, 'crush_lu/signup.html', {'form': form})


@crush_login_required
def create_profile(request):
    """Profile creation"""
    # Check if user is an ACTIVE coach
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
        messages.error(request, 'Coaches cannot create dating profiles. You have an active coach account.')
        return redirect('crush_lu:coach_dashboard')
    except CrushCoach.DoesNotExist:
        # Either no coach record, or coach is inactive - allow profile creation
        pass

    # Check if profile already exists
    try:
        profile = CrushProfile.objects.get(user=request.user)
        messages.info(request, 'You already have a profile. You can edit it here.')
        return redirect('crush_lu:edit_profile')
    except CrushProfile.DoesNotExist:
        pass

    if request.method == 'POST':
        form = CrushProfileForm(request.POST, request.FILES)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            # Mark profile as completed and submitted
            profile.completion_status = 'submitted'
            profile.save()

            # Create profile submission for coach review
            submission = ProfileSubmission.objects.create(profile=profile)
            submission.assign_coach()

            messages.success(request, 'Profile submitted for review!')
            return redirect('crush_lu:profile_submitted')
    else:
        form = CrushProfileForm()

    return render(request, 'crush_lu/create_profile.html', {'form': form})


@crush_login_required
def edit_profile(request):
    """Edit existing profile - uses same multi-step form as create"""
    # Check if user is an ACTIVE coach
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
        messages.error(request, 'Coaches cannot edit dating profiles. You have an active coach account.')
        return redirect('crush_lu:coach_dashboard')
    except CrushCoach.DoesNotExist:
        # Either no coach record, or coach is inactive - allow profile editing
        pass

    # Try to get existing profile, redirect to create if doesn't exist
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, 'You need to create a profile first.')
        return redirect('crush_lu:create_profile')

    # Check if profile is already submitted and pending approval
    if profile.completion_status == 'submitted':
        try:
            submission = ProfileSubmission.objects.get(profile=profile)
            # If pending or under review, redirect to status page
            if submission.status in ['pending', 'under_review']:
                messages.info(request, 'Your profile is currently under review. You\'ll be notified once it\'s approved.')
                return redirect('crush_lu:profile_submitted')
            # If rejected, allow editing
            elif submission.status == 'rejected':
                messages.warning(request, 'Your profile was not approved. Please review the feedback and update your profile.')
                # Continue to allow editing
            # If approved, they shouldn't be editing via this form
            elif submission.status == 'approved':
                messages.success(request, 'Your profile is already approved!')
                return redirect('crush_lu:dashboard')
        except ProfileSubmission.DoesNotExist:
            # Profile marked as submitted but no submission record - allow editing
            pass

    # Use the same multi-step template as create, but with editing mode
    if request.method == 'POST':
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            # Mark as submitted when completing the form
            profile.completion_status = 'submitted'
            profile.save()

            # Create or update profile submission for coach review
            submission, created = ProfileSubmission.objects.get_or_create(
                profile=profile,
                defaults={'status': 'pending'}
            )
            if created:
                submission.assign_coach()

            messages.success(request, 'Profile submitted for review!')
            return redirect('crush_lu:profile_submitted')
    else:
        form = CrushProfileForm(instance=profile)

    # When editing an existing profile, ALWAYS start from step 1
    # This allows users to review and edit all sections
    # The auto-advance logic (step1 -> step2) is only for NEW profile creation
    context = {
        'form': form,
        'profile': profile,
        'is_editing': True,
        'current_step': None  # Always start at step 1 when editing
    }
    return render(request, 'crush_lu/create_profile.html', context)


@crush_login_required
def profile_submitted(request):
    """Confirmation page after profile submission"""
    try:
        profile = CrushProfile.objects.get(user=request.user)
        submission = ProfileSubmission.objects.filter(profile=profile).latest('submitted_at')
    except (CrushProfile.DoesNotExist, ProfileSubmission.DoesNotExist):
        messages.error(request, 'No profile submission found.')
        return redirect('crush_lu:create_profile')

    context = {
        'submission': submission,
    }
    return render(request, 'crush_lu/profile_submitted.html', context)


# User dashboard
@crush_login_required
def dashboard(request):
    """User dashboard - redirects ACTIVE coaches to their dashboard"""
    # Check if user is an ACTIVE coach
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
        return redirect('crush_lu:coach_dashboard')
    except CrushCoach.DoesNotExist:
        # Either no coach record, or coach is inactive - show dating dashboard
        pass

    # Regular user dashboard
    try:
        profile = CrushProfile.objects.get(user=request.user)
        # Get latest submission status
        latest_submission = ProfileSubmission.objects.filter(
            profile=profile
        ).order_by('-submitted_at').first()

        # Get user's event registrations
        registrations = EventRegistration.objects.filter(
            user=request.user
        ).select_related('event').order_by('-event__date_time')

        # Get connection count
        connection_count = EventConnection.objects.filter(
            Q(requester=request.user) | Q(recipient=request.user),
            status__in=['accepted', 'coach_reviewing', 'coach_approved', 'shared']
        ).count()

        context = {
            'profile': profile,
            'submission': latest_submission,
            'registrations': registrations,
            'connection_count': connection_count,
        }
    except CrushProfile.DoesNotExist:
        messages.warning(request, 'Please complete your profile first.')
        return redirect('crush_lu:create_profile')

    return render(request, 'crush_lu/dashboard.html', context)


# Events
def event_list(request):
    """List of upcoming events"""
    events = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        date_time__gte=timezone.now()
    ).order_by('date_time')

    # Filter by event type if provided
    event_type = request.GET.get('type')
    if event_type:
        events = events.filter(event_type=event_type)

    # For coaches: show unpublished events count
    unpublished_count = 0
    if request.user.is_authenticated:
        try:
            coach = CrushCoach.objects.get(user=request.user, is_active=True)
            unpublished_count = MeetupEvent.objects.filter(
                is_published=False,
                is_cancelled=False,
                date_time__gte=timezone.now()
            ).count()
        except CrushCoach.DoesNotExist:
            pass

    context = {
        'events': events,
        'event_types': MeetupEvent.EVENT_TYPE_CHOICES,
        'unpublished_count': unpublished_count,
    }
    return render(request, 'crush_lu/event_list.html', context)


def event_detail(request, event_id):
    """Event detail page"""
    event = get_object_or_404(MeetupEvent, id=event_id, is_published=True)

    # Check if user is registered
    user_registration = None
    if request.user.is_authenticated:
        user_registration = EventRegistration.objects.filter(
            event=event,
            user=request.user
        ).first()

    context = {
        'event': event,
        'user_registration': user_registration,
    }
    return render(request, 'crush_lu/event_detail.html', context)


@crush_login_required
def event_register(request, event_id):
    """Register for an event"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Check if profile is approved
    try:
        profile = CrushProfile.objects.get(user=request.user)
        if not profile.is_approved:
            messages.error(request, 'Your profile must be approved before registering for events.')
            return redirect('crush_lu:event_detail', event_id=event_id)
    except CrushProfile.DoesNotExist:
        messages.error(request, 'Please create a profile first.')
        return redirect('crush_lu:create_profile')

    # Check if already registered
    if EventRegistration.objects.filter(event=event, user=request.user).exists():
        messages.warning(request, 'You are already registered for this event.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Check if registration is open
    if not event.is_registration_open:
        messages.error(request, 'Registration is not available for this event.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    if request.method == 'POST':
        form = EventRegistrationForm(request.POST)
        if form.is_valid():
            registration = form.save(commit=False)
            registration.event = event
            registration.user = request.user

            # Set status based on availability
            if event.is_full:
                registration.status = 'waitlist'
                messages.info(request, 'Event is full. You have been added to the waitlist.')
            else:
                registration.status = 'confirmed'
                messages.success(request, 'Successfully registered for the event!')

            registration.save()
            return redirect('crush_lu:dashboard')
    else:
        form = EventRegistrationForm()

    context = {
        'event': event,
        'form': form,
    }
    return render(request, 'crush_lu/event_register.html', context)


@crush_login_required
def event_cancel(request, event_id):
    """Cancel event registration"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if request.method == 'POST':
        registration.status = 'cancelled'
        registration.save()
        messages.success(request, 'Your registration has been cancelled.')
        return redirect('crush_lu:dashboard')

    context = {
        'event': event,
        'registration': registration,
    }
    return render(request, 'crush_lu/event_cancel.html', context)


# Coach views
@crush_login_required
def coach_dashboard(request):
    """Coach dashboard for reviewing profiles"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    # Get pending submissions assigned to this coach
    pending_submissions = ProfileSubmission.objects.filter(
        coach=coach,
        status='pending'
    ).select_related('profile__user')

    # Get recently reviewed
    recent_reviews = ProfileSubmission.objects.filter(
        coach=coach,
        status__in=['approved', 'rejected', 'revision']
    ).select_related('profile__user').order_by('-reviewed_at')[:10]

    context = {
        'coach': coach,
        'pending_submissions': pending_submissions,
        'recent_reviews': recent_reviews,
    }
    return render(request, 'crush_lu/coach_dashboard.html', context)


@crush_login_required
def coach_review_profile(request, submission_id):
    """Review a profile submission"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    submission = get_object_or_404(
        ProfileSubmission,
        id=submission_id,
        coach=coach
    )

    if request.method == 'POST':
        form = ProfileReviewForm(request.POST, instance=submission)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.reviewed_at = timezone.now()

            # Update profile approval status
            if submission.status == 'approved':
                submission.profile.is_approved = True
                submission.profile.approved_at = timezone.now()
                submission.profile.save()
                messages.success(request, 'Profile approved!')
            elif submission.status == 'rejected':
                submission.profile.is_approved = False
                submission.profile.save()
                messages.info(request, 'Profile rejected.')
            elif submission.status == 'revision':
                messages.info(request, 'Revision requested.')

            submission.save()
            return redirect('crush_lu:coach_dashboard')
    else:
        form = ProfileReviewForm(instance=submission)

    context = {
        'submission': submission,
        'profile': submission.profile,
        'form': form,
    }
    return render(request, 'crush_lu/coach_review_profile.html', context)


@crush_login_required
def coach_sessions(request):
    """View and manage coach sessions"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    sessions = CoachSession.objects.filter(coach=coach).order_by('-created_at')

    context = {
        'coach': coach,
        'sessions': sessions,
    }
    return render(request, 'crush_lu/coach_sessions.html', context)


# Post-Event Connection Views
@crush_login_required
def event_attendees(request, event_id):
    """Show attendees after user has attended event - allows connection requests"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user attended this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if not user_registration.can_make_connections:
        messages.error(request, 'You must attend this event before making connections.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get other attendees (status='attended')
    attendees = EventRegistration.objects.filter(
        event=event,
        status='attended'
    ).exclude(user=request.user).select_related('user__crushprofile')

    # Get user's existing connection requests for this event
    sent_requests = EventConnection.objects.filter(
        requester=request.user,
        event=event
    ).values_list('recipient_id', flat=True)

    received_requests = EventConnection.objects.filter(
        recipient=request.user,
        event=event
    ).values_list('requester_id', flat=True)

    # Annotate attendees with connection status
    attendee_data = []
    for reg in attendees:
        attendee_user = reg.user
        connection_status = None
        connection_id = None

        if attendee_user.id in sent_requests:
            connection = EventConnection.objects.get(
                requester=request.user,
                recipient=attendee_user,
                event=event
            )
            connection_status = 'sent'
            connection_id = connection.id
        elif attendee_user.id in received_requests:
            connection = EventConnection.objects.get(
                requester=attendee_user,
                recipient=request.user,
                event=event
            )
            connection_status = 'received'
            connection_id = connection.id

        attendee_data.append({
            'user': attendee_user,
            'profile': getattr(attendee_user, 'crushprofile', None),
            'connection_status': connection_status,
            'connection_id': connection_id,
        })

    context = {
        'event': event,
        'attendees': attendee_data,
    }
    return render(request, 'crush_lu/event_attendees.html', context)


@crush_login_required
def request_connection(request, event_id, user_id):
    """Request connection with another event attendee"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    recipient = get_object_or_404(CrushProfile, user_id=user_id).user

    # Verify requester attended the event
    requester_reg = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if not requester_reg.can_make_connections:
        messages.error(request, 'You must attend this event before making connections.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Verify recipient attended the event
    recipient_reg = get_object_or_404(
        EventRegistration,
        event=event,
        user=recipient
    )

    if not recipient_reg.can_make_connections:
        messages.error(request, 'This person did not attend the event.')
        return redirect('crush_lu:event_attendees', event_id=event_id)

    # Check if connection already exists
    existing = EventConnection.objects.filter(
        Q(requester=request.user, recipient=recipient, event=event) |
        Q(requester=recipient, recipient=request.user, event=event)
    ).first()

    if existing:
        messages.warning(request, 'Connection request already exists.')
        return redirect('crush_lu:event_attendees', event_id=event_id)

    if request.method == 'POST':
        note = request.POST.get('note', '').strip()

        # Create connection request
        connection = EventConnection.objects.create(
            requester=request.user,
            recipient=recipient,
            event=event,
            requester_note=note
        )

        # Check if this is mutual (recipient already requested requester)
        reverse_connection = EventConnection.objects.filter(
            requester=recipient,
            recipient=request.user,
            event=event
        ).first()

        if reverse_connection:
            # Mutual interest! Both move to accepted
            connection.status = 'accepted'
            connection.save()
            reverse_connection.status = 'accepted'
            reverse_connection.save()

            # Assign coach to facilitate
            connection.assign_coach()
            reverse_connection.assigned_coach = connection.assigned_coach
            reverse_connection.save()

            messages.success(request, f'Mutual connection! 🎉 A coach will help facilitate your introduction.')
        else:
            messages.success(request, 'Connection request sent!')

        return redirect('crush_lu:event_attendees', event_id=event_id)

    context = {
        'event': event,
        'recipient': recipient,
    }
    return render(request, 'crush_lu/request_connection.html', context)


@crush_login_required
def respond_connection(request, connection_id, action):
    """Accept or decline a connection request"""
    connection = get_object_or_404(
        EventConnection,
        id=connection_id,
        recipient=request.user,
        status='pending'
    )

    if action == 'accept':
        connection.status = 'accepted'
        connection.save()

        # Assign coach
        connection.assign_coach()

        messages.success(request, 'Connection accepted! A coach will help facilitate your introduction.')
    elif action == 'decline':
        connection.status = 'declined'
        connection.save()
        messages.info(request, 'Connection request declined.')
    else:
        messages.error(request, 'Invalid action.')

    return redirect('crush_lu:my_connections')


@crush_login_required
def my_connections(request):
    """View all connections (sent, received, active)"""
    # Sent requests
    sent = EventConnection.objects.filter(
        requester=request.user
    ).select_related('recipient__crushprofile', 'event', 'assigned_coach').order_by('-requested_at')

    # Received requests (pending only)
    received_pending = EventConnection.objects.filter(
        recipient=request.user,
        status='pending'
    ).select_related('requester__crushprofile', 'event').order_by('-requested_at')

    # Active connections (accepted, coach_reviewing, coach_approved, shared)
    active = EventConnection.objects.filter(
        Q(requester=request.user) | Q(recipient=request.user),
        status__in=['accepted', 'coach_reviewing', 'coach_approved', 'shared']
    ).select_related('requester__crushprofile', 'recipient__crushprofile', 'event', 'assigned_coach').order_by('-requested_at')

    context = {
        'sent_requests': sent,
        'received_requests': received_pending,
        'active_connections': active,
    }
    return render(request, 'crush_lu/my_connections.html', context)


@crush_login_required
def connection_detail(request, connection_id):
    """View connection details and provide consent"""
    connection = get_object_or_404(
        EventConnection,
        Q(requester=request.user) | Q(recipient=request.user),
        id=connection_id
    )

    # Determine if current user is requester or recipient
    is_requester = (connection.requester == request.user)

    if request.method == 'POST':
        # Handle consent
        if 'consent' in request.POST:
            consent_value = request.POST.get('consent') == 'yes'

            if is_requester:
                connection.requester_consents_to_share = consent_value
            else:
                connection.recipient_consents_to_share = consent_value

            connection.save()

            # Check if both consented and coach approved
            if connection.can_share_contacts:
                connection.status = 'shared'
                connection.save()
                messages.success(request, 'Contact information is now shared! 🎉')
            else:
                messages.success(request, 'Your consent has been recorded.')

            return redirect('crush_lu:connection_detail', connection_id=connection_id)

    # Get the other person in the connection
    other_user = connection.recipient if is_requester else connection.requester

    # Get messages for this connection
    connection_messages = ConnectionMessage.objects.filter(
        connection=connection
    ).select_related('sender').order_by('sent_at')

    context = {
        'connection': connection,
        'is_requester': is_requester,
        'other_user': other_user,
        'other_profile': getattr(other_user, 'crushprofile', None),
        'messages': connection_messages,
    }
    return render(request, 'crush_lu/connection_detail.html', context)


# Event Activity Voting Views
@crush_login_required
def event_voting_lobby(request, event_id):
    """Pre-voting lobby with countdown and activity previews"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    # Only confirmed or attended registrations can vote
    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can access event voting.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get or create voting session
    voting_session, created = EventVotingSession.objects.get_or_create(event=event)

    # Get all activity options
    activity_options = EventActivityOption.objects.filter(event=event).order_by('activity_type', 'activity_variant')

    # Check if user has already voted
    user_vote = EventActivityVote.objects.filter(event=event, user=request.user).first()

    # Get total confirmed attendees count
    total_attendees = EventRegistration.objects.filter(
        event=event,
        status__in=['confirmed', 'attended']
    ).count()

    context = {
        'event': event,
        'voting_session': voting_session,
        'activity_options': activity_options,
        'user_vote': user_vote,
        'total_attendees': total_attendees,
    }
    return render(request, 'crush_lu/event_voting_lobby.html', context)


@crush_login_required
def event_activity_vote(request, event_id):
    """Active voting interface"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can vote.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if voting is open
    if not voting_session.is_voting_open:
        messages.warning(request, 'Voting is not currently open for this event.')
        return redirect('crush_lu:event_voting_lobby', event_id=event_id)

    if request.method == 'POST':
        presentation_option_id = request.POST.get('presentation_option_id')
        twist_option_id = request.POST.get('twist_option_id')

        if not presentation_option_id or not twist_option_id:
            messages.error(request, 'Please vote on BOTH categories: Presentation Style AND Speed Dating Twist.')
        else:
            try:
                from .models import GlobalActivityOption

                presentation_option = GlobalActivityOption.objects.get(
                    id=presentation_option_id,
                    activity_type='presentation_style',
                    is_active=True
                )
                twist_option = GlobalActivityOption.objects.get(
                    id=twist_option_id,
                    activity_type='speed_dating_twist',
                    is_active=True
                )

                # Handle presentation style vote
                presentation_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type='presentation_style'
                ).first()

                if presentation_vote:
                    # Update existing vote
                    presentation_vote.selected_option = presentation_option
                    presentation_vote.save()
                else:
                    # Create new vote
                    EventActivityVote.objects.create(
                        event=event,
                        user=request.user,
                        selected_option=presentation_option
                    )

                # Handle speed dating twist vote
                twist_vote = EventActivityVote.objects.filter(
                    event=event,
                    user=request.user,
                    selected_option__activity_type='speed_dating_twist'
                ).first()

                if twist_vote:
                    # Update existing vote
                    twist_vote.selected_option = twist_option
                    twist_vote.save()
                else:
                    # Create new vote
                    EventActivityVote.objects.create(
                        event=event,
                        user=request.user,
                        selected_option=twist_option
                    )

                # Update total votes only if this is first complete vote
                if not (presentation_vote and twist_vote):
                    voting_session.total_votes += 1
                    voting_session.save()

                messages.success(request, 'Your votes have been recorded for both categories!')
                return redirect('crush_lu:event_voting_results', event_id=event_id)

            except GlobalActivityOption.DoesNotExist:
                messages.error(request, 'Invalid activity option selected.')

    # Get all GLOBAL activity options (not per-event anymore!)
    from .models import GlobalActivityOption
    presentation_style_options = GlobalActivityOption.objects.filter(
        activity_type='presentation_style',
        is_active=True
    ).order_by('sort_order')

    speed_dating_twist_options = GlobalActivityOption.objects.filter(
        activity_type='speed_dating_twist',
        is_active=True
    ).order_by('sort_order')

    # Check if user has voted on BOTH categories
    presentation_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='presentation_style'
    ).first()

    twist_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='speed_dating_twist'
    ).first()

    context = {
        'event': event,
        'voting_session': voting_session,
        'presentation_style_options': presentation_style_options,
        'speed_dating_twist_options': speed_dating_twist_options,
        'presentation_vote': presentation_vote,
        'twist_vote': twist_vote,
        'has_voted_both': presentation_vote and twist_vote,
    }
    return render(request, 'crush_lu/event_activity_vote.html', context)


@crush_login_required
def event_voting_results(request, event_id):
    """Display voting results and transition to presentations when ready"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can view results.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get voting session
    voting_session = get_object_or_404(EventVotingSession, event=event)

    # Check if user has voted on BOTH categories
    presentation_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='presentation_style'
    ).first()

    twist_vote = EventActivityVote.objects.filter(
        event=event,
        user=request.user,
        selected_option__activity_type='speed_dating_twist'
    ).first()

    user_has_voted_both = presentation_vote and twist_vote

    # If voting ended and user hasn't voted, redirect back to voting with message
    if not voting_session.is_voting_open and not user_has_voted_both:
        messages.warning(request, 'Voting has ended. You did not vote, but you can still participate in presentations!')
        # Allow them to continue to presentations anyway
        return redirect('crush_lu:event_presentations', event_id=event_id)

    # Get vote counts for each GlobalActivityOption
    from .models import GlobalActivityOption
    from django.db.models import Count

    # Get all active global options with their vote counts for THIS event
    activity_options_with_votes = []

    for option in GlobalActivityOption.objects.filter(is_active=True).order_by('activity_type', 'sort_order'):
        vote_count = EventActivityVote.objects.filter(
            event=event,
            selected_option=option
        ).count()

        # Add vote_count attribute for template compatibility
        option.vote_count = vote_count
        option.is_winner = False  # Will be set below
        activity_options_with_votes.append(option)

    # Calculate total votes
    total_votes = voting_session.total_votes

    # If voting has ended, calculate winners and initialize presentation queue
    if not voting_session.is_voting_open:
        # Calculate winners if not already done
        if not voting_session.winning_presentation_style:
            voting_session.calculate_winner()
            voting_session.initialize_presentation_queue()
            voting_session.save()

        # Mark winners in the activity_options list
        for option in activity_options_with_votes:
            if option == voting_session.winning_presentation_style or option == voting_session.winning_speed_dating_twist:
                option.is_winner = True

        # Check if presentations have started
        has_presentations = PresentationQueue.objects.filter(event=event).exists()

        if has_presentations:
            # Automatically redirect to presentations after 5 seconds
            messages.success(request, 'Voting complete! Redirecting to presentations...')
            context = {
                'event': event,
                'voting_session': voting_session,
                'activity_options': activity_options_with_votes,
                'user_has_voted_both': user_has_voted_both,
                'total_votes': total_votes,
                'redirect_to_presentations': True,  # Signal to template
            }
            return render(request, 'crush_lu/event_voting_results.html', context)

    context = {
        'event': event,
        'voting_session': voting_session,
        'activity_options': activity_options_with_votes,
        'user_has_voted_both': user_has_voted_both,
        'total_votes': total_votes,
        'redirect_to_presentations': False,
    }
    return render(request, 'crush_lu/event_voting_results.html', context)


# Presentation Round Views (Phase 2)
@crush_login_required
def event_presentations(request, event_id):
    """Phase 2: Live presentation view - shows current presenter and allows rating"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can view presentations.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get the current presenter (status='presenting')
    current_presentation = PresentationQueue.objects.filter(
        event=event,
        status='presenting'
    ).select_related('user__crushprofile').first()

    # Get next presenter (for preview)
    next_presentation = PresentationQueue.objects.filter(
        event=event,
        status='waiting'
    ).order_by('presentation_order').first()

    # Get total presentation stats
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(event=event, status='completed').count()

    # Check if user has rated current presenter
    user_has_rated = False
    if current_presentation:
        user_has_rated = PresentationRating.objects.filter(
            event=event,
            presenter=current_presentation.user,
            rater=request.user
        ).exists()

    # Get the winning presentation style from voting session
    winning_style = voting_session.winning_presentation_style

    context = {
        'event': event,
        'current_presentation': current_presentation,
        'next_presentation': next_presentation,
        'total_presentations': total_presentations,
        'completed_presentations': completed_presentations,
        'user_has_rated': user_has_rated,
        'winning_style': winning_style,
        'is_presenting': current_presentation and current_presentation.user == request.user,
    }
    return render(request, 'crush_lu/event_presentations.html', context)


@crush_login_required
@require_http_methods(["POST"])
def submit_presentation_rating(request, event_id, presenter_id):
    """Submit anonymous 1-5 star rating for a presenter"""
    event = get_object_or_404(MeetupEvent, id=event_id)
    presenter = get_object_or_404(User, id=presenter_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        return JsonResponse({'success': False, 'error': 'Only confirmed attendees can rate.'}, status=403)

    # Cannot rate yourself
    if presenter == request.user:
        return JsonResponse({'success': False, 'error': 'You cannot rate yourself.'}, status=400)

    # Get rating from request
    rating_value = request.POST.get('rating')

    try:
        rating_value = int(rating_value)
        if rating_value < 1 or rating_value > 5:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Rating must be between 1 and 5.'}, status=400)

    # Create or update rating
    rating, created = PresentationRating.objects.update_or_create(
        event=event,
        presenter=presenter,
        rater=request.user,
        defaults={'rating': rating_value}
    )

    return JsonResponse({
        'success': True,
        'message': 'Rating submitted anonymously!' if created else 'Rating updated!',
        'rating': rating_value
    })


# Coach Presentation Control Panel
@crush_login_required
def coach_presentation_control(request, event_id):
    """Coach control panel for managing presentation queue"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is a coach
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'Only coaches can access presentation controls.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Get all presentations
    presentations = PresentationQueue.objects.filter(
        event=event
    ).select_related('user__crushprofile').order_by('presentation_order')

    # Get current presenter
    current_presentation = presentations.filter(status='presenting').first()

    # Get next presenter
    next_presentation = presentations.filter(status='waiting').order_by('presentation_order').first()

    # Get stats
    total_presentations = presentations.count()
    completed_presentations = presentations.filter(status='completed').count()

    # Get winning presentation style from voting session
    winning_style = voting_session.winning_presentation_style

    context = {
        'event': event,
        'presentations': presentations,
        'current_presentation': current_presentation,
        'next_presentation': next_presentation,
        'total_presentations': total_presentations,
        'completed_presentations': completed_presentations,
        'winning_style': winning_style,
    }
    return render(request, 'crush_lu/coach_presentation_control.html', context)


@crush_login_required
@require_http_methods(["POST"])
def coach_advance_presentation(request, event_id):
    """Advance to next presenter in the queue"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is a coach
    try:
        coach = CrushCoach.objects.get(user=request.user)
    except CrushCoach.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Only coaches can advance presentations.'}, status=403)

    # End current presentation if exists
    current_presentation = PresentationQueue.objects.filter(
        event=event,
        status='presenting'
    ).first()

    if current_presentation:
        current_presentation.status = 'completed'
        current_presentation.completed_at = timezone.now()
        current_presentation.save()

    # Start next presentation
    next_presentation = PresentationQueue.objects.filter(
        event=event,
        status='waiting'
    ).order_by('presentation_order').first()

    if next_presentation:
        next_presentation.status = 'presenting'
        next_presentation.started_at = timezone.now()
        next_presentation.save()

        return JsonResponse({
            'success': True,
            'message': f'Now presenting: {next_presentation.user.crushprofile.display_name}',
            'presenter_name': next_presentation.user.crushprofile.display_name,
            'presentation_order': next_presentation.presentation_order
        })
    else:
        return JsonResponse({
            'success': True,
            'message': 'All presentations completed!',
            'all_completed': True
        })


@crush_login_required
def my_presentation_scores(request, event_id):
    """Show user their personal presentation scores (private view)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    user_registration = get_object_or_404(
        EventRegistration,
        event=event,
        user=request.user
    )

    if user_registration.status not in ['confirmed', 'attended']:
        messages.error(request, 'Only confirmed attendees can view scores.')
        return redirect('crush_lu:event_detail', event_id=event_id)

    # Check if all presentations are completed
    total_presentations = PresentationQueue.objects.filter(event=event).count()
    completed_presentations = PresentationQueue.objects.filter(event=event, status='completed').count()

    all_completed = total_presentations > 0 and completed_presentations == total_presentations

    if not all_completed:
        messages.warning(request, 'Scores will be available after all presentations are completed.')
        return redirect('crush_lu:event_presentations', event_id=event_id)

    # Get ratings received by this user
    ratings_received = PresentationRating.objects.filter(
        event=event,
        presenter=request.user
    ).select_related('rater__crushprofile')

    # Calculate average score
    if ratings_received.exists():
        total_score = sum(r.rating for r in ratings_received)
        average_score = total_score / ratings_received.count()
        rating_count = ratings_received.count()
    else:
        average_score = 0
        rating_count = 0

    # Get individual ratings (without showing who rated)
    individual_ratings = [r.rating for r in ratings_received]
    individual_ratings.sort(reverse=True)  # Sort highest to lowest

    # Calculate rating distribution
    rating_distribution = {
        5: ratings_received.filter(rating=5).count(),
        4: ratings_received.filter(rating=4).count(),
        3: ratings_received.filter(rating=3).count(),
        2: ratings_received.filter(rating=2).count(),
        1: ratings_received.filter(rating=1).count(),
    }

    # Get user's rank among all participants
    from django.db.models import Avg, Count
    all_participants = PresentationQueue.objects.filter(event=event).values_list('user_id', flat=True)

    participant_scores = []
    for participant_id in all_participants:
        participant_ratings = PresentationRating.objects.filter(
            event=event,
            presenter_id=participant_id
        )
        if participant_ratings.exists():
            avg = participant_ratings.aggregate(Avg('rating'))['rating__avg']
            participant_scores.append((participant_id, avg))

    # Sort by average score (highest first)
    participant_scores.sort(key=lambda x: x[1], reverse=True)

    # Find user's rank
    user_rank = None
    for idx, (participant_id, score) in enumerate(participant_scores, start=1):
        if participant_id == request.user.id:
            user_rank = idx
            break

    context = {
        'event': event,
        'average_score': average_score,
        'rating_count': rating_count,
        'individual_ratings': individual_ratings,
        'rating_distribution': rating_distribution,
        'user_rank': user_rank,
        'total_participants': len(participant_scores),
    }
    return render(request, 'crush_lu/my_presentation_scores.html', context)


@crush_login_required
def get_current_presenter_api(request, event_id):
    """API endpoint to get current presenter status (for polling)"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user is registered for this event
    try:
        user_registration = EventRegistration.objects.get(
            event=event,
            user=request.user
        )
        if user_registration.status not in ['confirmed', 'attended']:
            return JsonResponse({'error': 'Not authorized'}, status=403)
    except EventRegistration.DoesNotExist:
        return JsonResponse({'error': 'Not registered for this event'}, status=403)

    # Get current presenter
    current_presentation = PresentationQueue.objects.filter(
        event=event,
        status='presenting'
    ).select_related('user__crushprofile').first()

    if current_presentation:
        # Check if user has rated this presenter
        user_has_rated = PresentationRating.objects.filter(
            event=event,
            presenter=current_presentation.user,
            rater=request.user
        ).exists()

        # Calculate time remaining
        time_remaining = 90
        if current_presentation.started_at:
            from django.utils import timezone
            elapsed = (timezone.now() - current_presentation.started_at).total_seconds()
            time_remaining = max(0, int(90 - elapsed))

        return JsonResponse({
            'has_presenter': True,
            'presenter_id': current_presentation.user.id,
            'presenter_name': current_presentation.user.crushprofile.display_name,
            'presentation_order': current_presentation.presentation_order,
            'started_at': current_presentation.started_at.isoformat() if current_presentation.started_at else None,
            'time_remaining': time_remaining,
            'user_has_rated': user_has_rated,
            'is_presenting': current_presentation.user == request.user
        })
    else:
        # Check if all presentations are completed
        total_presentations = PresentationQueue.objects.filter(event=event).count()
        completed_presentations = PresentationQueue.objects.filter(event=event, status='completed').count()

        return JsonResponse({
            'has_presenter': False,
            'all_completed': total_presentations > 0 and completed_presentations == total_presentations,
            'completed_count': completed_presentations,
            'total_count': total_presentations
        })


# Demo/Guided Tour View
def voting_demo(request):
    """Interactive demo of the voting system for new users"""
    return render(request, 'crush_lu/voting_demo.html')
