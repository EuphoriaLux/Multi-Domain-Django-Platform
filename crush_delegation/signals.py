"""
Signal handlers for Crush Delegation app.

Handles Microsoft OAuth profile creation, company matching, and photo download.
"""
import logging
import requests
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.signals import user_logged_in
from django.core.files.base import ContentFile
from django.utils import timezone
from allauth.socialaccount.signals import pre_social_login, social_account_updated
from allauth.socialaccount.models import SocialAccount, SocialToken

from .models import Company, DelegationProfile, AccessLog

logger = logging.getLogger(__name__)

# Management keywords that trigger auto-rejection
MANAGEMENT_KEYWORDS = [
    'ceo', 'cto', 'cfo', 'coo', 'cio',
    'chief', 'director', 'owner', 'founder',
    'president', 'executive', 'partner',
    'managing director', 'general manager',
    'vp', 'vice president',
]

# Consumer email domains (personal accounts)
CONSUMER_DOMAINS = [
    'outlook.com', 'hotmail.com', 'live.com', 'msn.com',
    'gmail.com', 'yahoo.com', 'icloud.com', 'me.com',
    'proton.me', 'protonmail.com',
]


def _is_delegation_domain(request):
    """Check if current request is from delegations.lu domain"""
    if not request:
        return False
    try:
        host = request.get_host().split(':')[0].lower()
    except KeyError:
        # During tests, the request may not have SERVER_NAME set
        return False
    return host in ['delegations.lu', 'localhost', '127.0.0.1']


def _is_consumer_email(email):
    """Check if email is from a consumer domain (personal account)"""
    if not email or '@' not in email:
        return False
    domain = email.split('@')[1].lower()
    return domain in CONSUMER_DOMAINS


def _is_management_title(job_title):
    """Check if job title indicates management role"""
    if not job_title:
        return False
    title_lower = job_title.lower()
    return any(keyword in title_lower for keyword in MANAGEMENT_KEYWORDS)


def _match_user_to_company(email, microsoft_tenant_id=None):
    """
    Match user to company by email domain or Microsoft tenant ID.

    Priority:
    1. Microsoft tenant ID (most accurate)
    2. Email domain match

    Returns Company instance or None
    """
    if not email or '@' not in email:
        return None

    email_domain = email.split('@')[1].lower()

    # Skip consumer domains - they can't match to a company
    if email_domain in CONSUMER_DOMAINS:
        return None

    # 1. Try matching by Microsoft tenant ID (most accurate)
    if microsoft_tenant_id:
        company = Company.objects.filter(
            microsoft_tenant_id=microsoft_tenant_id,
            is_active=True
        ).first()
        if company:
            logger.info(f"Matched user {email} to company {company.name} by tenant ID")
            return company

    # 2. Try matching by email domain
    for company in Company.objects.filter(is_active=True):
        if company.email_domains:
            domains_lower = [d.lower() for d in company.email_domains]
            if email_domain in domains_lower:
                logger.info(f"Matched user {email} to company {company.name} by email domain")
                return company

    logger.info(f"No company match for user {email}")
    return None


def _determine_profile_status(email, job_title, company):
    """
    Determine initial profile status based on user data.

    Returns tuple: (status, role, reason)
    """
    # Personal account - pending approval
    if _is_consumer_email(email):
        return 'pending', 'pending', 'Personal Microsoft account - pending admin approval'

    # No company match
    if not company:
        return 'no_company', 'pending', 'Email domain does not match any registered company'

    # Management title - auto-reject
    if _is_management_title(job_title):
        return 'rejected', 'pending', f'Management role detected: {job_title}'

    # Company has auto-approve enabled
    if company.auto_approve_workers:
        return 'approved', 'worker', 'Auto-approved based on company settings'

    # Default: pending approval
    return 'pending', 'pending', 'Awaiting admin approval'


def _download_microsoft_photo(access_token):
    """
    Download profile photo from Microsoft Graph API.

    Returns tuple: (ContentFile, extension) or (None, None)
    """
    try:
        headers = {'Authorization': f'Bearer {access_token}'}
        photo_url = 'https://graph.microsoft.com/v1.0/me/photo/$value'

        response = requests.get(photo_url, headers=headers, timeout=10)

        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            extension = 'png' if 'png' in content_type else 'jpg'
            logger.info("Successfully downloaded Microsoft profile photo")
            return ContentFile(response.content), extension
        else:
            logger.info(f"No profile photo available: HTTP {response.status_code}")
            return None, None
    except Exception as e:
        logger.error(f"Error downloading Microsoft photo: {str(e)}")
        return None, None


@receiver(pre_social_login)
def check_microsoft_access_on_login(sender, request, sociallogin, **kwargs):
    """
    Check access before allowing Microsoft login on delegation domain.
    This runs BEFORE the user is logged in.
    """
    if not _is_delegation_domain(request):
        return

    if sociallogin.account.provider != 'microsoft':
        return

    extra_data = sociallogin.account.extra_data
    email = extra_data.get('mail') or extra_data.get('userPrincipalName', '')
    job_title = extra_data.get('jobTitle', '')

    logger.info(f"Microsoft pre_social_login for: {email}")

    # Check if existing user is blocked
    if sociallogin.is_existing:
        try:
            profile = DelegationProfile.objects.get(user=sociallogin.user)
            if profile.manually_blocked:
                from django.core.exceptions import PermissionDenied
                logger.warning(f"Blocked user attempted login: {email}")
                raise PermissionDenied("Your account has been blocked from this platform.")
        except DelegationProfile.DoesNotExist:
            pass


@receiver(post_save, sender=SocialAccount)
def create_delegation_profile_from_microsoft(sender, instance, created, **kwargs):
    """
    Create DelegationProfile when a new Microsoft SocialAccount is created.
    """
    if instance.provider != 'microsoft':
        return

    if not created:
        return

    logger.info(f"Creating DelegationProfile for Microsoft user: {instance.user.email}")

    try:
        extra_data = instance.extra_data

        # Extract user data from Microsoft
        email = instance.user.email or extra_data.get('mail') or extra_data.get('userPrincipalName', '')
        microsoft_id = extra_data.get('id', '')
        microsoft_tenant_id = extra_data.get('tid', '')  # Tenant ID from token claims
        job_title = extra_data.get('jobTitle', '')
        department = extra_data.get('department', '')
        office_location = extra_data.get('officeLocation', '')

        # Match user to company
        company = _match_user_to_company(email, microsoft_tenant_id)

        # Determine initial status
        status, role, reason = _determine_profile_status(email, job_title, company)

        # Create profile
        profile, profile_created = DelegationProfile.objects.get_or_create(
            user=instance.user,
            defaults={
                'company': company,
                'microsoft_id': microsoft_id,
                'microsoft_tenant_id': microsoft_tenant_id,
                'job_title': job_title,
                'department': department,
                'office_location': office_location,
                'status': status,
                'role': role,
                'rejection_reason': reason if status == 'rejected' else '',
                'approved_at': timezone.now() if status == 'approved' else None,
            }
        )

        if profile_created:
            # Log the access decision
            action_map = {
                'approved': 'auto_approved',
                'rejected': 'auto_rejected',
                'pending': 'login_pending',
                'no_company': 'no_company_match',
            }
            AccessLog.objects.create(
                profile=profile,
                action=action_map.get(status, 'login_pending'),
                details=reason
            )

            # Try to download profile photo
            try:
                token = SocialToken.objects.filter(
                    account=instance,
                    account__provider='microsoft'
                ).first()

                if token:
                    photo_content, extension = _download_microsoft_photo(token.token)
                    if photo_content:
                        filename = f'microsoft_{instance.user.id}.{extension}'
                        profile.profile_photo.save(filename, photo_content, save=True)
                        logger.info(f"Saved Microsoft photo for {email}")
            except Exception as e:
                logger.error(f"Error saving profile photo: {str(e)}")

            logger.info(f"Created DelegationProfile for {email} with status: {status}")

    except Exception as e:
        logger.error(f"Error creating DelegationProfile: {str(e)}", exc_info=True)


@receiver(social_account_updated)
def update_delegation_profile_from_microsoft(sender, request, sociallogin, **kwargs):
    """
    Update DelegationProfile when Microsoft account is updated/refreshed.
    """
    if sociallogin.account.provider != 'microsoft':
        return

    if not _is_delegation_domain(request):
        return

    try:
        profile = DelegationProfile.objects.get(user=sociallogin.user)
        extra_data = sociallogin.account.extra_data

        # Update fields that might change
        profile.department = extra_data.get('department', '') or profile.department
        profile.job_title = extra_data.get('jobTitle', '') or profile.job_title
        profile.office_location = extra_data.get('officeLocation', '') or profile.office_location
        profile.save(update_fields=['department', 'job_title', 'office_location', 'updated_at'])

        logger.info(f"Updated DelegationProfile for {sociallogin.user.email}")

    except DelegationProfile.DoesNotExist:
        logger.info(f"No DelegationProfile to update for {sociallogin.user.email}")


@receiver(user_logged_in)
def update_delegation_last_login(sender, request, user, **kwargs):
    """
    Update last login timestamp and log successful login for delegation users.
    """
    if not _is_delegation_domain(request):
        return

    try:
        profile = DelegationProfile.objects.get(user=user)
        profile.last_login_at = timezone.now()
        profile.save(update_fields=['last_login_at'])

        # Log successful login if approved
        if profile.is_approved:
            AccessLog.objects.create(
                profile=profile,
                action='login_success',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500]
            )
    except DelegationProfile.DoesNotExist:
        pass
