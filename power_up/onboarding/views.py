from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin

from power_up.crm.models import CustomerGroup, Tenant

from .forms import OnboardingConfigForm, TenantOnboardingForm
from .models import OnboardingEmail, OnboardingSession
from .utils.email_builder import build_onboarding_email
from .utils.meeting_slots import (
    format_slot,
    generate_meeting_slots,
    group_slots_by_day,
)


@staff_member_required
def dashboard(request):
    groups = (
        CustomerGroup.objects.filter(is_active=True)
        .prefetch_related("contracts__plan", "entities__tenants", "authorized_contacts")
        .order_by("name")
    )

    stats = {
        "total": groups.count(),
        "none": groups.filter(onboarding_status="none").count(),
        "in_progress": groups.filter(onboarding_status="in_progress").count(),
        "email_sent": groups.filter(onboarding_status="email_sent").count(),
        "completed": groups.filter(onboarding_status="completed").count(),
    }

    return render(
        request,
        "onboarding/dashboard.html",
        {"groups": groups, "stats": stats, "page_title": "Onboarding"},
    )


@staff_member_required
def start_session(request, group_id):
    if request.method != "POST":
        return redirect("onboarding:dashboard")

    group = get_object_or_404(CustomerGroup, pk=group_id)
    session = OnboardingSession.objects.create(
        group=group,
        created_by=request.user,
        contact_name="",
        contact_email="",
    )

    if group.onboarding_status == CustomerGroup.OnboardingStatus.NONE:
        group.onboarding_status = CustomerGroup.OnboardingStatus.IN_PROGRESS
        group.save(update_fields=["onboarding_status"])

    return redirect("onboarding:configure", session_id=session.pk)


@staff_member_required
def configure(request, session_id):
    session = get_object_or_404(
        OnboardingSession.objects.select_related("group"), pk=session_id
    )
    group = session.group

    if request.method == "POST":
        form = OnboardingConfigForm(request.POST, instance=session, group=group)
        if form.is_valid():
            form.save()
            return redirect("onboarding:preview", session_id=session.pk)
    else:
        form = OnboardingConfigForm(instance=session, group=group)

    # Group summary for sidebar
    active_contract = group.contracts.filter(status="active").first()

    tenants = Tenant.objects.filter(
        entity__group=group, is_active=True
    ).select_related("entity")

    contacts = group.authorized_contacts.filter(is_active=True)
    # Annotate contacts with their role label for this group
    role_map = dict(
        group.user_roles.values_list("contact_id", "role")
    )
    contacts_with_roles = []
    for c in contacts:
        c.role_label = dict(
            group.user_roles.model.Role.choices
        ).get(role_map.get(c.pk), "")
        contacts_with_roles.append(c)

    return render(
        request,
        "onboarding/configure.html",
        {
            "form": form,
            "session": session,
            "group": group,
            "active_contract": active_contract,
            "tenants": tenants,
            "contacts": contacts_with_roles,
            "page_title": f"Configure Onboarding — {group.name}",
        },
    )


@staff_member_required
def preview(request, session_id):
    session = get_object_or_404(
        OnboardingSession.objects.select_related("group"), pk=session_id
    )

    email_data = build_onboarding_email(session)

    return render(
        request,
        "onboarding/preview.html",
        {
            "session": session,
            "group": session.group,
            "email_data": email_data,
            "page_title": f"Preview — {session.group.name}",
        },
    )


@staff_member_required
def download_eml(request, session_id):
    session = get_object_or_404(
        OnboardingSession.objects.select_related("group"), pk=session_id
    )

    email_data = build_onboarding_email(session)

    # Save to OnboardingEmail record
    email_record, _created = OnboardingEmail.objects.get_or_create(
        session=session,
        defaults={
            "subject": email_data["subject"],
            "html_content": email_data["html"],
            "plain_content": email_data["text"],
            "recipient_email": session.contact_email,
        },
    )
    email_record.downloaded_at = timezone.now()
    email_record.save(update_fields=["downloaded_at"])

    # Update session + group status
    session.status = OnboardingSession.Status.EMAIL_GENERATED
    session.save(update_fields=["status"])

    group = session.group
    if group.onboarding_status in (
        CustomerGroup.OnboardingStatus.NONE,
        CustomerGroup.OnboardingStatus.IN_PROGRESS,
    ):
        group.onboarding_status = CustomerGroup.OnboardingStatus.EMAIL_SENT
        group.save(update_fields=["onboarding_status"])

    # Build MIME .eml
    msg = MIMEMultipart("alternative")
    msg["Subject"] = email_data["subject"]
    msg["From"] = session.sender_email or "noreply@schneider-it.lu"
    msg["To"] = session.contact_email
    msg["X-Unsent"] = "1"  # Tells Outlook to open as draft

    msg.attach(MIMEText(email_data["text"], "plain", "utf-8"))
    msg.attach(MIMEText(email_data["html"], "html", "utf-8"))

    eml_bytes = msg.as_bytes()
    filename = f"onboarding-{session.group.name.lower().replace(' ', '-')}.eml"

    response = HttpResponse(eml_bytes, content_type="message/rfc822")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@staff_member_required
def session_detail(request, session_id):
    session = get_object_or_404(
        OnboardingSession.objects.select_related(
            "group", "created_by", "sender", "recipient"
        ).prefetch_related("emails", "tenants"),
        pk=session_id,
    )

    return render(
        request,
        "onboarding/session_detail.html",
        {
            "session": session,
            "group": session.group,
            "emails": session.emails.all(),
            "page_title": f"Session — {session.group.name}",
        },
    )


@staff_member_required
@xframe_options_sameorigin
def partial_email_preview(request, session_id):
    session = get_object_or_404(OnboardingSession, pk=session_id)
    email_data = build_onboarding_email(session)
    return HttpResponse(email_data["html"], content_type="text/html")


@staff_member_required
def partial_slot_picker(request):
    slots = generate_meeting_slots()
    grouped = group_slots_by_day(slots)
    return render(
        request,
        "onboarding/partials/slot_picker.html",
        {"grouped_slots": grouped, "format_slot": format_slot},
    )


@staff_member_required
def tenant_edit(request, tenant_id, session_id=None):
    tenant = get_object_or_404(Tenant.objects.select_related("entity__group"), pk=tenant_id)
    group = tenant.entity.group

    if request.method == "POST":
        form = TenantOnboardingForm(request.POST, instance=tenant)
        if form.is_valid():
            form.save()
            if session_id:
                return redirect("onboarding:configure", session_id=session_id)
            return redirect("onboarding:dashboard")
    else:
        form = TenantOnboardingForm(instance=tenant)

    return render(
        request,
        "onboarding/tenant_edit.html",
        {
            "form": form,
            "tenant": tenant,
            "group": group,
            "session_id": session_id,
            "page_title": f"Edit Tenant — {tenant.company_name}",
        },
    )
