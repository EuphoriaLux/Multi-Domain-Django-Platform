"""
Public-facing views for Crush.lu

Landing pages, legal pages, and marketing content.
"""
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import MeetupEvent, CrushProfile, UserActivity


def home(request):
    """Landing page - redirects authenticated users to dashboard"""
    # If user is logged in, redirect to their dashboard
    if request.user.is_authenticated:
        return redirect('crush_lu:dashboard')

    upcoming_events = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        date_time__gte=timezone.now()
    )[:3]

    context = {
        'upcoming_events': upcoming_events,
    }
    return render(request, 'crush_lu/public/home.html', context)


def about(request):
    """About page"""
    return render(request, 'crush_lu/public/about.html')


def how_it_works(request):
    """How it works page"""
    return render(request, 'crush_lu/public/how_it_works.html')


def privacy_policy(request):
    """Privacy policy page"""
    return render(request, 'crush_lu/public/privacy_policy.html')


def terms_of_service(request):
    """Terms of service page"""
    return render(request, 'crush_lu/public/terms_of_service.html')


def membership(request):
    """
    Membership program landing page.
    Public access for viewing, login required for wallet actions.
    Explains membership tiers, how to earn points, and PWA benefits.
    """
    is_pwa_user = False
    profile = None
    referral_url = None

    if request.user.is_authenticated:
        # Check if user has installed PWA
        try:
            activity = UserActivity.objects.get(user=request.user)
            is_pwa_user = activity.is_pwa_user
        except UserActivity.DoesNotExist:
            pass

        # Get profile and referral URL if available
        try:
            profile = CrushProfile.objects.get(user=request.user)
            from ..models import ReferralCode
            from ..referrals import build_referral_url
            referral_code = ReferralCode.get_or_create_for_profile(profile)
            referral_url = build_referral_url(referral_code.code, request=request)
        except CrushProfile.DoesNotExist:
            pass

    # Membership tier data
    tiers = [
        {
            'name': _('Basic'),
            'key': 'basic',
            'points': 0,
            'emoji': '💜',
            'benefits': [
                _('Access to public events'),
                _('Basic profile features'),
                _('Connection messaging'),
            ]
        },
        {
            'name': _('Bronze'),
            'key': 'bronze',
            'points': 100,
            'emoji': '🥉',
            'benefits': [
                _('All Basic benefits'),
                _('Priority event registration'),
                _('Profile badge'),
            ]
        },
        {
            'name': _('Silver'),
            'key': 'silver',
            'points': 500,
            'emoji': '🥈',
            'benefits': [
                _('All Bronze benefits'),
                _('Exclusive events access'),
                _('Extended profile features'),
            ]
        },
        {
            'name': _('Gold'),
            'key': 'gold',
            'points': 1000,
            'emoji': '🥇',
            'benefits': [
                _('All Silver benefits'),
                _('VIP event access'),
                _('Personal coach session'),
            ]
        },
    ]

    context = {
        'is_pwa_user': is_pwa_user,
        'profile': profile,
        'referral_url': referral_url,
        'tiers': tiers,
        'current_tier': profile.membership_tier if profile else 'basic',
        'current_points': profile.referral_points if profile else 0,
    }

    return render(request, 'crush_lu/public/membership.html', context)
