"""
Views for Power-Up corporate/investor site.

All views are simple template renders - no forms, no authentication.
This is a static marketing site for investors and partners.
"""

from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from .platforms import PLATFORMS


def home(request):
    """Landing page with hero, mission, and portfolio preview."""
    context = {
        "platforms": PLATFORMS,
        "page_title": _("Power-Up - Building Luxembourg's Digital Future"),
        "meta_description": _(
            "Power-Up builds digital platforms for Luxembourg. "
            "Discover our portfolio: Crush.lu, VinsDelux, and PowerUP."
        ),
    }
    return render(request, "power_up/home.html", context)


def about(request):
    """Company story, team, and values."""
    context = {
        "page_title": _("About Power-Up"),
        "meta_description": _(
            "Learn about Power-Up, the company building Luxembourg's "
            "digital future through innovative platforms."
        ),
    }
    return render(request, "power_up/about.html", context)


def platforms(request):
    """Portfolio showcase with all platforms."""
    context = {
        "platforms": PLATFORMS,
        "page_title": _("Our Platforms - Power-Up"),
        "meta_description": _(
            "Explore Power-Up's portfolio of digital platforms: "
            "Crush.lu dating, VinsDelux wine adoption, and PowerUP networking."
        ),
    }
    return render(request, "power_up/platforms.html", context)


def investors(request):
    """Investment and partnership information."""
    context = {
        "page_title": _("Investors & Partners - Power-Up"),
        "meta_description": _(
            "Partner with Power-Up to build Luxembourg's digital future. "
            "Investment and partnership opportunities."
        ),
    }
    return render(request, "power_up/investors.html", context)


def contact(request):
    """Static contact information block."""
    context = {
        "page_title": _("Contact Us - Power-Up"),
        "meta_description": _(
            "Get in touch with Power-Up. Contact information for "
            "investors, partners, and general inquiries."
        ),
    }
    return render(request, "power_up/contact.html", context)
