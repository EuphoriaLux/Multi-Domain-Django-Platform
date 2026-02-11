from django.contrib import admin

from power_up.admin import power_up_admin_site
from .models import (
    CustomerGroup,
    Entity,
    Tenant,
    AuthorizedContact,
    ContactTenantPermission,
    UserRole,
    Plan,
    Contract,
    AccountManager,
    GroupAccountManager,
    ServiceExpert,
    GroupServiceExpert,
    Ticket,
    TicketComment,
    TicketUsagePeriod,
)

# -- Inlines -----------------------------------------------------------------


class EntityInline(admin.TabularInline):
    model = Entity
    extra = 0
    fields = ["name", "description", "is_active"]
    show_change_link = True


class TenantInline(admin.TabularInline):
    model = Tenant
    extra = 0
    fields = [
        "company_name",
        "tenant_id_azure",
        "primary_domain",
        "microsoft_tenant_domain",
        "environment_type",
        "has_azure",
        "gdap_link",
        "is_active",
    ]
    show_change_link = True


class AuthorizedContactInline(admin.TabularInline):
    model = AuthorizedContact
    extra = 0
    fields = [
        "display_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "job_title",
        "is_active",
    ]
    show_change_link = True


class ContactTenantPermissionInline(admin.TabularInline):
    model = ContactTenantPermission
    extra = 0
    fields = ["tenant", "can_create_tickets", "can_view_tickets", "granted_at"]
    readonly_fields = ["granted_at"]


class ContactTenantPermissionByTenantInline(admin.TabularInline):
    model = ContactTenantPermission
    extra = 0
    fk_name = "tenant"
    fields = ["contact", "can_create_tickets", "can_view_tickets", "granted_at"]
    readonly_fields = ["granted_at"]
    verbose_name = "Authorized Contact Permission"
    verbose_name_plural = "Authorized Contact Permissions"


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 0
    fields = ["group", "role"]


class ContractInline(admin.TabularInline):
    model = Contract
    extra = 0
    fields = [
        "contract_number",
        "plan",
        "start_date",
        "end_date",
        "status",
        "auto_renewal",
    ]
    show_change_link = True


class GroupAccountManagerInline(admin.TabularInline):
    model = GroupAccountManager
    extra = 0
    fields = ["account_manager", "is_primary", "assigned_at"]
    readonly_fields = ["assigned_at"]


class GroupAccountManagerByManagerInline(admin.TabularInline):
    model = GroupAccountManager
    extra = 0
    fk_name = "account_manager"
    fields = ["group", "is_primary", "assigned_at"]
    readonly_fields = ["assigned_at"]
    verbose_name = "Group Assignment"
    verbose_name_plural = "Group Assignments"


class GroupServiceExpertInline(admin.TabularInline):
    model = GroupServiceExpert
    extra = 0
    fields = ["service_expert", "is_primary", "assigned_at"]
    readonly_fields = ["assigned_at"]


class GroupServiceExpertByExpertInline(admin.TabularInline):
    model = GroupServiceExpert
    extra = 0
    fk_name = "service_expert"
    fields = ["group", "is_primary", "assigned_at"]
    readonly_fields = ["assigned_at"]
    verbose_name = "Group Assignment"
    verbose_name_plural = "Group Assignments"


# -- ModelAdmins -------------------------------------------------------------


class CustomerGroupAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "is_active",
        "onboarding_status",
        "entity_count",
        "active_contract_count",
        "created_at",
    ]
    list_filter = ["is_active", "onboarding_status"]
    search_fields = ["name", "description"]
    inlines = [
        EntityInline,
        AuthorizedContactInline,
        ContractInline,
        GroupAccountManagerInline,
        GroupServiceExpertInline,
    ]

    @admin.display(description="Entities")
    def entity_count(self, obj):
        return obj.entities.count()

    @admin.display(description="Active Contracts")
    def active_contract_count(self, obj):
        return obj.contracts.filter(status="active").count()


class EntityAdmin(admin.ModelAdmin):
    list_display = ["name", "group", "is_active", "tenant_count", "created_at"]
    list_filter = ["is_active", "group"]
    search_fields = ["name", "description", "group__name"]
    list_select_related = ["group"]
    inlines = [TenantInline]

    @admin.display(description="Tenants")
    def tenant_count(self, obj):
        return obj.tenants.count()


class TenantAdmin(admin.ModelAdmin):
    list_display = [
        "company_name",
        "primary_domain",
        "tenant_id_azure",
        "environment_type",
        "has_azure",
        "has_gdap_link",
        "entity",
        "group_name",
        "is_active",
    ]
    list_filter = ["environment_type", "is_active", "has_azure", "entity__group"]
    search_fields = [
        "company_name",
        "primary_domain",
        "tenant_id_azure",
        "entity__name",
        "entity__group__name",
    ]
    list_select_related = ["entity", "entity__group"]
    inlines = [ContactTenantPermissionByTenantInline]

    @admin.display(description="GDAP", boolean=True)
    def has_gdap_link(self, obj):
        return bool(obj.gdap_link)

    @admin.display(description="Group", ordering="entity__group__name")
    def group_name(self, obj):
        return obj.entity.group.name


class AuthorizedContactAdmin(admin.ModelAdmin):
    list_display = [
        "display_name",
        "email",
        "phone",
        "group",
        "is_active",
        "created_at",
    ]
    list_filter = ["is_active", "group"]
    search_fields = ["display_name", "email", "entra_object_id", "group__name"]
    list_select_related = ["group"]
    inlines = [ContactTenantPermissionInline, UserRoleInline]


class ContactTenantPermissionAdmin(admin.ModelAdmin):
    list_display = [
        "contact",
        "tenant",
        "can_create_tickets",
        "can_view_tickets",
        "granted_at",
    ]
    list_filter = ["can_create_tickets", "can_view_tickets"]
    search_fields = [
        "contact__display_name",
        "contact__email",
        "tenant__company_name",
    ]
    list_select_related = ["contact", "tenant"]


class UserRoleAdmin(admin.ModelAdmin):
    list_display = ["contact", "group", "role"]
    list_filter = ["role", "group"]
    search_fields = ["contact__display_name", "contact__email", "group__name"]
    list_select_related = ["contact", "group"]


class PlanAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "plan_type",
        "support_hours",
        "max_tenants",
        "max_authorized_contacts",
        "support_requests_per_year",
        "crit_sit_management",
    ]
    list_filter = ["plan_type", "crit_sit_management"]
    search_fields = ["name"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = [
        (
            "General Info",
            {"fields": ["name", "plan_type", "description", "support_provider"]},
        ),
        (
            "Support Details",
            {
                "fields": [
                    "products_covered",
                    "severity_levels",
                    "support_hours",
                    "support_channels",
                    "crit_sit_management",
                ]
            },
        ),
        (
            "Limits & Quotas",
            {
                "fields": [
                    "max_tenants",
                    "max_authorized_contacts",
                    "support_requests_per_year",
                ]
            },
        ),
        (
            "Timestamps",
            {"fields": ["created_at", "updated_at"], "classes": ["collapse"]},
        ),
    ]


class ContractAdmin(admin.ModelAdmin):
    list_display = [
        "contract_number",
        "group",
        "plan",
        "start_date",
        "end_date",
        "status",
        "auto_renewal",
    ]
    list_filter = ["status", "auto_renewal", "plan", "group"]
    search_fields = ["contract_number", "group__name"]
    list_select_related = ["group", "plan"]
    date_hierarchy = "start_date"


class AccountManagerAdmin(admin.ModelAdmin):
    list_display = [
        "display_name",
        "employee_id",
        "email",
        "phone_dedicated",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = ["display_name", "email", "employee_id"]
    inlines = [GroupAccountManagerByManagerInline]


class ServiceExpertAdmin(admin.ModelAdmin):
    list_display = [
        "display_name",
        "employee_id",
        "email",
        "phone_dedicated",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = ["display_name", "email", "employee_id"]
    inlines = [GroupServiceExpertByExpertInline]


class GroupServiceExpertAdmin(admin.ModelAdmin):
    list_display = ["service_expert", "group", "is_primary", "assigned_at"]
    list_filter = ["is_primary", "group"]
    search_fields = [
        "service_expert__display_name",
        "service_expert__email",
        "group__name",
    ]
    list_select_related = ["service_expert", "group"]


class GroupAccountManagerAdmin(admin.ModelAdmin):
    list_display = ["account_manager", "group", "is_primary", "assigned_at"]
    list_filter = ["is_primary", "group"]
    search_fields = [
        "account_manager__display_name",
        "account_manager__email",
        "group__name",
    ]
    list_select_related = ["account_manager", "group"]


# -- Ticket Admin ------------------------------------------------------------


class TicketCommentInline(admin.TabularInline):
    model = TicketComment
    extra = 0
    fields = ["author_name", "author_email", "message", "is_internal", "created_at"]
    readonly_fields = ["created_at"]


class TicketAdmin(admin.ModelAdmin):
    list_display = [
        "reference_number",
        "title",
        "tenant_name",
        "group_name",
        "severity",
        "status",
        "priority",
        "assigned_to",
        "assigned_expert",
        "created_at",
    ]
    list_filter = ["status", "severity", "priority", "product_category", "is_billable"]
    search_fields = [
        "reference_number",
        "title",
        "description",
        "tenant__company_name",
        "tenant__entity__group__name",
    ]
    list_select_related = ["tenant__entity__group", "assigned_to", "assigned_expert", "requester"]
    readonly_fields = [
        "reference_number",
        "created_at",
        "updated_at",
        "first_response_at",
        "resolved_at",
        "closed_at",
    ]
    inlines = [TicketCommentInline]
    fieldsets = [
        (
            "Ticket Info",
            {
                "fields": [
                    "reference_number",
                    "title",
                    "description",
                    "tenant",
                    "requester",
                ]
            },
        ),
        (
            "Classification",
            {
                "fields": [
                    "severity",
                    "status",
                    "priority",
                    "product_category",
                ]
            },
        ),
        (
            "Assignment & Escalation",
            {
                "fields": [
                    "assigned_to",
                    "assigned_expert",
                    "microsoft_case_id",
                    "is_billable",
                ]
            },
        ),
        (
            "Internal",
            {"fields": ["internal_notes"], "classes": ["collapse"]},
        ),
        (
            "Timestamps",
            {
                "fields": [
                    "created_at",
                    "updated_at",
                    "first_response_at",
                    "resolved_at",
                    "closed_at",
                ],
                "classes": ["collapse"],
            },
        ),
    ]

    @admin.display(description="Tenant", ordering="tenant__company_name")
    def tenant_name(self, obj):
        return obj.tenant.company_name

    @admin.display(description="Group", ordering="tenant__entity__group__name")
    def group_name(self, obj):
        return obj.tenant.entity.group.name


class TicketCommentAdmin(admin.ModelAdmin):
    list_display = [
        "ticket",
        "author_name",
        "is_internal",
        "created_at",
    ]
    list_filter = ["is_internal"]
    search_fields = ["author_name", "message", "ticket__reference_number"]
    list_select_related = ["ticket"]


class TicketUsagePeriodAdmin(admin.ModelAdmin):
    list_display = [
        "group",
        "contract",
        "period_start",
        "period_end",
        "ticket_count",
    ]
    list_filter = ["group"]
    list_select_related = ["group", "contract"]


# -- Register on power_up_admin_site ----------------------------------------

power_up_admin_site.register(Ticket, TicketAdmin)
power_up_admin_site.register(TicketComment, TicketCommentAdmin)
power_up_admin_site.register(TicketUsagePeriod, TicketUsagePeriodAdmin)
power_up_admin_site.register(CustomerGroup, CustomerGroupAdmin)
power_up_admin_site.register(Entity, EntityAdmin)
power_up_admin_site.register(Tenant, TenantAdmin)
power_up_admin_site.register(AuthorizedContact, AuthorizedContactAdmin)
power_up_admin_site.register(ContactTenantPermission, ContactTenantPermissionAdmin)
power_up_admin_site.register(UserRole, UserRoleAdmin)
power_up_admin_site.register(Plan, PlanAdmin)
power_up_admin_site.register(Contract, ContractAdmin)
power_up_admin_site.register(AccountManager, AccountManagerAdmin)
power_up_admin_site.register(GroupAccountManager, GroupAccountManagerAdmin)
power_up_admin_site.register(ServiceExpert, ServiceExpertAdmin)
power_up_admin_site.register(GroupServiceExpert, GroupServiceExpertAdmin)
