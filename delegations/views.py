"""
Views for Delegations.lu app.

Handles user dashboard, profile management, and access control pages.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .signals import ensure_delegation_profile_for_user


def _get_or_create_profile(user):
    """
    Get existing DelegationProfile or create one from Microsoft SocialAccount.
    Returns (profile, created) tuple.
    """
    return ensure_delegation_profile_for_user(user)


def home(request):
    """
    Landing page for delegations.lu domain.
    Shows Microsoft login button for unauthenticated users.
    Redirects authenticated users to appropriate page based on status.
    """
    if request.user.is_authenticated:
        profile, _created = _get_or_create_profile(request.user)
        if profile:
            if profile.is_approved:
                return redirect("delegations:dashboard")
            elif profile.manually_blocked or profile.status == "rejected":
                return redirect("delegations:access_denied")
            elif profile.status == "pending":
                return redirect("delegations:pending_approval")
            elif profile.status == "no_company":
                return redirect("delegations:no_company")

    return render(request, "delegations/home.html")


@login_required
def dashboard(request):
    """
    Main dashboard for approved delegation users.
    Only accessible to users with approved status.
    """
    profile, created = _get_or_create_profile(request.user)

    if not profile:
        messages.warning(
            request, "Please sign in with Microsoft to access this platform."
        )
        return redirect("delegations:home")

    if created:
        messages.info(request, "Your profile has been created.")

    # Check access
    if not profile.is_approved:
        if profile.manually_blocked or profile.status == "rejected":
            return redirect("delegations:access_denied")
        elif profile.status == "pending":
            return redirect("delegations:pending_approval")
        elif profile.status == "no_company":
            return redirect("delegations:no_company")

    context = {
        "profile": profile,
        "company": profile.company,
    }
    return render(request, "delegations/dashboard.html", context)


@login_required
def profile_view(request):
    """
    User profile page showing Microsoft account details.
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        messages.warning(request, "Your profile is being set up. Please wait.")
        return redirect("delegations:home")

    # Only approved users can view profile
    if not profile.is_approved:
        return redirect("delegations:dashboard")

    context = {
        "profile": profile,
        "user": request.user,
    }
    return render(request, "delegations/profile.html", context)


@login_required
def pending_approval(request):
    """
    Page shown to users waiting for admin approval.
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        return redirect("delegations:home")

    # If already approved, redirect to dashboard
    if profile.is_approved:
        return redirect("delegations:dashboard")

    # If rejected or blocked, show access denied
    if profile.status == "rejected" or profile.manually_blocked:
        return redirect("delegations:access_denied")

    context = {
        "profile": profile,
    }
    return render(request, "delegations/pending_approval.html", context)


@login_required
def no_company(request):
    """
    Page shown to users whose email domain doesn't match any company.
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        return redirect("delegations:home")

    # If approved, redirect to dashboard
    if profile.is_approved:
        return redirect("delegations:dashboard")

    if profile.manually_blocked or profile.status == "rejected":
        return redirect("delegations:access_denied")

    # If they have a company now, check other status
    if profile.company and profile.status == "pending":
        return redirect("delegations:pending_approval")

    context = {
        "profile": profile,
        "user_email": request.user.email,
    }
    return render(request, "delegations/no_company.html", context)


@login_required
def access_denied(request):
    """
    Page shown to rejected or manually blocked users (e.g., management roles).
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        return redirect("delegations:home")

    # If approved, redirect to dashboard
    if profile.is_approved:
        return redirect("delegations:dashboard")

    reason = profile.rejection_reason
    if not reason and profile.manually_blocked:
        reason = "Your account has been blocked by an administrator."

    context = {
        "profile": profile,
        "reason": reason or "Your account does not have access to this platform.",
    }
    return render(request, "delegations/access_denied.html", context)
