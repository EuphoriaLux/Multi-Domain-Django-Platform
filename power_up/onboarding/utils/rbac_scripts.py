"""
RBAC PowerShell script generator — ported from rbacUtils.ts / templateGenerator.ts.

Generates PowerShell scripts that assign Azure RBAC roles to Schneider IT
support groups across all subscriptions in a tenant.
"""

# Entra ID Object IDs for the two agent groups
HELP_DESK_AGENTS_OBJECT_ID = "b6770181-d9f5-4818-b5b1-ea51cd9f66e5"
ADMIN_AGENTS_OBJECT_ID = "9a838974-22d3-415b-8136-c790e285afeb"


def generate_rbac_script(tenants):
    """
    Generate a PowerShell script for Azure RBAC role assignments.

    Only includes tenants where ``has_azure`` is True.
    Substitutes the tenant's ``microsoft_tenant_domain`` (or ``tenant_id_azure``)
    as the TenantID parameter.

    Args:
        tenants: queryset or list of Tenant model instances.

    Returns:
        A string containing the full PowerShell script, or ``""`` if no
        Azure tenants are present.
    """
    azure_tenants = [t for t in tenants if t.has_azure]
    if not azure_tenants:
        return ""

    scripts = []
    for tenant in azure_tenants:
        tenant_id = tenant.tenant_id_azure or tenant.microsoft_tenant_domain
        scripts.append(_single_tenant_script(tenant.company_name, tenant_id))

    return "\n\n".join(scripts)


def _single_tenant_script(company_name, tenant_id):
    return f"""\
# ─── RBAC Setup for {company_name} ───
Connect-AzAccount -TenantID "{tenant_id}"

$subscriptions = Get-AzSubscription
foreach ($subscription in $subscriptions) {{
    Set-AzContext -SubscriptionId $subscription.Id

    # Help Desk Agents — Support Request Contributor
    New-AzRoleAssignment `
        -ObjectID "{HELP_DESK_AGENTS_OBJECT_ID}" `
        -RoleDefinitionName "Support Request Contributor" `
        -ObjectType "ForeignGroup" `
        -ErrorAction SilentlyContinue

    # Admin Agents — Owner
    New-AzRoleAssignment `
        -ObjectID "{ADMIN_AGENTS_OBJECT_ID}" `
        -RoleDefinitionName "Owner" `
        -ObjectType "ForeignGroup" `
        -ErrorAction SilentlyContinue

    # Validation
    Get-AzRoleAssignment -ObjectId "{HELP_DESK_AGENTS_OBJECT_ID}" | Format-Table DisplayName, RoleDefinitionName, Scope
    Get-AzRoleAssignment -ObjectId "{ADMIN_AGENTS_OBJECT_ID}" | Format-Table DisplayName, RoleDefinitionName, Scope
}}"""
