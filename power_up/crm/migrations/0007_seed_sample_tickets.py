import uuid
from datetime import timedelta

from django.db import migrations
from django.utils import timezone

# Reuse IDs from 0004
GRP_ARCELOR = uuid.UUID("a0000001-0000-0000-0000-000000000001")
GRP_FERRERO = uuid.UUID("a0000001-0000-0000-0000-000000000002")
GRP_SES = uuid.UUID("a0000001-0000-0000-0000-000000000003")
GRP_CACTUS = uuid.UUID("a0000001-0000-0000-0000-000000000004")
GRP_POST = uuid.UUID("a0000001-0000-0000-0000-000000000005")
GRP_CLEARSTREAM = uuid.UUID("a0000001-0000-0000-0000-000000000006")
GRP_GOODYEAR = uuid.UUID("a0000001-0000-0000-0000-000000000007")
GRP_ENCEVO = uuid.UUID("a0000001-0000-0000-0000-000000000008")

TEN_ARCELOR_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000001")
TEN_ARCELOR_DEV = uuid.UUID("c0000001-0000-0000-0000-000000000002")
TEN_ARCELOR_BE = uuid.UUID("c0000001-0000-0000-0000-000000000003")
TEN_FERRERO_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000004")
TEN_FERRERO_DE = uuid.UUID("c0000001-0000-0000-0000-000000000005")
TEN_SES_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000006")
TEN_CACTUS_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000008")
TEN_POST_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000009")
TEN_POST_TELE = uuid.UUID("c0000001-0000-0000-0000-000000000010")
TEN_CLEARSTREAM = uuid.UUID("c0000001-0000-0000-0000-000000000011")
TEN_GOODYEAR = uuid.UUID("c0000001-0000-0000-0000-000000000012")
TEN_ENCEVO_PROD = uuid.UUID("c0000001-0000-0000-0000-000000000013")

CON_MARC = uuid.UUID("d0000001-0000-0000-0000-000000000001")
CON_SOPHIE = uuid.UUID("d0000001-0000-0000-0000-000000000002")
CON_THOMAS = uuid.UUID("d0000001-0000-0000-0000-000000000003")
CON_LUC = uuid.UUID("d0000001-0000-0000-0000-000000000005")
CON_JULIE = uuid.UUID("d0000001-0000-0000-0000-000000000006")
CON_PIERRE = uuid.UUID("d0000001-0000-0000-0000-000000000007")
CON_SARAH = uuid.UUID("d0000001-0000-0000-0000-000000000008")
CON_GEORGES = uuid.UUID("d0000001-0000-0000-0000-000000000009")
CON_NICOLAS = uuid.UUID("d0000001-0000-0000-0000-000000000011")
CON_CLAIRE = uuid.UUID("d0000001-0000-0000-0000-000000000012")
CON_FRANK = uuid.UUID("d0000001-0000-0000-0000-000000000013")
CON_MARTINE = uuid.UUID("d0000001-0000-0000-0000-000000000014")
CON_PAUL = uuid.UUID("d0000001-0000-0000-0000-000000000015")
CON_ELENA = uuid.UUID("d0000001-0000-0000-0000-000000000016")
CON_ROMAIN = uuid.UUID("d0000001-0000-0000-0000-000000000017")
CON_CHRISTINE = uuid.UUID("d0000001-0000-0000-0000-000000000018")

AM_SCHNEIDER_A = uuid.UUID("e0000001-0000-0000-0000-000000000001")
AM_SCHNEIDER_B = uuid.UUID("e0000001-0000-0000-0000-000000000002")
AM_SCHNEIDER_C = uuid.UUID("e0000001-0000-0000-0000-000000000003")

# Deterministic ticket UUIDs
TKT = [uuid.UUID(f"10000001-0000-0000-0000-{i:012d}") for i in range(1, 20)]
CMT = [uuid.UUID(f"20000001-0000-0000-0000-{i:012d}") for i in range(1, 12)]


def seed_tickets(apps, schema_editor):
    Ticket = apps.get_model("crm", "Ticket")
    TicketComment = apps.get_model("crm", "TicketComment")

    now = timezone.now()

    # fmt: off
    tickets_data = [
        # ArcelorMittal (4 tickets — Platinum, Sev A/B/C)
        (TKT[0], "TKT-2026-00001", TEN_ARCELOR_PROD, CON_MARC, AM_SCHNEIDER_A,
         "Azure AD Conditional Access policy blocking global admins",
         "All global admins are being blocked by a conditional access policy that was auto-applied. Urgent — production admin access is down.",
         "A", "in_progress", "critical", "azure", "MS-2026-001234", False,
         "Escalated to Microsoft Premier. TAM assigned.", now - timedelta(hours=6), now - timedelta(hours=5), None, None),

        (TKT[1], "TKT-2026-00002", TEN_ARCELOR_DEV, CON_SOPHIE, AM_SCHNEIDER_A,
         "Dev tenant license assignment failures for E5",
         "Bulk license assignment for 50 dev accounts failing with 'insufficient licenses' despite having quota.",
         "C", "waiting_vendor", "medium", "m365", "", False,
         "", now - timedelta(days=3), now - timedelta(days=2, hours=20), None, None),

        (TKT[2], "TKT-2026-00003", TEN_ARCELOR_BE, CON_THOMAS, AM_SCHNEIDER_A,
         "Belgium tenant SharePoint migration stuck at 85%",
         "SharePoint Online migration from on-prem has been stuck at 85% for 48 hours. Migration tool shows no errors.",
         "B", "in_progress", "high", "m365", "MS-2026-001567", False,
         "Microsoft investigating backend. ETA: 24h.", now - timedelta(days=5), now - timedelta(days=4, hours=18), None, None),

        (TKT[3], "TKT-2026-00004", TEN_ARCELOR_PROD, CON_MARC, AM_SCHNEIDER_A,
         "Azure cost anomaly — unexpected Cosmos DB spend",
         "Monthly Cosmos DB cost jumped from EUR 800 to EUR 3,200. Need investigation of RU consumption.",
         "C", "resolved", "medium", "azure", "", False,
         "Root cause: dev team left test workload running. Resolved.", now - timedelta(days=14), now - timedelta(days=13), now - timedelta(days=10), None),

        # Ferrero (3 tickets — Gold, Sev B/C)
        (TKT[4], "TKT-2026-00005", TEN_FERRERO_PROD, CON_LUC, AM_SCHNEIDER_B,
         "Intune MDM enrollment failing for iOS 18 devices",
         "New batch of 200 iPhones cannot complete Intune enrollment. Error: 'Device management profile installation failed.'",
         "B", "in_progress", "high", "m365", "", False,
         "", now - timedelta(days=2), now - timedelta(days=1, hours=20), None, None),

        (TKT[5], "TKT-2026-00006", TEN_FERRERO_DE, CON_JULIE, AM_SCHNEIDER_B,
         "D365 Finance German tax module rounding error",
         "VAT calculations for German invoices are rounding incorrectly by 1 cent on orders over EUR 10,000.",
         "C", "waiting_customer", "medium", "d365", "", False,
         "Requested sample invoice data from Julie.", now - timedelta(days=7), now - timedelta(days=6), None, None),

        (TKT[6], "TKT-2026-00007", TEN_FERRERO_PROD, CON_PIERRE, AM_SCHNEIDER_B,
         "Exchange Online mailbox size alert for shared mailboxes",
         "Three shared mailboxes approaching 50GB limit. Need archiving solution guidance.",
         "C", "closed", "low", "m365", "", False,
         "", now - timedelta(days=30), now - timedelta(days=29), now - timedelta(days=25), now - timedelta(days=20)),

        # SES (2 tickets — Gold)
        (TKT[7], "TKT-2026-00008", TEN_SES_PROD, CON_SARAH, AM_SCHNEIDER_B,
         "Azure ExpressRoute latency spike to satellite ground stations",
         "Latency on ExpressRoute circuit to Frankfurt POP increased from 4ms to 45ms during peak hours.",
         "B", "waiting_vendor", "high", "azure", "MS-2026-002345", False,
         "Microsoft networking team investigating. Circuit ID shared.", now - timedelta(days=1), now - timedelta(hours=20), None, None),

        (TKT[8], "TKT-2026-00009", TEN_SES_PROD, CON_GEORGES, AM_SCHNEIDER_B,
         "Azure Backup job failures on SQL Managed Instance",
         "Nightly backup jobs for 3 SQL MI databases failing since last week. Error: timeout.",
         "C", "resolved", "medium", "azure", "", False,
         "", now - timedelta(days=10), now - timedelta(days=9, hours=12), now - timedelta(days=7), None),

        # Cactus (2 tickets — Bronze, billable)
        (TKT[9], "TKT-2026-00010", TEN_CACTUS_PROD, CON_NICOLAS, AM_SCHNEIDER_C,
         "M365 Teams phone system call drops",
         "Teams calling users reporting call drops after 10 minutes on external calls. Affects 3 stores.",
         "B", "in_progress", "high", "m365", "", True,
         "Billable — Bronze PAYG. 2 hours logged.", now - timedelta(days=4), now - timedelta(days=3, hours=18), None, None),

        (TKT[10], "TKT-2026-00011", TEN_CACTUS_PROD, CON_CLAIRE, AM_SCHNEIDER_C,
         "OneDrive sync issues on POS terminals",
         "Point-of-sale terminals running OneDrive sync are consuming excessive bandwidth.",
         "C", "new", "low", "m365", "", True,
         "Billable — Bronze PAYG.", now - timedelta(hours=8), None, None, None),

        # POST (2 tickets — Silver)
        (TKT[11], "TKT-2026-00012", TEN_POST_PROD, CON_FRANK, AM_SCHNEIDER_C,
         "Azure DevOps pipeline failures after agent update",
         "Self-hosted build agents updated to latest version. Now 40% of pipelines fail with permission errors.",
         "B", "in_progress", "high", "azure", "", False,
         "", now - timedelta(days=2), now - timedelta(days=1, hours=16), None, None),

        (TKT[12], "TKT-2026-00013", TEN_POST_TELE, CON_MARTINE, AM_SCHNEIDER_C,
         "Power BI Pro license allocation for Telecom analytics team",
         "Need to provision 15 additional Power BI Pro licenses for new analytics team members.",
         "C", "closed", "low", "m365", "", False,
         "", now - timedelta(days=20), now - timedelta(days=19), now - timedelta(days=18), now - timedelta(days=15)),

        # Clearstream (3 tickets — Platinum, Sev A/B)
        (TKT[13], "TKT-2026-00014", TEN_CLEARSTREAM, CON_PAUL, AM_SCHNEIDER_A,
         "Azure Key Vault access denied for production AKS cluster",
         "Production AKS pods cannot retrieve secrets from Key Vault. Service mesh certificate rotation failed.",
         "A", "resolved", "critical", "azure", "MS-2026-003456", False,
         "Root cause: managed identity binding expired. Renewed.", now - timedelta(days=8), now - timedelta(days=8, hours=-1), now - timedelta(days=7, hours=20), None),

        (TKT[14], "TKT-2026-00015", TEN_CLEARSTREAM, CON_ELENA, AM_SCHNEIDER_A,
         "Defender for Endpoint alert — suspicious PowerShell on DC",
         "Microsoft Defender flagged encoded PowerShell execution on domain controller CSDC01.",
         "A", "in_progress", "critical", "m365", "", False,
         "Security team investigating. No lateral movement detected yet.", now - timedelta(hours=3), now - timedelta(hours=2, minutes=30), None, None),

        (TKT[15], "TKT-2026-00016", TEN_CLEARSTREAM, CON_PAUL, AM_SCHNEIDER_A,
         "Azure SQL Managed Instance failover group lag",
         "Geo-replication lag between Luxembourg and Frankfurt MI exceeds 30 seconds during batch processing.",
         "B", "waiting_vendor", "high", "azure", "MS-2026-004567", False,
         "Microsoft data team reviewing replication metrics.", now - timedelta(days=6), now - timedelta(days=5, hours=12), None, None),

        # Goodyear (1 ticket — Silver)
        (TKT[16], "TKT-2026-00017", TEN_GOODYEAR, CON_ROMAIN, AM_SCHNEIDER_C,
         "Azure VM performance degradation in D-series",
         "D16s_v5 VMs running simulation workloads showing 30% CPU regression after platform maintenance.",
         "C", "new", "medium", "azure", "", False,
         "", now - timedelta(hours=12), None, None, None),

        # Encevo (1 ticket — Silver)
        (TKT[17], "TKT-2026-00018", TEN_ENCEVO_PROD, CON_CHRISTINE, AM_SCHNEIDER_C,
         "Power Apps portal authentication loop for external users",
         "External contractor users stuck in authentication redirect loop on Power Apps portal.",
         "C", "waiting_customer", "medium", "d365", "", False,
         "Asked Christine to verify B2C tenant config.", now - timedelta(days=3), now - timedelta(days=2, hours=12), None, None),
    ]
    # fmt: on

    for (
        tid,
        ref,
        tenant_id,
        requester_id,
        assigned_id,
        title,
        desc,
        severity,
        status,
        priority,
        product,
        ms_case,
        billable,
        notes,
        created,
        first_resp,
        resolved,
        closed,
    ) in tickets_data:
        Ticket.objects.update_or_create(
            id=tid,
            defaults={
                "reference_number": ref,
                "tenant_id": tenant_id,
                "requester_id": requester_id,
                "assigned_to_id": assigned_id,
                "title": title,
                "description": desc,
                "severity": severity,
                "status": status,
                "priority": priority,
                "product_category": product,
                "microsoft_case_id": ms_case,
                "is_billable": billable,
                "internal_notes": notes,
                "first_response_at": first_resp,
                "resolved_at": resolved,
                "closed_at": closed,
            },
        )
        # Backdate created_at (auto_now_add prevents setting in create)
        Ticket.objects.filter(id=tid).update(created_at=created)

    # Sample comments
    comments_data = [
        # TKT-2026-00001 (ArcelorMittal CA policy)
        (
            CMT[0],
            TKT[0],
            "Alexander Schneider",
            "a.schneider@schneider-itm.lu",
            "Escalated to Microsoft Premier Support. TAM is reviewing the conditional access policy set. ETA for callback: 2 hours.",
            True,
        ),
        (
            CMT[1],
            TKT[0],
            "Marc Weber",
            "m.weber@arcelormittal.com",
            "We need this resolved ASAP. Board meeting at 14:00 requires admin portal access.",
            False,
        ),
        (
            CMT[2],
            TKT[0],
            "Alexander Schneider",
            "a.schneider@schneider-itm.lu",
            "Microsoft identified the policy. It was a preview feature that auto-enabled. Working on rollback.",
            False,
        ),
        # TKT-2026-00003 (Belgium SharePoint migration)
        (
            CMT[3],
            TKT[2],
            "Maria Koch",
            "m.koch@schneider-itm.lu",
            "Internal note: This is the 3rd migration stall for this tenant. Consider switching to ShareGate tool.",
            True,
        ),
        # TKT-2026-00005 (Ferrero Intune)
        (
            CMT[4],
            TKT[4],
            "Maria Koch",
            "m.koch@schneider-itm.lu",
            "Confirmed: iOS 18.3 introduced new MDM profile requirements. Apple published KB article HT214123.",
            False,
        ),
        (
            CMT[5],
            TKT[4],
            "Luc Thill",
            "l.thill@ferrero.com",
            "Can we get a workaround? The 200 devices are needed for the Luxembourg stores rollout next week.",
            False,
        ),
        # TKT-2026-00010 (Cactus Teams phone)
        (
            CMT[6],
            TKT[9],
            "Jan Wagner",
            "j.wagner@schneider-itm.lu",
            "Ran network trace on affected store. SBC firmware needs update. Scheduling maintenance window.",
            False,
        ),
        # TKT-2026-00014 (Clearstream Key Vault)
        (
            CMT[7],
            TKT[13],
            "Alexander Schneider",
            "a.schneider@schneider-itm.lu",
            "Managed identity binding for AKS renewed. All pods recovering. Monitoring for 24h.",
            False,
        ),
        (
            CMT[8],
            TKT[13],
            "Paul Kremer",
            "p.kremer@clearstream.com",
            "Confirmed — all services back to normal. Please add monitoring alert for identity expiration.",
            False,
        ),
        # TKT-2026-00015 (Clearstream Defender alert)
        (
            CMT[9],
            TKT[14],
            "Alexander Schneider",
            "a.schneider@schneider-itm.lu",
            "Internal: Isolated CSDC01 from network. Forensic image captured. No signs of data exfiltration.",
            True,
        ),
    ]

    for cmt_id, ticket_id, name, email, message, internal in comments_data:
        TicketComment.objects.update_or_create(
            id=cmt_id,
            defaults={
                "ticket_id": ticket_id,
                "author_name": name,
                "author_email": email,
                "message": message,
                "is_internal": internal,
            },
        )


def remove_tickets(apps, schema_editor):
    Ticket = apps.get_model("crm", "Ticket")
    TicketComment = apps.get_model("crm", "TicketComment")
    ticket_ids = [TKT[i] for i in range(18)]
    TicketComment.objects.filter(ticket_id__in=ticket_ids).delete()
    Ticket.objects.filter(id__in=ticket_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0006_ticket_models"),
    ]

    operations = [
        migrations.RunPython(seed_tickets, remove_tickets),
    ]
