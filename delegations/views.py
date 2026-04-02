"""
Views for Delegations.lu app.

Handles user dashboard, profile management, and access control pages.
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.utils import timezone

from .models import DelegationProfile, Company


def _get_or_create_profile(user):
    """
    Get existing DelegationProfile or create one from Microsoft SocialAccount.
    Returns (profile, created) tuple.
    """
    try:
        return user.delegation_profile, False
    except DelegationProfile.DoesNotExist:
        pass

    # Try to create from Microsoft SocialAccount
    from allauth.socialaccount.models import SocialAccount
    try:
        social_account = SocialAccount.objects.get(user=user, provider='microsoft')
    except SocialAccount.DoesNotExist:
        return None, False

    extra_data = social_account.extra_data
    email = user.email or extra_data.get('mail') or extra_data.get('userPrincipalName', '')
    microsoft_id = extra_data.get('id') or ''
    microsoft_tenant_id = extra_data.get('tid') or ''
    job_title = extra_data.get('jobTitle') or ''
    department = extra_data.get('department') or ''
    office_location = extra_data.get('officeLocation') or ''

    # Match to company
    company = None
    if email and '@' in email:
        email_domain = email.split('@')[1].lower()
        consumer_domains = ['outlook.com', 'hotmail.com', 'live.com', 'gmail.com', 'yahoo.com']
        if email_domain not in consumer_domains:
            # Try tenant ID first
            if microsoft_tenant_id:
                company = Company.objects.filter(
                    microsoft_tenant_id=microsoft_tenant_id,
                    is_active=True
                ).first()
            # Then email domain
            if not company:
                for c in Company.objects.filter(is_active=True):
                    if c.email_domains and email_domain in [d.lower() for d in c.email_domains]:
                        company = c
                        break

    # Determine status
    management_keywords = ['ceo', 'cto', 'cfo', 'director', 'owner', 'founder', 'president', 'chief']
    is_consumer = email and '@' in email and email.split('@')[1].lower() in ['outlook.com', 'hotmail.com', 'live.com', 'gmail.com']
    is_management = job_title and any(kw in job_title.lower() for kw in management_keywords)

    if is_consumer:
        status, role = 'pending', 'pending'
    elif not company:
        status, role = 'no_company', 'pending'
    elif is_management:
        status, role = 'rejected', 'pending'
    elif company and company.auto_approve_workers:
        status, role = 'approved', 'worker'
    else:
        status, role = 'pending', 'pending'

    # Create profile
    profile = DelegationProfile.objects.create(
        user=user,
        company=company,
        microsoft_id=microsoft_id,
        microsoft_tenant_id=microsoft_tenant_id,
        job_title=job_title,
        department=department,
        office_location=office_location,
        status=status,
        role=role,
        approved_at=timezone.now() if status == 'approved' else None,
    )

    return profile, True


def home(request):
    """
    Landing page for delegations.lu domain.
    Shows Microsoft login button for unauthenticated users.
    Redirects authenticated users to appropriate page based on status.
    """
    if request.user.is_authenticated:
        profile, created = _get_or_create_profile(request.user)
        if profile:
            if profile.is_approved:
                return redirect('delegations:dashboard')
            elif profile.status == 'pending':
                return redirect('delegations:pending_approval')
            elif profile.status == 'no_company':
                return redirect('delegations:no_company')
            elif profile.status == 'rejected':
                return redirect('delegations:access_denied')

    return render(request, 'delegations/home.html')


@login_required
def dashboard(request):
    """
    Main dashboard for approved delegation users.
    Only accessible to users with approved status.
    """
    profile, created = _get_or_create_profile(request.user)

    if not profile:
        messages.warning(request, 'Please sign in with Microsoft to access this platform.')
        return redirect('delegations:home')

    if created:
        messages.info(request, 'Your profile has been created.')

    # Check access
    if not profile.is_approved:
        if profile.status == 'pending':
            return redirect('delegations:pending_approval')
        elif profile.status == 'no_company':
            return redirect('delegations:no_company')
        elif profile.status == 'rejected' or profile.manually_blocked:
            return redirect('delegations:access_denied')

    context = {
        'profile': profile,
        'company': profile.company,
    }
    return render(request, 'delegations/dashboard.html', context)


@login_required
def profile_view(request):
    """
    User profile page showing Microsoft account details.
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        messages.warning(request, 'Your profile is being set up. Please wait.')
        return redirect('delegations:home')

    # Only approved users can view profile
    if not profile.is_approved:
        return redirect('delegations:dashboard')

    context = {
        'profile': profile,
        'user': request.user,
    }
    return render(request, 'delegations/profile.html', context)


@login_required
def pending_approval(request):
    """
    Page shown to users waiting for admin approval.
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        return redirect('delegations:home')

    # If already approved, redirect to dashboard
    if profile.is_approved:
        return redirect('delegations:dashboard')

    # If rejected, show access denied
    if profile.status == 'rejected' or profile.manually_blocked:
        return redirect('delegations:access_denied')

    context = {
        'profile': profile,
    }
    return render(request, 'delegations/pending_approval.html', context)


@login_required
def no_company(request):
    """
    Page shown to users whose email domain doesn't match any company.
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        return redirect('delegations:home')

    # If approved, redirect to dashboard
    if profile.is_approved:
        return redirect('delegations:dashboard')

    # If they have a company now, check other status
    if profile.company:
        if profile.status == 'pending':
            return redirect('delegations:pending_approval')
        elif profile.status == 'rejected':
            return redirect('delegations:access_denied')

    context = {
        'profile': profile,
        'user_email': request.user.email,
    }
    return render(request, 'delegations/no_company.html', context)


@login_required
def access_denied(request):
    """
    Page shown to rejected users (e.g., management roles).
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        return redirect('delegations:home')

    # If approved, redirect to dashboard
    if profile.is_approved:
        return redirect('delegations:dashboard')

    context = {
        'profile': profile,
        'reason': profile.rejection_reason or 'Your account does not have access to this platform.',
    }
    return render(request, 'delegations/access_denied.html', context)
