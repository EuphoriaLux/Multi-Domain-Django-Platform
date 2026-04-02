"""
Seed onboarding test data — populates service experts, GDAP links, Azure flags,
and sample onboarding sessions. Idempotent (safe to run multiple times).

Usage:
    python manage.py seed_onboarding_data
"""

import uuid
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from power_up.crm.models import (
    AuthorizedContact,
    CustomerGroup,
    GroupServiceExpert,
    ServiceExpert,
    Tenant,
)
from power_up.onboarding.models import OnboardingSession

User = get_user_model()

# ---------------------------------------------------------------------------
# Deterministic UUIDs — match the CRM seed migrations
# ---------------------------------------------------------------------------

# Customer Groups (from 0004_seed_sample_crm_data)
GRP_ARCELOR = uuid.UUID("a0000001-0000-0000-0000-000000000001")
GRP_FERRERO = uuid.UUID("a0000001-0000-0000-0000-000000000002")
GRP_SES = uuid.UUID("a0000001-0000-0000-0000-000000000003")
GRP_CACTUS = uuid.UUID("a0000001-0000-0000-0000-000000000004")
GRP_POST = uuid.UUID("a0000001-0000-0000-0000-000000000005")
GRP_CLEARSTREAM = uuid.UUID("a0000001-0000-0000-0000-000000000006")
GRP_GOODYEAR = uuid.UUID("a0000001-0000-0000-0000-000000000007")
GRP_ENCEVO = uuid.UUID("a0000001-0000-0000-0000-000000000008")

# Tenants (from 0004)
TEN_ARCELOR_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000001")
TEN_ARCELOR_DEV = uuid.UUID("c0000001-0000-0000-0000-000000000002")
TEN_ARCELOR_BE = uuid.UUID("c0000001-0000-0000-0000-000000000003")
TEN_FERRERO_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000004")
TEN_FERRERO_DE = uuid.UUID("c0000001-0000-0000-0000-000000000005")
TEN_SES_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000006")
TEN_SES_SAND = uuid.UUID("c0000001-0000-0000-0000-000000000007")
TEN_CACTUS_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000008")
TEN_POST_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000009")
TEN_POST_TELE = uuid.UUID("c0000001-0000-0000-0000-000000000010")
TEN_CLEARSTREAM = uuid.UUID("c0000001-0000-0000-0000-000000000011")
TEN_GOODYEAR = uuid.UUID("c0000001-0000-0000-0000-000000000012")
TEN_ENCEVO_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000013")
TEN_ENOVOS_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000014")

# Clearstream extended tenants (from 0005)
TEN_CS_BANKING_PROD = uuid.UUID("c0000002-0000-0000-0000-000000000001")
TEN_CS_FUND_PROD = uuid.UUID("c0000002-0000-0000-0000-000000000003")
TEN_CS_SERVICES_PROD = uuid.UUID("c0000002-0000-0000-0000-000000000005")
TEN_CS_HOLDING_PROD = uuid.UUID("c0000002-0000-0000-0000-000000000006")

# Contacts (from 0004 — admin-role contacts per group)
CON_MARC = uuid.UUID("d0000001-0000-0000-0000-000000000001")      # ArcelorMittal admin
CON_LUC = uuid.UUID("d0000001-0000-0000-0000-000000000005")       # Ferrero admin
CON_SARAH = uuid.UUID("d0000001-0000-0000-0000-000000000008")     # SES admin
CON_NICOLAS = uuid.UUID("d0000001-0000-0000-0000-000000000011")   # Cactus admin
CON_FRANK = uuid.UUID("d0000001-0000-0000-0000-000000000013")     # POST admin
CON_PAUL = uuid.UUID("d0000001-0000-0000-0000-000000000015")      # Clearstream admin
CON_ROMAIN = uuid.UUID("d0000001-0000-0000-0000-000000000017")    # Goodyear admin
CON_CHRISTINE = uuid.UUID("d0000001-0000-0000-0000-000000000018") # Encevo admin

# New deterministic UUIDs for onboarding-specific data
SE_THOMAS = uuid.UUID("ee000001-0000-0000-0000-000000000001")
SE_LAURA = uuid.UUID("ee000001-0000-0000-0000-000000000002")
SE_KEVIN = uuid.UUID("ee000001-0000-0000-0000-000000000003")

SESSION_ARCELOR = uuid.UUID("55000001-0000-0000-0000-000000000001")
SESSION_CLEARSTREAM = uuid.UUID("55000001-0000-0000-0000-000000000002")
SESSION_CACTUS = uuid.UUID("55000001-0000-0000-0000-000000000003")


class Command(BaseCommand):
    help = "Seed onboarding test data: service experts, GDAP links, Azure flags, sample sessions"

    def handle(self, *args, **options):
        self.stdout.write("Seeding onboarding data...")

        self._seed_service_experts()
        self._seed_tenant_onboarding_fields()
        self._seed_onboarding_sessions()

        self.stdout.write(self.style.SUCCESS("Done! Onboarding test data seeded."))

    def _seed_service_experts(self):
        """Create 3 service experts and assign them to customer groups."""
        self.stdout.write("  Creating service experts...")

        experts = {}
        experts_data = [
            (
                SE_THOMAS,
                "SCH-SE-001",
                "t.schneider@schneider-itm.lu",
                "Thomas Schneider",
                "+352 26 00 20 01",
            ),
            (
                SE_LAURA,
                "SCH-SE-002",
                "l.muller@schneider-itm.lu",
                "Laura Muller",
                "+352 26 00 20 02",
            ),
            (
                SE_KEVIN,
                "SCH-SE-003",
                "k.hansen@schneider-itm.lu",
                "Kevin Hansen",
                "+352 26 00 20 03",
            ),
        ]

        for se_id, emp_id, email, name, phone in experts_data:
            obj, created = ServiceExpert.objects.update_or_create(
                id=se_id,
                defaults={
                    "employee_id": emp_id,
                    "email": email,
                    "display_name": name,
                    "phone_dedicated": phone,
                    "is_active": True,
                },
            )
            experts[se_id] = obj
            status = "created" if created else "updated"
            self.stdout.write(f"    {name} ({status})")

        # Assign experts to groups (Thomas = Platinum, Laura = Gold, Kevin = Silver/Bronze)
        assignments = [
            # Thomas — primary for Platinum clients
            (GRP_ARCELOR, SE_THOMAS, True),
            (GRP_CLEARSTREAM, SE_THOMAS, True),
            # Laura — primary for Gold clients
            (GRP_FERRERO, SE_LAURA, True),
            (GRP_SES, SE_LAURA, True),
            # Kevin — primary for Silver & Bronze
            (GRP_POST, SE_KEVIN, True),
            (GRP_CACTUS, SE_KEVIN, True),
            (GRP_GOODYEAR, SE_KEVIN, True),
            (GRP_ENCEVO, SE_KEVIN, True),
        ]

        for group_id, expert_id, is_primary in assignments:
            GroupServiceExpert.objects.update_or_create(
                group_id=group_id,
                service_expert_id=expert_id,
                defaults={"is_primary": is_primary},
            )
        self.stdout.write(f"    Assigned experts to {len(assignments)} groups")

    def _seed_tenant_onboarding_fields(self):
        """Update existing tenants with has_azure, gdap_link, and contact fields."""
        self.stdout.write("  Setting Azure & GDAP fields on tenants...")

        # Tenants with Azure subscriptions (need RBAC setup)
        azure_tenants = [
            TEN_ARCELOR_PROD,
            TEN_ARCELOR_BE,
            TEN_FERRERO_PROD,
            TEN_SES_PROD,
            TEN_POST_PROD,
            TEN_CLEARSTREAM,
            TEN_CS_BANKING_PROD,
            TEN_CS_SERVICES_PROD,
            TEN_GOODYEAR,
            TEN_ENCEVO_PROD,
            TEN_ENOVOS_PROD,
        ]
        updated = Tenant.objects.filter(id__in=azure_tenants).update(has_azure=True)
        self.stdout.write(f"    Set has_azure=True on {updated} tenants")

        # GDAP approval links (realistic Partner Center URLs)
        gdap_links = {
            TEN_ARCELOR_PROD: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/1a2b3c4d-0001-0001-0001-000000000001",
            TEN_ARCELOR_BE: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/1a2b3c4d-0001-0001-0001-000000000003",
            TEN_FERRERO_PROD: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/2a2b3c4d-0002-0002-0002-000000000001",
            TEN_SES_PROD: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/3a2b3c4d-0003-0003-0003-000000000001",
            TEN_CACTUS_PROD: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/4a2b3c4d-0004-0004-0004-000000000001",
            TEN_POST_PROD: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/5a2b3c4d-0005-0005-0005-000000000001",
            TEN_CLEARSTREAM: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/6a2b3c4d-0006-0006-0006-000000000001",
            TEN_CS_BANKING_PROD: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/6a2b3c4d-0006-0006-0006-000000000002",
            TEN_GOODYEAR: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/7a2b3c4d-0007-0007-0007-000000000001",
            TEN_ENCEVO_PROD: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/8a2b3c4d-0008-0008-0008-000000000001",
            TEN_ENOVOS_PROD: "https://admin.microsoft.com/AdminPortal/Home#/partners/invitation/granularAdminRelationships/8a2b3c4d-0008-0008-0008-000000000002",
        }

        for tenant_id, link in gdap_links.items():
            Tenant.objects.filter(id=tenant_id).update(gdap_link=link)
        self.stdout.write(f"    Set GDAP links on {len(gdap_links)} tenants")

        # Some tenants intentionally left WITHOUT gdap_link to test warnings:
        # - TEN_ARCELOR_DEV (dev tenant, no GDAP needed)
        # - TEN_SES_SAND (sandbox, no GDAP)
        # - TEN_FERRERO_DE (missing — shows amber warning in onboarding)
        # - TEN_POST_TELE (missing — shows amber warning)
        # - TEN_CS_FUND_PROD (missing — shows amber warning)
        # - TEN_CS_HOLDING_PROD (missing — shows amber warning)

        # Update authorized contact fields for richer onboarding data
        contact_updates = {
            CON_MARC: {
                "first_name": "Marc",
                "last_name": "Weber",
                "job_title": "Head of IT Infrastructure",
                "business_phone": "+352 4792 2100",
                "mobile_phone": "+352 621 100 001",
            },
            CON_LUC: {
                "first_name": "Luc",
                "last_name": "Thill",
                "job_title": "IT Director",
                "business_phone": "+352 4200 1100",
                "mobile_phone": "+352 621 200 001",
            },
            CON_SARAH: {
                "first_name": "Sarah",
                "last_name": "Hoffmann",
                "job_title": "Systems Administrator",
                "business_phone": "+352 710 725 100",
                "mobile_phone": "+352 621 300 001",
            },
            CON_NICOLAS: {
                "first_name": "Nicolas",
                "last_name": "Reuter",
                "job_title": "IT Manager",
                "business_phone": "+352 4295 2100",
                "mobile_phone": "+352 621 400 001",
            },
            CON_FRANK: {
                "first_name": "Frank",
                "last_name": "Lentz",
                "job_title": "Network & Security Manager",
                "business_phone": "+352 4760 1100",
                "mobile_phone": "+352 621 500 001",
            },
            CON_PAUL: {
                "first_name": "Paul",
                "last_name": "Kremer",
                "job_title": "CTO",
                "business_phone": "+352 243 32100",
                "mobile_phone": "+352 621 600 001",
            },
            CON_ROMAIN: {
                "first_name": "Romain",
                "last_name": "Lopes",
                "job_title": "IT Coordinator",
                "business_phone": "+352 8131 5100",
                "mobile_phone": "+352 621 700 001",
            },
            CON_CHRISTINE: {
                "first_name": "Christine",
                "last_name": "Majerus",
                "job_title": "IT Operations Manager",
                "business_phone": "+352 2737 6100",
                "mobile_phone": "+352 621 800 001",
            },
        }

        for contact_id, fields in contact_updates.items():
            AuthorizedContact.objects.filter(id=contact_id).update(**fields)
        self.stdout.write(f"    Updated {len(contact_updates)} contact profiles")

    def _seed_onboarding_sessions(self):
        """Create sample onboarding sessions in various states."""
        self.stdout.write("  Creating sample onboarding sessions...")

        # Get or create a staff user for created_by
        staff_user = User.objects.filter(is_staff=True).first()
        if not staff_user:
            staff_user = User.objects.create_user(
                username="admin",
                email="admin@schneider-itm.lu",
                password="admin",
                is_staff=True,
                is_superuser=True,
            )
            self.stdout.write("    Created admin staff user (admin/admin)")

        # Generate meeting slots (next Tue/Thu)
        now = datetime.now()
        slots = []
        for days_ahead in range(1, 15):
            candidate = now + timedelta(days=days_ahead)
            if candidate.weekday() in (1, 3):  # Tue, Thu
                for hour in (10, 10, 14, 14):
                    minute = 0 if slots and slots[-1].hour != hour else 30
                    slots.append(candidate.replace(hour=hour, minute=minute, second=0, microsecond=0))
                if len(slots) >= 8:
                    break

        slot_isos = [s.isoformat() for s in slots[:6]]

        # --- Session 1: ArcelorMittal — draft (in-progress) ---
        session1, created = OnboardingSession.objects.update_or_create(
            id=SESSION_ARCELOR,
            defaults={
                "group_id": GRP_ARCELOR,
                "status": OnboardingSession.Status.DRAFT,
                "language": "en",
                "recipient_id": CON_MARC,
                "contact_name": "Marc Weber",
                "contact_email": "m.weber@arcelormittal.com",
                "meeting_slots": slot_isos,
                "include_gdap": True,
                "include_rbac": True,
                "include_conditional_access": False,
                "additional_notes": "Multi-entity setup — ensure Belgian tenant is included.",
                "sender_id": SE_THOMAS,
                "sender_name": "Thomas Schneider",
                "sender_email": "t.schneider@schneider-itm.lu",
                "sender_phone": "+352 26 00 20 01",
                "sender_title": "Service Delivery Expert",
                "created_by": staff_user,
            },
        )
        if created:
            session1.tenants.set([TEN_ARCELOR_PROD, TEN_ARCELOR_DEV, TEN_ARCELOR_BE])
        CustomerGroup.objects.filter(id=GRP_ARCELOR).update(onboarding_status="in_progress")
        self.stdout.write(f"    ArcelorMittal session ({'created' if created else 'updated'})")

        # --- Session 2: Clearstream — email generated ---
        session2, created = OnboardingSession.objects.update_or_create(
            id=SESSION_CLEARSTREAM,
            defaults={
                "group_id": GRP_CLEARSTREAM,
                "status": OnboardingSession.Status.EMAIL_GENERATED,
                "language": "en",
                "recipient_id": CON_PAUL,
                "contact_name": "Paul Kremer",
                "contact_email": "p.kremer@clearstream.com",
                "meeting_slots": slot_isos[:4],
                "include_gdap": True,
                "include_rbac": True,
                "include_conditional_access": True,
                "additional_notes": "Platinum client — include all production tenants. Discuss CA policies in meeting.",
                "sender_id": SE_THOMAS,
                "sender_name": "Thomas Schneider",
                "sender_email": "t.schneider@schneider-itm.lu",
                "sender_phone": "+352 26 00 20 01",
                "sender_title": "Service Delivery Expert",
                "created_by": staff_user,
            },
        )
        if created:
            session2.tenants.set([
                TEN_CLEARSTREAM,
                TEN_CS_BANKING_PROD,
                TEN_CS_FUND_PROD,
                TEN_CS_SERVICES_PROD,
                TEN_CS_HOLDING_PROD,
            ])
        CustomerGroup.objects.filter(id=GRP_CLEARSTREAM).update(onboarding_status="email_sent")
        self.stdout.write(f"    Clearstream session ({'created' if created else 'updated'})")

        # --- Session 3: Cactus — draft (just started) ---
        session3, created = OnboardingSession.objects.update_or_create(
            id=SESSION_CACTUS,
            defaults={
                "group_id": GRP_CACTUS,
                "status": OnboardingSession.Status.DRAFT,
                "language": "fr",
                "recipient_id": CON_NICOLAS,
                "contact_name": "Nicolas Reuter",
                "contact_email": "n.reuter@cactus.lu",
                "meeting_slots": slot_isos[:2],
                "include_gdap": True,
                "include_rbac": False,
                "include_conditional_access": False,
                "additional_notes": "",
                "sender_id": SE_KEVIN,
                "sender_name": "Kevin Hansen",
                "sender_email": "k.hansen@schneider-itm.lu",
                "sender_phone": "+352 26 00 20 03",
                "sender_title": "Service Delivery Expert",
                "created_by": staff_user,
            },
        )
        if created:
            session3.tenants.set([TEN_CACTUS_PROD])
        CustomerGroup.objects.filter(id=GRP_CACTUS).update(onboarding_status="in_progress")
        self.stdout.write(f"    Cactus session ({'created' if created else 'updated'})")

        # Groups left at "none" status — ready for fresh onboarding:
        # Ferrero, SES, POST, Goodyear, Encevo
        self.stdout.write("    Groups with no session: Ferrero, SES, POST, Goodyear, Encevo")
