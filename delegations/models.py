"""
Models for Delegations.lu - Staff management and event coordination platform.

Multi-company architecture supporting:
- Company isolation (each company sees only their data)
- Microsoft OAuth integration
- Role-based access control (workers vs management)
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from crush_lu.storage import powerup_upload_path


class Company(models.Model):
    """
    Company/Organization that uses the delegation platform.

    Each company has isolated data - workers only see their own company's
    events, requests, and announcements.
    """
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, help_text="URL-friendly identifier")

    # Email domain(s) for auto-matching users to company
    email_domains = models.JSONField(
        default=list,
        blank=True,
        help_text="Email domains for auto-matching users, e.g., ['company.lu', 'company.com']"
    )

    # Optional: Microsoft Tenant ID for stricter matching
    microsoft_tenant_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Microsoft Entra ID tenant ID for precise user matching"
    )

    # Company settings
    logo = models.ImageField(upload_to=powerup_upload_path('delegation/companies'), blank=True, null=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    auto_approve_workers = models.BooleanField(
        default=False,
        help_text="Automatically approve users from this company's domain"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company"
        verbose_name_plural = "Companies"
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_domains_display(self):
        """Return comma-separated list of email domains"""
        return ', '.join(self.email_domains) if self.email_domains else '-'


class DelegationProfile(models.Model):
    """
    User profile for the Delegations.lu platform.

    Links users to their company and tracks access control status.
    """
    ROLE_CHOICES = [
        ('pending', 'Pending Verification'),
        ('worker', 'Worker'),
        ('staff_delegate', 'Staff Delegate'),
        ('delegate_admin', 'Delegate Admin'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('no_company', 'No Company Match'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='delegation_profile'
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='profiles'
    )

    # Microsoft account data
    microsoft_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Microsoft account object ID"
    )
    microsoft_tenant_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="User's Microsoft tenant ID"
    )
    department = models.CharField(max_length=200, blank=True, default='')
    job_title = models.CharField(max_length=200, blank=True, default='')
    office_location = models.CharField(max_length=200, blank=True, default='')

    # Profile photo (downloaded from Microsoft Graph)
    profile_photo = models.ImageField(
        upload_to=powerup_upload_path('delegation/profiles'),
        blank=True,
        null=True
    )

    # Access control
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='pending'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    manually_approved = models.BooleanField(
        default=False,
        help_text="Admin manually approved this user"
    )
    manually_blocked = models.BooleanField(
        default=False,
        help_text="Admin manually blocked this user"
    )
    rejection_reason = models.TextField(
        blank=True,
        help_text="Reason for rejection if status is rejected"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Delegation Profile"
        verbose_name_plural = "Delegation Profiles"

    def __str__(self):
        name = self.user.get_full_name() or self.user.email
        company = self.company.name if self.company else 'No Company'
        return f"{name} ({company})"

    @property
    def is_approved(self):
        """Check if user has access to the platform"""
        if self.manually_blocked:
            return False
        if self.manually_approved:
            return True
        return self.status == 'approved'

    @property
    def display_name(self):
        """Return display name for the user"""
        return self.user.get_full_name() or self.user.email.split('@')[0]

    def get_profile_photo_url(self):
        """Return profile photo URL or default placeholder"""
        if self.profile_photo:
            return self.profile_photo.url
        return '/static/delegations/images/default-avatar.svg'

    def approve(self, role='worker'):
        """Approve the user with given role"""
        self.status = 'approved'
        self.role = role
        self.approved_at = timezone.now()
        self.save(update_fields=['status', 'role', 'approved_at', 'updated_at'])

    def reject(self, reason=''):
        """Reject the user"""
        self.status = 'rejected'
        self.rejection_reason = reason
        self.save(update_fields=['status', 'rejection_reason', 'updated_at'])


class AccessLog(models.Model):
    """
    Audit log for access control decisions and login attempts.
    """
    ACTION_CHOICES = [
        ('login_success', 'Login Successful'),
        ('login_blocked', 'Login Blocked'),
        ('login_pending', 'Login - Pending Approval'),
        ('auto_approved', 'Auto-Approved'),
        ('auto_rejected', 'Auto-Rejected'),
        ('manual_approved', 'Manually Approved'),
        ('manual_rejected', 'Manually Rejected'),
        ('no_company_match', 'No Company Match'),
    ]

    profile = models.ForeignKey(
        DelegationProfile,
        on_delete=models.CASCADE,
        related_name='access_logs'
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Access Log"
        verbose_name_plural = "Access Logs"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.profile.user.email} - {self.get_action_display()}"
