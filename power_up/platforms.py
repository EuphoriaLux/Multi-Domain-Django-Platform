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
        "status_label": _("Owned product · Live"),
        "group": "featured",
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
        "description": _("A concept showcase for vineyard plot adoption in Luxembourg."),
        "url": "https://vinsdelux.com",
        "icon": "sparkles",
        "status": "concept",
        "status_label": _("Concept"),
        "group": "other",
        "launched": "2024",
        "highlights": [
            _("Vineyard adoption concept"),
        ],
    },
    {
        "slug": "entreprinder",
        "name": "Entreprinder",
        "tagline": _("Entrepreneur networking for Luxembourg"),
        "description": _("A networking site for Luxembourg entrepreneurs, currently at an early stage."),
        "url": "https://entreprinder.lu",
        "icon": "users",
        "status": "prototype",
        "status_label": _("Owned project · Early stage"),
        "group": "other",
        "launched": "2024",
        "highlights": [
            _("Entrepreneur networking"),
        ],
    },
    {
        "slug": "finops-hub",
        "name": "FinOps Hub",
        "tagline": _("Azure cost management & analytics"),
        "description": _("An internal tool for importing and reviewing Azure cost data."),
        "url": "/finops/",
        "icon": "chart",
        "status": "internal",
        "status_label": _("Internal tool · Restricted access"),
        "group": "featured",
        "launched": "2025",
        "highlights": [
            _("Cost data imports and reporting"),
        ],
    },
    {
        "slug": "tableau-lu",
        "name": "Tableau.lu",
        "tagline": _("AI-generated art e-commerce"),
        "description": _("An AI art gallery site presenting a collection that is coming soon."),
        "url": "https://tableau.lu",
        "icon": "palette",
        "status": "coming-soon",
        "status_label": _("Coming soon"),
        "group": "other",
        "launched": "2025",
        "highlights": [
            _("AI art gallery"),
        ],
    },
    {
        "slug": "delegations-lu",
        "name": "Delegations.lu",
        "tagline": _("Staff management portal"),
        "description": _("A restricted staff portal for events, requests and announcements with Microsoft sign-in."),
        "url": "https://delegations.lu",
        "icon": "clipboard",
        "status": "restricted",
        "status_label": _("Restricted portal"),
        "group": "other",
        "launched": "2025",
        "highlights": [
            _("Staff access with Microsoft sign-in"),
        ],
    },
]
