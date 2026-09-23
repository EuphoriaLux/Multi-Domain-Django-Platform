"""Public Power-Up agency pages. All views are static template renders."""

from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from .platforms import PLATFORMS
from .solutions import SOLUTIONS


@require_GET
def home(request):
    """Landing page for prospective software clients."""
    context = {
        "page_title": _("Custom Software Development in Luxembourg | Power-Up"),
        "meta_description": _(
            "Power-Up builds custom business software, portals and workflow tools "
            "for Luxembourg organizations, with a focus on Microsoft Azure."
        ),
    }
    return render(request, "power_up/home.html", context)


@require_GET
def services(request):
    """Custom software and Azure services."""
    return render(request, "power_up/services.html", {
        "page_title": _("Custom Software and Azure Services | Power-Up"),
        "meta_description": _(
            "Custom applications, business portals, Azure architecture and "
            "workflow integrations for Luxembourg organizations."
        ),
    })


@require_GET
def work(request):
    """Selected work with explicit ownership and maturity labels."""
    return render(request, "power_up/work.html", {
        "page_title": _("Selected Software Work | Power-Up"),
        "meta_description": _(
            "Explore Power-Up's client delivery, owned products and internal "
            "Azure tools, with clear project status."
        ),
        "platforms": PLATFORMS,
    })


@require_GET
def approach(request):
    """How software engagements are scoped and delivered."""
    return render(request, "power_up/approach.html", {
        "page_title": _("Our Software Delivery Approach | Power-Up"),
        "meta_description": _(
            "See how Power-Up discovers, builds, launches and supports "
            "custom business software."
        ),
    })


@require_GET
def about(request):
    """Company focus and experience."""
    context = {
        "page_title": _("About Power-Up"),
        "meta_description": _(
            "Meet Power-Up, a Luxembourg software studio focused on custom "
            "business applications and Microsoft Azure."
        ),
    }
    return render(request, "power_up/about.html", context)


@require_GET
def solutions(request):
    """Event products sold to venues, agencies and companies."""
    context = {
        "solutions": SOLUTIONS,
        "pilot_subject": _("Pilot partner 2027"),
        "page_title": _("Event Solutions for Businesses - Power-Up"),
        "meta_description": _(
            "Ticketing, quiz nights and speed dating for venues, event "
            "agencies and companies in Luxembourg, run on technology "
            "proven live on Crush.lu."
        ),
    }
    return render(request, "power_up/solutions.html", context)


@require_GET
def platforms(request):
    """Legacy portfolio route, kept for existing links."""
    context = {
        "platforms": PLATFORMS,
        "page_title": _("Our Platforms - Power-Up"),
        "meta_description": _(
            "Explore Power-Up's client work, owned products, internal tools "
            "and concepts with their current status."
        ),
    }
    return render(request, "power_up/platforms.html", context)


@require_GET
def investors(request):
    """Partnership information at the existing URL."""
    context = {
        "page_title": _("Partnerships | Power-Up"),
        "meta_description": _(
            "Discuss software delivery and strategic partnerships with Power-Up."
        ),
    }
    return render(request, "power_up/investors.html", context)


@require_GET
def contact(request):
    """Project enquiry contact information."""
    context = {
        "page_title": _("Discuss Your Software Project | Power-Up"),
        "meta_description": _(
            "Tell Power-Up about your business software project in Luxembourg."
        ),
    }
    return render(request, "power_up/contact.html", context)
