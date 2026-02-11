"""
Onboarding email builder — orchestrates HTML + plain text email generation.

Pulls CRM data from the session's group and renders Django templates
into complete email content.
"""

from datetime import datetime, timedelta

from django.template.loader import render_to_string

from .meeting_slots import group_slots_by_day
from .rbac_scripts import generate_rbac_script


def _enrich_grouped_slots(grouped_slots):
    """Add formatted time strings to each slot for template rendering."""
    for day_data in grouped_slots.values():
        for period in ("morning", "afternoon"):
            enriched = []
            for slot in day_data[period]:
                end = slot + timedelta(minutes=30)
                enriched.append(
                    {
                        "dt": slot,
                        "start": slot.strftime("%H:%M"),
                        "end": end.strftime("%H:%M"),
                    }
                )
            day_data[period] = enriched
    return grouped_slots


def build_onboarding_email(session):
    """
    Build a complete onboarding email from an OnboardingSession.

    Returns a dict::

        {"html": str, "text": str, "subject": str}
    """
    group = session.group

    # Active contract + plan
    active_contract = group.contracts.filter(status="active").first()
    plan = active_contract.plan if active_contract else None

    # Tenants — selected on the session (not all group tenants)
    tenants = list(session.tenants.select_related("entity").all())

    # Authorized contacts
    contacts = list(group.authorized_contacts.filter(is_active=True))

    # Meeting slots (stored as ISO strings in JSONField)
    selected_slots = []
    for s in session.meeting_slots or []:
        if isinstance(s, str):
            selected_slots.append(datetime.fromisoformat(s))
        else:
            selected_slots.append(s)
    grouped_slots = _enrich_grouped_slots(group_slots_by_day(selected_slots))

    # RBAC script
    rbac_script = ""
    if session.include_rbac:
        rbac_script = generate_rbac_script(tenants)

    # Subject line
    subject = f"Onboarding \u2013 {group.name}"
    if plan:
        subject = f"Onboarding \u2013 {group.name} ({plan.get_plan_type_display()})"

    max_contacts = plan.max_authorized_contacts if plan else 2
    empty_contact_rows = max(0, max_contacts - len(contacts))

    context = {
        "session": session,
        "group": group,
        "contract": active_contract,
        "plan": plan,
        "tenants": tenants,
        "contacts": contacts,
        "selected_slots": selected_slots,
        "grouped_slots": grouped_slots,
        "rbac_script": rbac_script,
        "max_contacts": max_contacts,
        "max_tenants": plan.max_tenants if plan else 1,
        "empty_contact_rows": range(empty_contact_rows),
    }

    html = render_to_string("onboarding/email/onboarding_email.html", context)
    text = render_to_string("onboarding/email/onboarding_email.txt", context)

    return {"html": html, "text": text, "subject": subject}
