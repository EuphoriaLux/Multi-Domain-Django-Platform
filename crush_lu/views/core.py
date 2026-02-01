"""
Core views for Crush.lu

This module contains only essential utility views:
- dashboard: Central user hub
- special_welcome: Special user experience for VIP users
- luxid_mockup_view: LuxID integration demonstration (stakeholder presentations)
- luxid_auth_mockup_view: LuxID authentication mockup (stakeholder presentations)

All other views have been split into domain-specific modules:
- views_public.py: Public pages (home, about, privacy, terms, membership)
- views_pwa.py: PWA infrastructure (service worker, manifest, offline)
- views_invitations.py: Private invitation system
- views_account.py: Account management (settings, password, deletion, GDPR)
- views_onboarding.py: User registration and profile creation
- views_events.py: Event lifecycle, voting, speed-dating presentations
- views_connections.py: Post-event connections and messaging
- views_coach.py: Coach dashboard, profile review, journey management
- views_profile.py: Profile editing and creation
- views_journey.py: Interactive journey system
- views_advent.py: Advent calendar
- views_phone_verification.py: Phone number verification
- views_media.py: Secure media serving
- views_oauth_popup.py: OAuth popup flows
- views_wallet.py: Wallet passes (Apple/Google)
- views_seo.py: SEO utilities
"""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.db.models import Q
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)

from ..models import (
    CrushProfile, CrushCoach, ProfileSubmission,
    EventRegistration, EventConnection, UserActivity,
    SpecialUserExperience,
)
from ..decorators import crush_login_required


def luxid_mockup_view(request):
    """Mockup view for LuxID integration demonstration (NOT PRODUCTION)

    This view displays a visual mockup of how the profile submission page would
    look for users who authenticate via LuxID. It demonstrates the value proposition
    of LuxID integration: skipping the screening call and faster approval times.

    This is for stakeholder presentations and negotiations only.

    Access restrictions:
    - Available on: localhost, test.crush.lu (staging)
    - Blocked on: crush.lu (production)
    """
    from django.http import Http404
    from django.conf import settings

    # Check if we're on production (not DEBUG, not test.* subdomain)
    host = request.META.get('HTTP_HOST', '').split(':')[0].lower()
    is_staging = host.startswith('test.')
    is_development = settings.DEBUG or host in ['localhost', '127.0.0.1']

    # Block access on production
    if not is_development and not is_staging:
        raise Http404("This mockup is only available on staging and development environments")

    # Create sample context data for the mockup
    context = {
        'submission': {
            'status': 'pending',
            'submitted_at': timezone.now() - timedelta(hours=2),
            'coach': None,
            'get_status_display': lambda: _('Pending Review'),
        }
    }
    return render(request, 'crush_lu/onboarding/profile_submitted_luxid_mockup.html', context)


def luxid_auth_mockup_view(request):
    """Mockup view for LuxID login/signup integration (NOT PRODUCTION)

    This view displays a visual mockup of the login/signup page with LuxID
    as an authentication provider. It demonstrates how LuxID would appear
    alongside existing social login options (Google, Facebook, Microsoft).

    Key features shown:
    - LuxID button with Fast Track badge
    - Benefits callout (government-verified, skip screening, faster approval)
    - LuxID branding (rainbow gradient)
    - Hero banner highlighting LuxID integration

    This is for stakeholder presentations and negotiations only.

    Access restrictions:
    - Available on: localhost, test.crush.lu (staging)
    - Blocked on: crush.lu (production)
    """
    from django.http import Http404
    from django.conf import settings
    from ..forms import CrushSignupForm
    from allauth.account.forms import LoginForm

    # Check if we're on production (not DEBUG, not test.* subdomain)
    host = request.META.get('HTTP_HOST', '').split(':')[0].lower()
    is_staging = host.startswith('test.')
    is_development = settings.DEBUG or host in ['localhost', '127.0.0.1']

    # Block access on production
    if not is_development and not is_staging:
        raise Http404("This mockup is only available on staging and development environments")

    # Create context data for the mockup (similar to UnifiedAuthView)
    context = {
        'signup_form': CrushSignupForm(),
        'login_form': LoginForm(),
        'mode': request.GET.get('mode', 'login'),  # Allow switching via ?mode=signup
    }
    return render(request, 'crush_lu/onboarding/auth_luxid_mockup.html', context)


# User dashboard
@crush_login_required
def dashboard(request):
    """User dashboard - redirects ACTIVE coaches to their dashboard unless ?user_view=1"""
    # Check if user is an ACTIVE coach
    # Allow coaches to view their user dashboard via ?user_view=1 parameter
    user_view = request.GET.get('user_view') == '1'
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
        if not user_view:
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

        # Check PWA status from UserActivity model (not CrushProfile)
        is_pwa_user = False
        try:
            activity = UserActivity.objects.filter(user=request.user).first()
            if activity:
                is_pwa_user = activity.is_pwa_user
        except Exception:
            pass
        # Get or create referral code for this user's profile
        from ..models import ReferralCode
        from ..referrals import build_referral_url
        referral_code = ReferralCode.get_or_create_for_profile(profile)
        referral_url = build_referral_url(referral_code.code, request=request)

        context = {
            'profile': profile,
            'submission': latest_submission,
            'registrations': registrations,
            'connection_count': connection_count,
            'is_pwa_user': is_pwa_user,
            'referral_url': referral_url,
        }
    except CrushProfile.DoesNotExist:
        messages.warning(request, _('Please complete your profile first.'))
        return redirect('crush_lu:create_profile')

    return render(request, 'crush_lu/profile/dashboard.html', context)


def special_welcome(request):
    """
    Special welcome page for VIP users with custom animations and experience.
    Only accessible if special_experience_active is set in session.

    If a journey is configured for this user, redirect to journey_map instead.
    """
    # Check if special experience is active in session
    if not request.session.get('special_experience_active'):
        messages.warning(request, _('This page is not available.'))
        # Redirect to home instead of dashboard (special users don't need profiles)
        return redirect('crush_lu:home')

    # Get special experience data from session
    special_experience_data = request.session.get('special_experience_data', {})

    # Get the full SpecialUserExperience object for complete data
    special_experience_id = request.session.get('special_experience_id')
    try:
        special_experience = SpecialUserExperience.objects.get(
            id=special_experience_id,
            is_active=True
        )
    except SpecialUserExperience.DoesNotExist:
        # Clear session data if experience is not found or inactive
        request.session.pop('special_experience_active', None)
        request.session.pop('special_experience_id', None)
        request.session.pop('special_experience_data', None)
        messages.warning(request, _('This special experience is no longer available.'))
        return redirect('crush_lu:home')

    # ============================================================================
    # NEW: Check if journey is configured - redirect to appropriate journey type
    # ============================================================================
    from ..models import JourneyConfiguration

    # First check for Wonderland journey
    wonderland_journey = JourneyConfiguration.objects.filter(
        special_experience=special_experience,
        journey_type='wonderland',
        is_active=True
    ).first()

    if wonderland_journey:
        logger.info(f"🎮 Redirecting {request.user.username} to Wonderland journey: {wonderland_journey.journey_name}")
        return redirect('crush_lu:journey_map')

    # Check for Advent Calendar journey
    advent_journey = JourneyConfiguration.objects.filter(
        special_experience=special_experience,
        journey_type='advent_calendar',
        is_active=True
    ).first()

    if advent_journey:
        logger.info(f"🎄 Redirecting {request.user.username} to Advent Calendar")
        return redirect('crush_lu:advent_calendar')

    # No journey configured - show simple welcome page
    # ============================================================================

    context = {
        'special_experience': special_experience,
    }

    # Mark session as viewed (only show once per login)
    request.session['special_experience_viewed'] = True

    return render(request, 'crush_lu/special/special_welcome.html', context)
