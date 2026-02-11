from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import TicketCommentForm, TicketCreateForm, TicketUpdateForm
from .models import (
    AccountManager,
    AuthorizedContact,
    ContactTenantPermission,
    Contract,
    CustomerGroup,
    Entity,
    GroupAccountManager,
    Plan,
    Tenant,
    Ticket,
    TicketComment,
    TicketUsagePeriod,
)


@staff_member_required
def groups_overview(request):
    """Customer Groups dashboard — high-level overview of all groups."""
    today = timezone.now().date()

    groups = (
        CustomerGroup.objects.prefetch_related(
            "entities__tenants",
            "authorized_contacts",
            "contracts__plan",
            "account_manager_assignments__account_manager",
        )
        .annotate(
            num_entities=Count("entities", distinct=True),
            num_tenants=Count("entities__tenants", distinct=True),
            num_contacts=Count("authorized_contacts", distinct=True),
        )
        .order_by("name")
    )

    # Build per-group summary data
    group_cards = []
    for group in groups:
        active_contract = (
            group.contracts.filter(
                status="active",
                start_date__lte=today,
                end_date__gte=today,
            )
            .select_related("plan")
            .first()
        )

        primary_manager = (
            group.account_manager_assignments.filter(is_primary=True)
            .select_related("account_manager")
            .first()
        )

        days_remaining = None
        if active_contract:
            days_remaining = (active_contract.end_date - today).days

        group_cards.append(
            {
                "group": group,
                "active_contract": active_contract,
                "plan": active_contract.plan if active_contract else None,
                "primary_manager": (
                    primary_manager.account_manager if primary_manager else None
                ),
                "days_remaining": days_remaining,
            }
        )

    # Aggregate stats
    total_groups = len(group_cards)
    total_active = sum(1 for g in group_cards if g["active_contract"])
    total_contacts = sum(g["group"].num_contacts for g in group_cards)
    total_tenants = sum(g["group"].num_tenants for g in group_cards)

    # Plan distribution
    plan_dist = {}
    for g in group_cards:
        if g["plan"]:
            key = g["plan"].get_plan_type_display()
            plan_dist[key] = plan_dist.get(key, 0) + 1

    # Contracts expiring within 90 days
    expiring_soon = [
        g
        for g in group_cards
        if g["days_remaining"] is not None and g["days_remaining"] <= 90
    ]

    return render(
        request,
        "crm/groups_overview.html",
        {
            "page_title": "Customer Groups Overview",
            "group_cards": group_cards,
            "total_groups": total_groups,
            "total_active": total_active,
            "total_contacts": total_contacts,
            "total_tenants": total_tenants,
            "plan_distribution": plan_dist,
            "expiring_soon": expiring_soon,
        },
    )


@staff_member_required
def group_detail(request, pk):
    """Detailed view of a single Customer Group."""
    today = timezone.now().date()

    group = get_object_or_404(
        CustomerGroup.objects.prefetch_related(
            "entities__tenants",
            "authorized_contacts__roles",
            "authorized_contacts__tenant_permissions__tenant",
            "contracts__plan",
            "account_manager_assignments__account_manager",
            "service_expert_assignments__service_expert",
        ),
        pk=pk,
    )

    entities = group.entities.annotate(
        num_tenants=Count("tenants"),
    ).order_by("name")

    contacts = group.authorized_contacts.prefetch_related(
        "roles", "tenant_permissions__tenant"
    ).order_by("display_name")

    contracts = group.contracts.select_related("plan").order_by("-start_date")

    active_contract = contracts.filter(
        status="active",
        start_date__lte=today,
        end_date__gte=today,
    ).first()

    managers = group.account_manager_assignments.select_related(
        "account_manager"
    ).order_by("-is_primary")

    service_experts = group.service_expert_assignments.select_related(
        "service_expert"
    ).order_by("-is_primary")

    days_remaining = None
    if active_contract:
        days_remaining = (active_contract.end_date - today).days

    total_tenants = sum(e.num_tenants for e in entities)

    # Recent tickets for this group
    recent_tickets = Ticket.objects.filter(
        tenant__entity__group=group,
    ).select_related(
        "tenant", "assigned_to"
    )[:10]

    # Ticket quota usage
    ticket_quota_used = TicketUsagePeriod.get_current_usage(group)
    ticket_quota_max = (
        active_contract.plan.support_requests_per_year if active_contract else 0
    )

    return render(
        request,
        "crm/group_detail.html",
        {
            "page_title": f"{group.name}",
            "group": group,
            "entities": entities,
            "contacts": contacts,
            "contracts": contracts,
            "active_contract": active_contract,
            "days_remaining": days_remaining,
            "managers": managers,
            "service_experts": service_experts,
            "total_tenants": total_tenants,
            "recent_tickets": recent_tickets,
            "ticket_quota_used": ticket_quota_used,
            "ticket_quota_max": ticket_quota_max,
        },
    )


# ---------------------------------------------------------------------------
# Ticket Views
# ---------------------------------------------------------------------------


@staff_member_required
def ticket_list(request):
    """Filterable ticket list with KPI summary row."""
    tickets = Ticket.objects.select_related(
        "tenant__entity__group",
        "requester",
        "assigned_to",
        "assigned_expert",
    )

    # Filters
    status_filter = request.GET.get("status", "")
    severity_filter = request.GET.get("severity", "")
    priority_filter = request.GET.get("priority", "")
    group_filter = request.GET.get("group", "")
    search = request.GET.get("q", "").strip()

    if status_filter:
        tickets = tickets.filter(status=status_filter)
    if severity_filter:
        tickets = tickets.filter(severity=severity_filter)
    if priority_filter:
        tickets = tickets.filter(priority=priority_filter)
    if group_filter:
        tickets = tickets.filter(tenant__entity__group_id=group_filter)
    if search:
        tickets = tickets.filter(
            Q(reference_number__icontains=search)
            | Q(title__icontains=search)
            | Q(tenant__company_name__icontains=search)
            | Q(requester__display_name__icontains=search)
        )

    tickets = tickets.order_by("-created_at")

    # KPIs (computed on unfiltered set)
    all_tickets = Ticket.objects.all()
    open_statuses = ["new", "in_progress", "waiting_customer", "waiting_vendor"]
    kpi_open = all_tickets.filter(status__in=open_statuses).count()
    kpi_new = all_tickets.filter(status="new").count()
    kpi_total = all_tickets.count()
    kpi_resolved = all_tickets.filter(
        status__in=["resolved", "closed"],
    ).count()

    groups = CustomerGroup.objects.order_by("name")

    return render(
        request,
        "crm/ticket_list.html",
        {
            "page_title": "Support Tickets",
            "tickets": tickets,
            "kpi_open": kpi_open,
            "kpi_new": kpi_new,
            "kpi_total": kpi_total,
            "kpi_resolved": kpi_resolved,
            "groups": groups,
            "status_filter": status_filter,
            "severity_filter": severity_filter,
            "priority_filter": priority_filter,
            "group_filter": group_filter,
            "search": search,
            "status_choices": Ticket.Status.choices,
            "severity_choices": Ticket.Severity.choices,
            "priority_choices": Ticket.Priority.choices,
        },
    )


@staff_member_required
def ticket_detail(request, pk):
    """Full ticket detail with comments and sidebar metadata."""
    ticket = get_object_or_404(
        Ticket.objects.select_related(
            "tenant__entity__group",
            "requester__group",
            "assigned_to",
            "assigned_expert",
        ),
        pk=pk,
    )
    comments = ticket.comments.order_by("created_at")
    comment_form = TicketCommentForm()
    update_form = TicketUpdateForm(instance=ticket)

    group = ticket.tenant.entity.group
    ticket_quota_used = TicketUsagePeriod.get_current_usage(group)

    # Get active contract for quota info
    today = timezone.now().date()
    active_contract = (
        Contract.objects.filter(
            group=group,
            status="active",
            start_date__lte=today,
            end_date__gte=today,
        )
        .select_related("plan")
        .first()
    )
    ticket_quota_max = (
        active_contract.plan.support_requests_per_year if active_contract else 0
    )

    return render(
        request,
        "crm/ticket_detail.html",
        {
            "page_title": ticket.reference_number,
            "ticket": ticket,
            "comments": comments,
            "comment_form": comment_form,
            "update_form": update_form,
            "group": group,
            "active_contract": active_contract,
            "ticket_quota_used": ticket_quota_used,
            "ticket_quota_max": ticket_quota_max,
        },
    )


@staff_member_required
def ticket_create(request):
    """Create a new support ticket."""
    group_id = request.GET.get("group") or request.POST.get("group")
    group = None
    if group_id:
        group = get_object_or_404(CustomerGroup, pk=group_id)

    if request.method == "POST":
        form = TicketCreateForm(request.POST, group=group)
        if form.is_valid():
            ticket = form.save()
            messages.success(
                request, f"Ticket {ticket.reference_number} created successfully."
            )
            return redirect("crm:ticket_detail", pk=ticket.pk)
    else:
        form = TicketCreateForm(group=group)

    # Get active contract for group context banner
    active_contract = None
    if group:
        today = timezone.now().date()
        active_contract = (
            Contract.objects.filter(
                group=group,
                status="active",
                start_date__lte=today,
                end_date__gte=today,
            )
            .select_related("plan")
            .first()
        )

    return render(
        request,
        "crm/ticket_create.html",
        {
            "page_title": "New Ticket",
            "form": form,
            "group": group,
            "active_contract": active_contract,
        },
    )


@staff_member_required
def ticket_requester_options(request):
    """HTMX partial: return <option> elements for requester filtered by tenant."""
    tenant_id = request.GET.get("tenant")
    group_id = request.GET.get("group")

    options = '<option value="">---------</option>'

    if tenant_id:
        # Contacts with permissions on this specific tenant
        contact_ids = ContactTenantPermission.objects.filter(
            tenant_id=tenant_id,
        ).values_list("contact_id", flat=True)
        contacts = AuthorizedContact.objects.filter(
            id__in=contact_ids, is_active=True
        ).order_by("display_name")
    elif group_id:
        # Fall back to all contacts in the group
        contacts = AuthorizedContact.objects.filter(
            group_id=group_id, is_active=True
        ).order_by("display_name")
    else:
        # No filter — all active contacts
        contacts = AuthorizedContact.objects.filter(is_active=True).order_by(
            "display_name"
        )

    for contact in contacts:
        options += f'<option value="{contact.pk}">{contact}</option>'

    return HttpResponse(options)


@staff_member_required
def ticket_update(request, pk):
    """Update ticket status, assignment, and metadata."""
    ticket = get_object_or_404(Ticket, pk=pk)

    if request.method == "POST":
        old_status = ticket.status
        form = TicketUpdateForm(request.POST, instance=ticket)
        if form.is_valid():
            ticket = form.save(commit=False)
            now = timezone.now()

            # Track SLA timestamps
            if old_status == "new" and ticket.status != "new":
                if not ticket.first_response_at:
                    ticket.first_response_at = now

            if ticket.status == "resolved" and not ticket.resolved_at:
                ticket.resolved_at = now

            if ticket.status == "closed" and not ticket.closed_at:
                ticket.closed_at = now

            ticket.save()
            messages.success(request, f"Ticket {ticket.reference_number} updated.")
            return redirect("crm:ticket_detail", pk=ticket.pk)

    return redirect("crm:ticket_detail", pk=ticket.pk)


@staff_member_required
def ticket_comment_add(request, pk):
    """Add a comment to a ticket."""
    ticket = get_object_or_404(Ticket, pk=pk)

    if request.method == "POST":
        form = TicketCommentForm(request.POST)
        if form.is_valid():
            TicketComment.objects.create(
                ticket=ticket,
                author_name=request.user.get_full_name() or request.user.username,
                author_email=request.user.email,
                message=form.cleaned_data["message"],
                is_internal=form.cleaned_data["is_internal"],
            )
            messages.success(request, "Comment added.")

    return redirect("crm:ticket_detail", pk=ticket.pk)
