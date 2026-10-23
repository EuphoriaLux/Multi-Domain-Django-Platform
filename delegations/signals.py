"""
Signal handlers and onboarding helpers for Delegations.lu app.

Handles Microsoft OAuth profile creation, company matching, tenant ID
extraction, nOAuth spoof-resistance, and profile photo download.
"""

import logging
import threading
from types import SimpleNamespace

import requests
from allauth.socialaccount.models import SocialAccount, SocialToken
from allauth.socialaccount.signals import pre_social_login, social_account_updated
from django.contrib.auth.signals import user_logged_in
from django.core.files.base import ContentFile
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import AccessLog, Company, DelegationProfile

logger = logging.getLogger(__name__)

# Thread-local storage to pass domain and login context between signals
_thread_local = threading.local()

# Management keywords that trigger auto-rejection
MANAGEMENT_KEYWORDS = [
    "ceo",
    "cto",
    "cfo",
    "coo",
    "cio",
    "chief",
    "director",
    "owner",
    "founder",
    "president",
    "executive",
    "partner",
    "managing director",
    "general manager",
    "vp",
    "vice president",
]

# Consumer email domains (personal accounts)
CONSUMER_DOMAINS = [
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "gmail.com",
    "yahoo.com",
    "icloud.com",
    "me.com",
    "proton.me",
    "protonmail.com",
]


def _host_is_delegations(host):
    """delegations.lu, its ``delegation.localhost`` dev alias, or bare localhost."""
    from azureproject.domains import get_canonical_domain

    return host in ("localhost", "127.0.0.1") or (
        get_canonical_domain(host) == "delegations.lu"
    )


def _is_delegation_domain(request):
    """Check if current request is from delegations.lu domain"""
    if not request:
        return False
    try:
        host = request.get_host().split(":")[0].lower()
    except KeyError:
        # During tests, the request may not have SERVER_NAME set
        return False
    return _host_is_delegations(host)


def _is_consumer_email(email):
    """Check if email is from a consumer domain (personal account)"""
    if not email or "@" not in email:
        return False
    domain = email.split("@")[1].lower()
    return domain in CONSUMER_DOMAINS


def _is_management_title(job_title):
    """Check if job title indicates management role"""
    if not job_title:
        return False
    title_lower = job_title.lower()
    return any(keyword in title_lower for keyword in MANAGEMENT_KEYWORDS)


def _is_external_guest_upn(extra_data):
    """True when Microsoft Graph reports a B2B guest UPN (``#EXT#``)."""
    upn = ((extra_data or {}).get("userPrincipalName") or "").strip().lower()
    return "#ext#" in upn


def _email_trusted_for_domain_match(extra_data, email):
    """
    Guard against the nOAuth pattern (spoofed writable ``mail`` attribute on
    an external Entra tenant).

    When ``userPrincipalName`` is present in ``extra_data``, only trust
    ``email`` for email-domain company matching if the UPN proves the tenant
    owns that domain (and is not a ``#EXT#`` B2B guest).
    """
    if not email or "@" not in email:
        return False
    data = extra_data or {}
    if _is_external_guest_upn(data):
        return False
    if data.get("userPrincipalName"):
        from azureproject.adapters import _microsoft_email_is_tenant_owned

        return _microsoft_email_is_tenant_owned(data, email)
    return True


def _extract_microsoft_tenant_id(social_account, sociallogin=None, extra_data=None):
    """
    Extract the Microsoft Entra tenant ID for a user's Microsoft account.

    allauth's Microsoft provider stores the Graph ``/me`` payload in
    ``extra_data``, which does not carry ``tid``. We resolve the tenant via
    ``azureproject.adapters._microsoft_tenant_id`` (from a pinned SocialApp or
    the JWT access token's ``tid`` claim), falling back to ``extra_data["tid"]``
    when provided directly.
    """
    from azureproject.adapters import _microsoft_tenant_id

    if sociallogin is not None and getattr(sociallogin, "token", None) is not None:
        tid = _microsoft_tenant_id(sociallogin)
        if tid:
            return tid

    if social_account is not None and social_account.pk:
        token = (
            SocialToken.objects.filter(
                account=social_account, account__provider="microsoft"
            )
            .select_related("app")
            .first()
        )
        if token is not None:
            tid = _microsoft_tenant_id(SimpleNamespace(token=token))
            if tid:
                return tid

    data = (
        extra_data
        if extra_data is not None
        else (getattr(social_account, "extra_data", None) or {})
    )
    return (data.get("tid") or "").strip()


def _match_user_to_company(email, microsoft_tenant_id=None, extra_data=None):
    """
    Match user to company by Microsoft tenant ID or verified email domain.

    Priority:
    1. Microsoft tenant ID (most accurate)
    2. Exact email domain match (when tenant-owned)

    Returns Company instance or None
    """
    if not email or "@" not in email:
        return None

    email_domain = email.split("@")[1].lower()

    # Skip consumer domains - they can't match to a company
    if email_domain in CONSUMER_DOMAINS:
        return None

    # External B2B guest UPNs never auto-match to a company
    if _is_external_guest_upn(extra_data):
        logger.warning("Refusing automatic company match for B2B guest UPN")
        return None

    # 1. Try matching by Microsoft tenant ID (most accurate)
    if microsoft_tenant_id:
        company = Company.objects.filter(
            microsoft_tenant_id=microsoft_tenant_id, is_active=True
        ).first()
        if company:
            logger.info(f"Matched user {email} to company {company.name} by tenant ID")
            return company

    # Guard against spoofed Graph `mail` on an external tenant (nOAuth)
    if not _email_trusted_for_domain_match(extra_data, email):
        logger.warning(
            "Refusing email-domain company match for unverified Microsoft mail %s",
            email_domain,
        )
        return None

    # 2. Try matching by exact email domain
    for company in Company.objects.filter(is_active=True):
        if company.email_domains:
            domains_lower = [d.strip().lower() for d in company.email_domains if d]
            if email_domain in domains_lower:
                logger.info(
                    f"Matched user {email} to company {company.name} by email domain"
                )
                return company

    logger.info(f"No company match for user {email}")
    return None


def _determine_profile_status(email, job_title, company, extra_data=None):
    """
    Determine initial profile status based on user data.

    Returns tuple: (status, role, reason)
    """
    if _is_external_guest_upn(extra_data):
        return (
            "pending",
            "pending",
            "External guest Microsoft account - pending admin approval",
        )

    if extra_data and not _email_trusted_for_domain_match(extra_data, email):
        return (
            "pending",
            "pending",
            "Unverified Microsoft email domain - pending admin approval",
        )

    # Personal account - pending approval
    if _is_consumer_email(email):
        return (
            "pending",
            "pending",
            "Personal Microsoft account - pending admin approval",
        )

    # No company match
    if not company:
        return (
            "no_company",
            "pending",
            "Email domain does not match any registered company",
        )

    # Management title - auto-reject
    if _is_management_title(job_title):
        return "rejected", "pending", f"Management role detected: {job_title}"

    # Company has auto-approve enabled
    if company.auto_approve_workers:
        return "approved", "worker", "Auto-approved based on company settings"

    # Default: pending approval
    return "pending", "pending", "Awaiting admin approval"


def _download_microsoft_photo(access_token):
    """
    Download profile photo from Microsoft Graph API.

    Returns tuple: (ContentFile, extension) or (None, None)
    """
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        photo_url = "https://graph.microsoft.com/v1.0/me/photo/$value"

        response = requests.get(photo_url, headers=headers, timeout=10)

        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "image/jpeg")
            extension = "png" if "png" in content_type else "jpg"
            logger.info("Successfully downloaded Microsoft profile photo")
            return ContentFile(response.content), extension
        else:
            logger.info(f"No profile photo available: HTTP {response.status_code}")
            return None, None
    except Exception as e:
        logger.error(f"Error downloading Microsoft photo: {str(e)}")
        return None, None


def ensure_delegation_profile_for_user(user, social_account=None, sociallogin=None):
    """
    Get existing DelegationProfile or create one from the user's Microsoft
    SocialAccount, recording the initial AccessLog and downloading the profile
    photo when available.

    Returns (profile, created) tuple, or (None, False) if the user has no
    Microsoft SocialAccount.
    """
    try:
        return user.delegation_profile, False
    except DelegationProfile.DoesNotExist:
        pass

    if social_account is None and sociallogin is not None:
        if getattr(sociallogin.account, "provider", None) == "microsoft":
            social_account = sociallogin.account

    if social_account is None:
        social_account = SocialAccount.objects.filter(
            user=user, provider="microsoft"
        ).first()

    if social_account is None:
        return None, False

    extra_data = social_account.extra_data or {}

    email = (
        user.email
        or extra_data.get("mail")
        or extra_data.get("userPrincipalName")
        or ""
    ).strip()
    microsoft_id = (extra_data.get("id") or "").strip() or None
    microsoft_tenant_id = _extract_microsoft_tenant_id(
        social_account, sociallogin=sociallogin, extra_data=extra_data
    )
    job_title = (extra_data.get("jobTitle") or "").strip()
    department = (extra_data.get("department") or "").strip()
    office_location = (extra_data.get("officeLocation") or "").strip()

    company = _match_user_to_company(
        email, microsoft_tenant_id=microsoft_tenant_id, extra_data=extra_data
    )
    status, role, reason = _determine_profile_status(
        email, job_title, company, extra_data=extra_data
    )

    profile, profile_created = DelegationProfile.objects.get_or_create(
        user=user,
        defaults={
            "company": company,
            "microsoft_id": microsoft_id,
            "microsoft_tenant_id": microsoft_tenant_id,
            "job_title": job_title,
            "department": department,
            "office_location": office_location,
            "status": status,
            "role": role,
            "rejection_reason": reason if status == "rejected" else "",
            "approved_at": timezone.now() if status == "approved" else None,
        },
    )

    if profile_created:
        action_map = {
            "approved": "auto_approved",
            "rejected": "auto_rejected",
            "pending": "login_pending",
            "no_company": "no_company_match",
        }
        AccessLog.objects.create(
            profile=profile,
            action=action_map.get(status, "login_pending"),
            details=reason,
        )

        # Try to download profile photo (prefer token on sociallogin when
        # called from post_save before SocialToken is saved to DB).
        try:
            access_token = None
            if sociallogin is not None and getattr(sociallogin, "token", None):
                access_token = sociallogin.token.token
            if not access_token and social_account.pk:
                token_obj = SocialToken.objects.filter(
                    account=social_account, account__provider="microsoft"
                ).first()
                if token_obj:
                    access_token = token_obj.token

            if access_token:
                photo_content, extension = _download_microsoft_photo(access_token)
                if photo_content:
                    filename = f"microsoft_{user.id}.{extension}"
                    profile.profile_photo.save(filename, photo_content, save=True)
                    logger.info(f"Saved Microsoft photo for {email}")
        except Exception as e:
            logger.error(f"Error saving profile photo: {str(e)}")

        logger.info(f"Created DelegationProfile for {email} with status: {status}")

    return profile, profile_created


@receiver(pre_social_login)
def check_microsoft_access_on_login(sender, request, sociallogin, **kwargs):
    """
    Check access before allowing Microsoft login on delegation domain.
    This runs BEFORE the user is logged in.

    Sets thread-local context to indicate this is a delegations.lu login,
    which is used by create_delegation_profile_from_microsoft to decide
    whether to create a DelegationProfile.
    """
    # Reset the flags at the start of each login attempt
    _thread_local.is_delegation_login = False
    _thread_local.sociallogin = None

    if sociallogin.account.provider != "microsoft":
        return

    if not _is_delegation_domain(request):
        return

    # Set flag and sociallogin reference for downstream signal handlers
    _thread_local.is_delegation_login = True
    _thread_local.sociallogin = sociallogin

    extra_data = sociallogin.account.extra_data or {}
    email = extra_data.get("mail") or extra_data.get("userPrincipalName", "")

    logger.info(f"Microsoft pre_social_login for delegations.lu: {email}")

    # Check if existing user is blocked
    if sociallogin.is_existing:
        try:
            profile = DelegationProfile.objects.get(user=sociallogin.user)
            if profile.manually_blocked:
                from django.core.exceptions import PermissionDenied

                AccessLog.objects.create(
                    profile=profile,
                    action="login_blocked",
                    details="Blocked user attempted Microsoft login",
                    ip_address=request.META.get("REMOTE_ADDR") if request else None,
                    user_agent=(
                        request.META.get("HTTP_USER_AGENT", "")[:500] if request else ""
                    ),
                )
                logger.warning(f"Blocked user attempted login: {email}")
                raise PermissionDenied(
                    "Your account has been blocked from this platform."
                )
        except DelegationProfile.DoesNotExist:
            pass


@receiver(post_save, sender=SocialAccount)
def create_delegation_profile_from_microsoft(sender, instance, created, **kwargs):
    """
    Create DelegationProfile when a new Microsoft SocialAccount is created.

    Only creates profile if the login originated from delegations.lu domain.
    We check for a thread-local flag set by the pre_social_login signal.
    """
    if instance.provider != "microsoft":
        return

    if not created:
        return

    # Only create DelegationProfile if login was from delegations.lu
    if not getattr(_thread_local, "is_delegation_login", False):
        logger.info(
            f"Skipping DelegationProfile creation for {instance.user.email} - not a delegations.lu login"
        )
        return

    logger.info(f"Creating DelegationProfile for Microsoft user: {instance.user.email}")

    try:
        ensure_delegation_profile_for_user(
            instance.user,
            social_account=instance,
            sociallogin=getattr(_thread_local, "sociallogin", None),
        )
    except Exception as e:
        logger.error(f"Error creating DelegationProfile: {str(e)}", exc_info=True)


@receiver(social_account_updated)
def update_delegation_profile_from_microsoft(sender, request, sociallogin, **kwargs):
    """
    Update DelegationProfile when Microsoft account is updated/refreshed.
    """
    if sociallogin.account.provider != "microsoft":
        return

    if not _is_delegation_domain(request):
        return

    try:
        profile = DelegationProfile.objects.get(user=sociallogin.user)
        extra_data = sociallogin.account.extra_data or {}

        # Update fields that might change
        profile.department = extra_data.get("department", "") or profile.department
        profile.job_title = extra_data.get("jobTitle", "") or profile.job_title
        profile.office_location = (
            extra_data.get("officeLocation", "") or profile.office_location
        )
        tid = _extract_microsoft_tenant_id(
            sociallogin.account, sociallogin=sociallogin, extra_data=extra_data
        )
        if tid and not profile.microsoft_tenant_id:
            profile.microsoft_tenant_id = tid

        update_fields = [
            "department",
            "job_title",
            "office_location",
            "microsoft_tenant_id",
            "updated_at",
        ]

        # If a worker's title updated to a management role and they were not
        # manually approved as an exception, revoke access automatically.
        if (
            _is_management_title(profile.job_title)
            and not profile.manually_approved
            and profile.status != "rejected"
        ):
            profile.status = "rejected"
            profile.rejection_reason = f"Management role detected: {profile.job_title}"
            update_fields.extend(["status", "rejection_reason"])
            AccessLog.objects.create(
                profile=profile,
                action="auto_rejected",
                details=profile.rejection_reason,
            )

        profile.save(update_fields=update_fields)
        logger.info(f"Updated DelegationProfile for {sociallogin.user.email}")

    except DelegationProfile.DoesNotExist:
        logger.info(f"No DelegationProfile to update for {sociallogin.user.email}")


@receiver(user_logged_in)
def update_delegation_last_login(sender, request, user, **kwargs):
    """
    Ensure DelegationProfile exists (e.g. when a user with a pre-existing
    Microsoft SocialAccount from another domain logs into delegations.lu for
    the first time), update last login timestamp, and log successful login.
    """
    if not _is_delegation_domain(request):
        return

    try:
        profile, _ = ensure_delegation_profile_for_user(
            user,
            sociallogin=getattr(_thread_local, "sociallogin", None),
        )
        if not profile:
            return
        profile.last_login_at = timezone.now()
        profile.save(update_fields=["last_login_at"])

        # Log successful login if approved
        if profile.is_approved:
            AccessLog.objects.create(
                profile=profile,
                action="login_success",
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
            )
    except Exception as e:
        logger.error(f"Error in update_delegation_last_login: {str(e)}", exc_info=True)
