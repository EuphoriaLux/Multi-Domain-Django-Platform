# Extend Clearstream International (Platinum) with realistic data
# for a major financial institution: multiple entities, tenants, contacts.

import uuid

from django.db import migrations

# Existing IDs from 0004
GRP_CLEARSTREAM = uuid.UUID("a0000001-0000-0000-0000-000000000006")
ENT_CLEARSTREAM = uuid.UUID("b0000001-0000-0000-0000-000000000009")
TEN_CLEARSTREAM = uuid.UUID("c0000001-0000-0000-0000-000000000011")
CON_PAUL = uuid.UUID("d0000001-0000-0000-0000-000000000015")
CON_ELENA = uuid.UUID("d0000001-0000-0000-0000-000000000016")

# New entities
ENT_CS_BANKING = uuid.UUID("b0000002-0000-0000-0000-000000000001")
ENT_CS_FUND = uuid.UUID("b0000002-0000-0000-0000-000000000002")
ENT_CS_SERVICES = uuid.UUID("b0000002-0000-0000-0000-000000000003")
ENT_CS_HOLDING = uuid.UUID("b0000002-0000-0000-0000-000000000004")

# New tenants
TEN_CS_BANKING_PROD = uuid.UUID("c0000002-0000-0000-0000-000000000001")
TEN_CS_BANKING_DEV = uuid.UUID("c0000002-0000-0000-0000-000000000002")
TEN_CS_FUND_PROD = uuid.UUID("c0000002-0000-0000-0000-000000000003")
TEN_CS_FUND_UAT = uuid.UUID("c0000002-0000-0000-0000-000000000004")
TEN_CS_SERVICES_PROD = uuid.UUID("c0000002-0000-0000-0000-000000000005")
TEN_CS_HOLDING_PROD = uuid.UUID("c0000002-0000-0000-0000-000000000006")
TEN_CS_MAIN_DEV = uuid.UUID("c0000002-0000-0000-0000-000000000007")
TEN_CS_MAIN_UAT = uuid.UUID("c0000002-0000-0000-0000-000000000008")
TEN_CS_MAIN_SANDBOX = uuid.UUID("c0000002-0000-0000-0000-000000000009")

# New contacts
CON_MARCUS = uuid.UUID("d0000002-0000-0000-0000-000000000001")
CON_ISABELLE = uuid.UUID("d0000002-0000-0000-0000-000000000002")
CON_DIETER = uuid.UUID("d0000002-0000-0000-0000-000000000003")
CON_NATHALIE = uuid.UUID("d0000002-0000-0000-0000-000000000004")
CON_YVES = uuid.UUID("d0000002-0000-0000-0000-000000000005")
CON_CARLA = uuid.UUID("d0000002-0000-0000-0000-000000000006")
CON_ANDREAS = uuid.UUID("d0000002-0000-0000-0000-000000000007")
CON_SANDRA = uuid.UUID("d0000002-0000-0000-0000-000000000008")
CON_LAURENT = uuid.UUID("d0000002-0000-0000-0000-000000000009")
CON_KATRIN = uuid.UUID("d0000002-0000-0000-0000-000000000010")
CON_PHILIPPE = uuid.UUID("d0000002-0000-0000-0000-000000000011")
CON_MARIE = uuid.UUID("d0000002-0000-0000-0000-000000000012")

ALL_NEW_ENTITY_IDS = [ENT_CS_BANKING, ENT_CS_FUND, ENT_CS_SERVICES, ENT_CS_HOLDING]
ALL_NEW_TENANT_IDS = [
    TEN_CS_BANKING_PROD,
    TEN_CS_BANKING_DEV,
    TEN_CS_FUND_PROD,
    TEN_CS_FUND_UAT,
    TEN_CS_SERVICES_PROD,
    TEN_CS_HOLDING_PROD,
    TEN_CS_MAIN_DEV,
    TEN_CS_MAIN_UAT,
    TEN_CS_MAIN_SANDBOX,
]
ALL_NEW_CONTACT_IDS = [
    CON_MARCUS,
    CON_ISABELLE,
    CON_DIETER,
    CON_NATHALIE,
    CON_YVES,
    CON_CARLA,
    CON_ANDREAS,
    CON_SANDRA,
    CON_LAURENT,
    CON_KATRIN,
    CON_PHILIPPE,
    CON_MARIE,
]


def seed_clearstream(apps, schema_editor):
    Entity = apps.get_model("crm", "Entity")
    Tenant = apps.get_model("crm", "Tenant")
    AuthorizedContact = apps.get_model("crm", "AuthorizedContact")
    ContactTenantPermission = apps.get_model("crm", "ContactTenantPermission")
    UserRole = apps.get_model("crm", "UserRole")

    # -------------------------------------------------------------------
    # 4 new entities (total: 5 with existing Clearstream International S.A.)
    # -------------------------------------------------------------------
    entities_data = [
        (
            ENT_CS_BANKING,
            "Clearstream Banking S.A.",
            "Regulated banking entity — settlement & custody",
        ),
        (
            ENT_CS_FUND,
            "Clearstream Fund Centre",
            "Investment fund order routing & settlement",
        ),
        (
            ENT_CS_SERVICES,
            "Clearstream Global Securities Services",
            "Global custody, collateral & lending",
        ),
        (
            ENT_CS_HOLDING,
            "Clearstream Holding AG",
            "Deutsche Boerse subsidiary holding — Frankfurt",
        ),
    ]
    for eid, name, desc in entities_data:
        Entity.objects.update_or_create(
            id=eid,
            defaults={
                "group_id": GRP_CLEARSTREAM,
                "name": name,
                "description": desc,
                "is_active": True,
            },
        )

    # -------------------------------------------------------------------
    # 9 new tenants (total: 10 with existing prod tenant)
    # -------------------------------------------------------------------
    tenants_data = [
        # Banking entity — prod + dev
        (
            TEN_CS_BANKING_PROD,
            ENT_CS_BANKING,
            "6a2b3c4d-0006-0006-0006-000000000002",
            "Clearstream Banking",
            "production",
            "csbanking.onmicrosoft.com",
        ),
        (
            TEN_CS_BANKING_DEV,
            ENT_CS_BANKING,
            "6a2b3c4d-0006-0006-0006-000000000003",
            "Clearstream Banking (Dev)",
            "development",
            "csbankingdev.onmicrosoft.com",
        ),
        # Fund Centre — prod + UAT
        (
            TEN_CS_FUND_PROD,
            ENT_CS_FUND,
            "6a2b3c4d-0006-0006-0006-000000000004",
            "Clearstream Fund Centre",
            "production",
            "csfundcentre.onmicrosoft.com",
        ),
        (
            TEN_CS_FUND_UAT,
            ENT_CS_FUND,
            "6a2b3c4d-0006-0006-0006-000000000005",
            "Clearstream Fund Centre (UAT)",
            "test",
            "csfunduat.onmicrosoft.com",
        ),
        # GSS — prod
        (
            TEN_CS_SERVICES_PROD,
            ENT_CS_SERVICES,
            "6a2b3c4d-0006-0006-0006-000000000006",
            "Clearstream Global Securities",
            "production",
            "csgss.onmicrosoft.com",
        ),
        # Holding — prod
        (
            TEN_CS_HOLDING_PROD,
            ENT_CS_HOLDING,
            "6a2b3c4d-0006-0006-0006-000000000007",
            "Clearstream Holding AG",
            "production",
            "csholding.onmicrosoft.com",
        ),
        # Main entity (existing ENT_CLEARSTREAM) — dev, UAT, sandbox
        (
            TEN_CS_MAIN_DEV,
            ENT_CLEARSTREAM,
            "6a2b3c4d-0006-0006-0006-000000000008",
            "Clearstream International (Dev)",
            "development",
            "csinternationaldev.onmicrosoft.com",
        ),
        (
            TEN_CS_MAIN_UAT,
            ENT_CLEARSTREAM,
            "6a2b3c4d-0006-0006-0006-000000000009",
            "Clearstream International (UAT)",
            "test",
            "csinternationaluat.onmicrosoft.com",
        ),
        (
            TEN_CS_MAIN_SANDBOX,
            ENT_CLEARSTREAM,
            "6a2b3c4d-0006-0006-0006-000000000010",
            "Clearstream International (Sandbox)",
            "sandbox",
            "csinternationalsb.onmicrosoft.com",
        ),
    ]
    for tid, eid, azure_id, name, env, domain in tenants_data:
        Tenant.objects.update_or_create(
            id=tid,
            defaults={
                "entity_id": eid,
                "tenant_id_azure": azure_id,
                "company_name": name,
                "environment_type": env,
                "primary_domain": domain,
                "is_active": True,
            },
        )

    # -------------------------------------------------------------------
    # 12 new contacts (total: 14 with Paul & Elena)
    # -------------------------------------------------------------------
    contacts_data = [
        # IT Infrastructure team
        (
            CON_MARCUS,
            "Marcus Engel",
            "m.engel@clearstream.com",
            "+352 621 610 001",
            "aad-marcus-cs01",
        ),
        (
            CON_ISABELLE,
            "Isabelle Dumont",
            "i.dumont@clearstream.com",
            "+352 621 610 002",
            "aad-isabelle-cs02",
        ),
        (
            CON_DIETER,
            "Dieter Braun",
            "d.braun@clearstream.com",
            "+49 69 211 001",
            "aad-dieter-cs03",
        ),
        # Security & Compliance
        (
            CON_NATHALIE,
            "Nathalie Reuter",
            "n.reuter@clearstream.com",
            "+352 621 610 004",
            "aad-nathalie-cs04",
        ),
        (
            CON_YVES,
            "Yves Koener",
            "y.koener@clearstream.com",
            "+352 621 610 005",
            "aad-yves-cs05",
        ),
        # Application Support
        (
            CON_CARLA,
            "Carla Ferreira",
            "c.ferreira@clearstream.com",
            "+352 621 610 006",
            "aad-carla-cs06",
        ),
        (
            CON_ANDREAS,
            "Andreas Wirtz",
            "a.wirtz@clearstream.com",
            "+49 69 211 007",
            "aad-andreas-cs07",
        ),
        (
            CON_SANDRA,
            "Sandra Pauly",
            "s.pauly@clearstream.com",
            "+352 621 610 008",
            "aad-sandra-cs08",
        ),
        # Business-side contacts
        (
            CON_LAURENT,
            "Laurent Heinz",
            "l.heinz@clearstream.com",
            "+352 621 610 009",
            "aad-laurent-cs09",
        ),
        (
            CON_KATRIN,
            "Katrin Schaefer",
            "k.schaefer@clearstream.com",
            "+49 69 211 010",
            "aad-katrin-cs10",
        ),
        # Management
        (
            CON_PHILIPPE,
            "Philippe Serafin",
            "p.serafin@clearstream.com",
            "+352 621 610 011",
            "aad-philippe-cs11",
        ),
        (
            CON_MARIE,
            "Marie-Claire Stein",
            "mc.stein@clearstream.com",
            "+352 621 610 012",
            "aad-marie-cs12",
        ),
    ]
    for cid, name, email, phone, entra_id in contacts_data:
        AuthorizedContact.objects.update_or_create(
            id=cid,
            defaults={
                "group_id": GRP_CLEARSTREAM,
                "display_name": name,
                "email": email,
                "phone": phone,
                "entra_object_id": entra_id,
                "is_active": True,
            },
        )

    # -------------------------------------------------------------------
    # Tenant permissions — realistic tiered access
    # -------------------------------------------------------------------

    # All production tenants
    prod_tenants = [
        TEN_CLEARSTREAM,
        TEN_CS_BANKING_PROD,
        TEN_CS_FUND_PROD,
        TEN_CS_SERVICES_PROD,
        TEN_CS_HOLDING_PROD,
    ]
    dev_tenants = [TEN_CS_BANKING_DEV, TEN_CS_MAIN_DEV]
    test_tenants = [TEN_CS_FUND_UAT, TEN_CS_MAIN_UAT]
    sandbox_tenants = [TEN_CS_MAIN_SANDBOX]
    all_tenants = prod_tenants + dev_tenants + test_tenants + sandbox_tenants

    permissions = []

    # Paul & Elena (existing admins) — full access everywhere
    for contact_id in [CON_PAUL, CON_ELENA]:
        for tenant_id in all_tenants:
            permissions.append((contact_id, tenant_id, True, True))

    # Marcus & Isabelle (IT Infra) — full access all environments
    for contact_id in [CON_MARCUS, CON_ISABELLE]:
        for tenant_id in all_tenants:
            permissions.append((contact_id, tenant_id, True, True))

    # Dieter (IT Infra Frankfurt) — Holding + Banking only
    for tenant_id in [TEN_CS_HOLDING_PROD, TEN_CS_BANKING_PROD, TEN_CS_BANKING_DEV]:
        permissions.append((CON_DIETER, tenant_id, True, True))

    # Nathalie & Yves (Security) — view-only on all prod, create on main
    for tenant_id in prod_tenants:
        permissions.append((CON_NATHALIE, tenant_id, False, True))
        permissions.append((CON_YVES, tenant_id, False, True))
    permissions.append((CON_NATHALIE, TEN_CLEARSTREAM, True, True))
    permissions.append((CON_YVES, TEN_CLEARSTREAM, True, True))

    # Carla, Andreas, Sandra (App Support) — prod + test
    for contact_id in [CON_CARLA, CON_ANDREAS, CON_SANDRA]:
        for tenant_id in prod_tenants + test_tenants:
            permissions.append((contact_id, tenant_id, True, True))

    # Laurent & Katrin (Business) — main prod + Fund Centre prod only
    for contact_id in [CON_LAURENT, CON_KATRIN]:
        for tenant_id in [TEN_CLEARSTREAM, TEN_CS_FUND_PROD]:
            permissions.append((contact_id, tenant_id, True, True))

    # Philippe (Management) — all prod, view only
    for tenant_id in prod_tenants:
        permissions.append((CON_PHILIPPE, tenant_id, False, True))

    # Marie-Claire (Management) — all prod, full
    for tenant_id in prod_tenants:
        permissions.append((CON_MARIE, tenant_id, True, True))

    for contact_id, tenant_id, can_create, can_view in permissions:
        ContactTenantPermission.objects.update_or_create(
            contact_id=contact_id,
            tenant_id=tenant_id,
            defaults={
                "can_create_tickets": can_create,
                "can_view_tickets": can_view,
            },
        )

    # -------------------------------------------------------------------
    # User roles
    # -------------------------------------------------------------------
    roles = [
        # Paul & Elena already have roles from 0004
        (CON_MARCUS, "manager"),  # IT Infra lead
        (CON_ISABELLE, "manager"),  # IT Infra
        (CON_DIETER, "user"),  # Frankfurt IT
        (CON_NATHALIE, "manager"),  # Security lead
        (CON_YVES, "user"),  # Security
        (CON_CARLA, "user"),  # App Support
        (CON_ANDREAS, "user"),  # App Support
        (CON_SANDRA, "user"),  # App Support
        (CON_LAURENT, "viewer"),  # Business
        (CON_KATRIN, "viewer"),  # Business
        (CON_PHILIPPE, "admin"),  # Management — CTO-level
        (CON_MARIE, "manager"),  # Management — IT Director
    ]
    for contact_id, role in roles:
        UserRole.objects.update_or_create(
            contact_id=contact_id,
            group_id=GRP_CLEARSTREAM,
            defaults={"role": role},
        )


def remove_clearstream(apps, schema_editor):
    ContactTenantPermission = apps.get_model("crm", "ContactTenantPermission")
    UserRole = apps.get_model("crm", "UserRole")
    AuthorizedContact = apps.get_model("crm", "AuthorizedContact")
    Tenant = apps.get_model("crm", "Tenant")
    Entity = apps.get_model("crm", "Entity")

    # Remove permissions for new contacts
    ContactTenantPermission.objects.filter(contact_id__in=ALL_NEW_CONTACT_IDS).delete()
    # Remove permissions for existing contacts on new tenants
    ContactTenantPermission.objects.filter(tenant_id__in=ALL_NEW_TENANT_IDS).delete()
    UserRole.objects.filter(contact_id__in=ALL_NEW_CONTACT_IDS).delete()
    AuthorizedContact.objects.filter(id__in=ALL_NEW_CONTACT_IDS).delete()
    Tenant.objects.filter(id__in=ALL_NEW_TENANT_IDS).delete()
    Entity.objects.filter(id__in=ALL_NEW_ENTITY_IDS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0004_seed_sample_crm_data"),
    ]

    operations = [
        migrations.RunPython(seed_clearstream, remove_clearstream),
    ]
