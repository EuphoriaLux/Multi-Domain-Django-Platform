import uuid

from django.db import models
from django.utils import timezone


class CustomerGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    class OnboardingStatus(models.TextChoices):
        NONE = "none", "Not Started"
        IN_PROGRESS = "in_progress", "In Progress"
        EMAIL_SENT = "email_sent", "Email Sent"
        COMPLETED = "completed", "Completed"

    onboarding_status = models.CharField(
        max_length=20,
        choices=OnboardingStatus.choices,
        default=OnboardingStatus.NONE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Customer Group"

    def __str__(self):
        return self.name

    @property
    def entity_count(self):
        return self.entities.count()

    @property
    def active_contracts(self):
        return self.contracts.filter(status="active")


class Entity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        CustomerGroup, on_delete=models.CASCADE, related_name="entities"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "entities"
        ordering = ["group__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.group.name})"

    @property
    def tenant_count(self):
        return self.tenants.count()


class Tenant(models.Model):
    class EnvironmentType(models.TextChoices):
        PRODUCTION = "production", "Production"
        SANDBOX = "sandbox", "Sandbox"
        DEVELOPMENT = "development", "Development"
        TEST = "test", "Test"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="tenants")
    tenant_id_azure = models.CharField(
        max_length=36,
        unique=True,
        verbose_name="Azure Tenant ID",
        help_text="The Azure AD / Entra ID tenant GUID",
    )
    company_name = models.CharField(max_length=255)
    environment_type = models.CharField(
        max_length=20,
        choices=EnvironmentType.choices,
        default=EnvironmentType.PRODUCTION,
    )
    primary_domain = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="e.g. contoso.onmicrosoft.com",
    )
    microsoft_tenant_domain = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="e.g. contoso.onmicrosoft.com",
    )
    implementation_deadline = models.DateField(null=True, blank=True)
    has_azure = models.BooleanField(
        default=False, help_text="Whether Azure RBAC setup is needed"
    )
    gdap_link = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="GDAP approval link for this tenant",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name"]

    def __str__(self):
        return f"{self.company_name} ({self.primary_domain})"

    @property
    def group(self):
        return self.entity.group


class AuthorizedContact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        CustomerGroup, on_delete=models.CASCADE, related_name="authorized_contacts"
    )
    entra_object_id = models.CharField(
        max_length=36,
        blank=True,
        default="",
        verbose_name="Entra Object ID",
        help_text="Azure AD / Entra ID object GUID for SSO mapping",
    )
    email = models.EmailField()
    display_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True, default="")
    first_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100, blank=True, default="")
    mobile_phone = models.CharField(max_length=50, blank=True, default="")
    business_phone = models.CharField(max_length=50, blank=True, default="")
    teams_address = models.CharField(max_length=255, blank=True, default="")
    job_title = models.CharField(max_length=200, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return f"{self.display_name} ({self.email})"


class ContactTenantPermission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(
        AuthorizedContact,
        on_delete=models.CASCADE,
        related_name="tenant_permissions",
    )
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="contact_permissions"
    )
    can_create_tickets = models.BooleanField(default=True)
    can_view_tickets = models.BooleanField(default=True)
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["contact", "tenant"]
        verbose_name = "Contact \u2194 Tenant Permission"
        verbose_name_plural = "Contact \u2194 Tenant Permissions"

    def __str__(self):
        return f"{self.contact.display_name} \u2192 {self.tenant.company_name}"


class UserRole(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        USER = "user", "User"
        VIEWER = "viewer", "Viewer"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(
        AuthorizedContact, on_delete=models.CASCADE, related_name="roles"
    )
    group = models.ForeignKey(
        CustomerGroup, on_delete=models.CASCADE, related_name="user_roles"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USER)

    class Meta:
        unique_together = ["contact", "group"]

    def __str__(self):
        return f"{self.contact.display_name} \u2013 {self.get_role_display()} @ {self.group.name}"


class Plan(models.Model):
    class PlanType(models.TextChoices):
        BRONZE = "bronze", "Bronze"
        SILVER = "silver", "Silver"
        GOLD = "gold", "Gold"
        PLATINUM = "platinum", "Platinum"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    plan_type = models.CharField(
        max_length=20, choices=PlanType.choices, default=PlanType.BRONZE
    )
    description = models.TextField(blank=True, default="")
    support_provider = models.CharField(
        max_length=100, default="Microsoft Premier Support"
    )
    products_covered = models.TextField(
        default="",
        help_text="Products covered by this plan (e.g. Azure, M365, D365, On-prem)",
    )
    severity_levels = models.CharField(
        max_length=50,
        default="B, C",
        help_text="Supported severity levels (e.g. 'B, C' or 'A, B, C')",
    )
    support_hours = models.CharField(
        max_length=20,
        default="8x5",
        help_text="Support availability (e.g. '8x5' or '24x7x365')",
    )
    support_channels = models.CharField(
        max_length=100,
        default="Email",
        help_text="Available support channels (e.g. 'Email' or 'Dedicated phone number or Email')",
    )
    max_tenants = models.PositiveIntegerField(
        default=1, help_text="Maximum number of tenants allowed on this plan"
    )
    max_authorized_contacts = models.PositiveIntegerField(
        default=2, help_text="Maximum number of authorized contacts allowed"
    )
    support_requests_per_year = models.PositiveIntegerField(
        default=0,
        help_text="Support requests per trailing 12-month period (0 = Pay As You Go)",
    )
    crit_sit_management = models.BooleanField(
        default=False, verbose_name="Critical Situation Management"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_plan_type_display()})"


class Contract(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        CustomerGroup, on_delete=models.CASCADE, related_name="contracts"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="contracts")
    contract_number = models.CharField(max_length=50, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    auto_renewal = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.contract_number} \u2013 {self.group.name} ({self.get_status_display()})"

    @property
    def is_active(self):
        from django.utils import timezone

        today = timezone.now().date()
        return (
            self.status == self.Status.ACTIVE
            and self.start_date <= today <= self.end_date
        )


class AccountManager(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_id = models.CharField(max_length=50, unique=True)
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=255)
    phone_dedicated = models.CharField(
        max_length=50, blank=True, default="", verbose_name="Dedicated Phone"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return f"{self.display_name} ({self.employee_id})"


class ServiceExpert(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_id = models.CharField(max_length=50, unique=True)
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=255)
    phone_dedicated = models.CharField(
        max_length=50, blank=True, default="", verbose_name="Dedicated Phone"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return f"{self.display_name} ({self.employee_id})"


class GroupAccountManager(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        CustomerGroup,
        on_delete=models.CASCADE,
        related_name="account_manager_assignments",
    )
    account_manager = models.ForeignKey(
        AccountManager, on_delete=models.CASCADE, related_name="group_assignments"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_primary = models.BooleanField(
        default=False, help_text="Primary account manager for this group"
    )

    class Meta:
        unique_together = ["group", "account_manager"]
        verbose_name = "Group \u2194 Account Manager"
        verbose_name_plural = "Group \u2194 Account Manager Assignments"

    def __str__(self):
        primary = " (Primary)" if self.is_primary else ""
        return f"{self.account_manager.display_name} \u2192 {self.group.name}{primary}"


class GroupServiceExpert(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        CustomerGroup,
        on_delete=models.CASCADE,
        related_name="service_expert_assignments",
    )
    service_expert = models.ForeignKey(
        ServiceExpert, on_delete=models.CASCADE, related_name="group_assignments"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_primary = models.BooleanField(
        default=False, help_text="Primary service expert for this group"
    )

    class Meta:
        unique_together = ["group", "service_expert"]
        verbose_name = "Group \u2194 Service Expert"
        verbose_name_plural = "Group \u2194 Service Expert Assignments"

    def __str__(self):
        primary = " (Primary)" if self.is_primary else ""
        return f"{self.service_expert.display_name} \u2192 {self.group.name}{primary}"


class Ticket(models.Model):
    class Severity(models.TextChoices):
        A = "A", "Severity A – Critical"
        B = "B", "Severity B – Important"
        C = "C", "Severity C – Non-critical"

    class Status(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In Progress"
        WAITING_CUSTOMER = "waiting_customer", "Waiting on Customer"
        WAITING_VENDOR = "waiting_vendor", "Waiting on Vendor"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class ProductCategory(models.TextChoices):
        AZURE = "azure", "Azure"
        M365 = "m365", "Microsoft 365"
        D365 = "d365", "Dynamics 365"
        ONPREM = "onprem", "On-Premises"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference_number = models.CharField(max_length=20, unique=True, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="tickets")
    requester = models.ForeignKey(
        AuthorizedContact, on_delete=models.PROTECT, related_name="tickets"
    )
    assigned_to = models.ForeignKey(
        AccountManager,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    assigned_expert = models.ForeignKey(
        ServiceExpert,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    title = models.CharField(max_length=255)
    description = models.TextField()
    severity = models.CharField(
        max_length=1, choices=Severity.choices, default=Severity.C
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.MEDIUM
    )
    product_category = models.CharField(
        max_length=10, choices=ProductCategory.choices, default=ProductCategory.AZURE
    )
    microsoft_case_id = models.CharField(max_length=50, blank=True, default="")
    is_billable = models.BooleanField(default=False)
    internal_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self):
        return f"{self.reference_number} – {self.title}"

    @property
    def group(self):
        return self.tenant.entity.group

    def save(self, *args, **kwargs):
        if not self.reference_number:
            year = timezone.now().year
            last = (
                Ticket.objects.filter(reference_number__startswith=f"TKT-{year}-")
                .order_by("-reference_number")
                .values_list("reference_number", flat=True)
                .first()
            )
            if last:
                seq = int(last.split("-")[-1]) + 1
            else:
                seq = 1
            self.reference_number = f"TKT-{year}-{seq:05d}"
        super().save(*args, **kwargs)


class TicketComment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="comments"
    )
    author_name = models.CharField(max_length=255)
    author_email = models.EmailField(blank=True, default="")
    message = models.TextField()
    is_internal = models.BooleanField(
        default=False, help_text="Internal comments are visible to staff only"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        visibility = "internal" if self.is_internal else "public"
        return f"Comment by {self.author_name} ({visibility})"


class TicketUsagePeriod(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        CustomerGroup, on_delete=models.CASCADE, related_name="ticket_usage_periods"
    )
    contract = models.ForeignKey(
        Contract, on_delete=models.CASCADE, related_name="ticket_usage_periods"
    )
    period_start = models.DateField()
    period_end = models.DateField()
    ticket_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-period_start"]

    def __str__(self):
        return (
            f"{self.group.name}: {self.ticket_count} tickets "
            f"({self.period_start} – {self.period_end})"
        )

    @classmethod
    def get_current_usage(cls, group):
        """Count tickets created in the trailing 12 months for a group."""
        twelve_months_ago = timezone.now() - timezone.timedelta(days=365)
        return Ticket.objects.filter(
            tenant__entity__group=group,
            created_at__gte=twelve_months_ago,
        ).count()
