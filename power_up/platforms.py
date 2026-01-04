"""
Portfolio data structure for Power-Up platforms.

Add new platforms by appending to the PLATFORMS list.
Icons use Heroicons naming convention (outline style).
"""

from django.utils.translation import gettext_lazy as _

PLATFORMS = [
    {
        "slug": "crush-lu",
        "name": "Crush.lu",
        "tagline": _("Privacy-first dating for Luxembourg"),
        "description": _(
            "A coach-curated dating platform focused on real connections "
            "through organized events. No endless swiping - just authentic "
            "meetups with people who share your values."
        ),
        "url": "https://crush.lu",
        "icon": "heart",
        "status": "live",
        "launched": "2025",
        "highlights": [
            _("Event-based matchmaking"),
            _("Coach-reviewed profiles"),
            _("Privacy controls"),
            _("Luxembourg community focus"),
        ],
    },
    {
        "slug": "vinsdelux",
        "name": "VinsDelux",
        "tagline": _("Adopt a vineyard plot in Luxembourg"),
        "description": _(
            "Premium wine e-commerce with a unique twist - adopt your own "
            "vineyard plot and receive wines from your personal vines. "
            "Experience Luxembourg's Moselle wine culture firsthand."
        ),
        "url": "https://vinsdelux.com",
        "icon": "sparkles",
        "status": "live",
        "launched": "2024",
        "highlights": [
            _("Plot adoption program"),
            _("Luxembourg Moselle wines"),
            _("Vineyard visit experiences"),
            _("Direct from producers"),
        ],
    },
    {
        "slug": "entreprinder",
        "name": "Entreprinder",
        "tagline": _("Entrepreneur networking for Luxembourg"),
        "description": _(
            "Connect with fellow entrepreneurs using smart matching. "
            "Find co-founders, mentors, and business partners in "
            "Luxembourg's growing startup ecosystem."
        ),
        "url": "https://entreprinder.lu",
        "icon": "users",
        "status": "live",
        "launched": "2024",
        "highlights": [
            _("Smart matching algorithm"),
            _("LinkedIn integration"),
            _("Local business focus"),
            _("Startup ecosystem"),
        ],
    },
    {
        "slug": "finops-hub",
        "name": "FinOps Hub",
        "tagline": _("Azure cost management & analytics"),
        "description": _(
            "Comprehensive Azure cost management dashboard for enterprises. "
            "Track cloud spending, analyze trends, and optimize costs across "
            "all your Azure subscriptions in real-time."
        ),
        "url": "/finops/",
        "icon": "chart",
        "status": "live",
        "launched": "2025",
        "highlights": [
            _("Multi-subscription tracking"),
            _("Cost trend analysis"),
            _("Service-level breakdown"),
            _("Automated daily sync"),
        ],
    },
]
