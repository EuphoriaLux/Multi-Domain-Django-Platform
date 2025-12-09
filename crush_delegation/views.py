"""
Views for Crush Delegation app.

Handles user dashboard, profile management, and access control pages.
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden

from .models import DelegationProfile


def home(request):
    """
    Landing page for Crush Delegation subdomain.
    Shows Microsoft login button for unauthenticated users.
    Redirects authenticated users to appropriate page based on status.
    """
    if request.user.is_authenticated:
        try:
            profile = request.user.delegation_profile
            if profile.is_approved:
                return redirect('crush_delegation:dashboard')
            elif profile.status == 'pending':
                return redirect('crush_delegation:pending_approval')
            elif profile.status == 'no_company':
                return redirect('crush_delegation:no_company')
            elif profile.status == 'rejected':
                return redirect('crush_delegation:access_denied')
        except DelegationProfile.DoesNotExist:
            # Profile will be created on next login via signal
            pass

    return render(request, 'crush_delegation/home.html')


@login_required
def dashboard(request):
    """
    Main dashboard for approved delegation users.
    Only accessible to users with approved status.
    """
    try:
        profile = request.user.delegation_profile
    except DelegationProfile.DoesNotExist:
        messages.warning(request, 'Your profile is being set up. Please wait.')
        return redirect('crush_delegation:home')

    # Check access
    if not profile.is_approved:
        if profile.status == 'pending':
            return redirect('crush_delegation:pending_approval')
        elif profile.status == 'no_company':
            return redirect('crush_delegation:no_company')
        elif profile.status == 'rejected' or profile.manually_blocked:
            return redirect('crush_delegation:access_denied')

    context = {
        'profile': profile,
        'company': profile.company,
    }
    return render(request, 'crush_delegation/dashboard.html', context)


@login_required
def profile_view(request):
    """
    User profile page showing Microsoft account details.
    """
    try:
        profile = request.user.delegation_profile
    except DelegationProfile.DoesNotExist:
        messages.warning(request, 'Your profile is being set up. Please wait.')
        return redirect('crush_delegation:home')

    # Only approved users can view profile
    if not profile.is_approved:
        return redirect('crush_delegation:dashboard')

    context = {
        'profile': profile,
        'user': request.user,
    }
    return render(request, 'crush_delegation/profile.html', context)


@login_required
def pending_approval(request):
    """
    Page shown to users waiting for admin approval.
    """
    try:
        profile = request.user.delegation_profile
    except DelegationProfile.DoesNotExist:
        return redirect('crush_delegation:home')

    # If already approved, redirect to dashboard
    if profile.is_approved:
        return redirect('crush_delegation:dashboard')

    # If rejected, show access denied
    if profile.status == 'rejected' or profile.manually_blocked:
        return redirect('crush_delegation:access_denied')

    context = {
        'profile': profile,
    }
    return render(request, 'crush_delegation/pending_approval.html', context)


@login_required
def no_company(request):
    """
    Page shown to users whose email domain doesn't match any company.
    """
    try:
        profile = request.user.delegation_profile
    except DelegationProfile.DoesNotExist:
        return redirect('crush_delegation:home')

    # If approved, redirect to dashboard
    if profile.is_approved:
        return redirect('crush_delegation:dashboard')

    # If they have a company now, check other status
    if profile.company:
        if profile.status == 'pending':
            return redirect('crush_delegation:pending_approval')
        elif profile.status == 'rejected':
            return redirect('crush_delegation:access_denied')

    context = {
        'profile': profile,
        'user_email': request.user.email,
    }
    return render(request, 'crush_delegation/no_company.html', context)


@login_required
def access_denied(request):
    """
    Page shown to rejected users (e.g., management roles).
    """
    try:
        profile = request.user.delegation_profile
    except DelegationProfile.DoesNotExist:
        return redirect('crush_delegation:home')

    # If approved, redirect to dashboard
    if profile.is_approved:
        return redirect('crush_delegation:dashboard')

    context = {
        'profile': profile,
        'reason': profile.rejection_reason or 'Your account does not have access to this platform.',
    }
    return render(request, 'crush_delegation/access_denied.html', context)
