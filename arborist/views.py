"""
Views for Arborist informational site.

All views are simple template renders - no forms, no authentication.
This is a static informational site.
"""

from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET


@require_GET
def home(request):
    """Landing page with hero and overview."""
    context = {
        "page_title": _("Arborist - Professional Tree Care Services"),
        "meta_description": _(
            "Professional arborist services in Luxembourg. "
            "Tree care, pruning, removal, and consultation."
        ),
    }
    return render(request, "arborist/home.html", context)


@require_GET
def about(request):
    """Company story, team, and values."""
    context = {
        "page_title": _("About - Arborist"),
        "meta_description": _(
            "Learn about our team of certified arborists "
            "and our commitment to professional tree care."
        ),
    }
    return render(request, "arborist/about.html", context)


@require_GET
def services(request):
    """Services showcase."""
    context = {
        "page_title": _("Our Services - Arborist"),
        "meta_description": _(
            "Explore our professional tree care services: "
            "pruning, removal, health assessment, and consultation."
        ),
    }
    return render(request, "arborist/services.html", context)


@require_GET
def contact(request):
    """Contact information."""
    context = {
        "page_title": _("Contact Us - Arborist"),
        "meta_description": _(
            "Get in touch with Arborist. Contact information for "
            "consultations and service inquiries."
        ),
    }
    return render(request, "arborist/contact.html", context)
