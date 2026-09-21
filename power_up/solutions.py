"""
Event products Power-Up sells to businesses, shown on the Solutions page.

Kept apart from PLATFORMS (platforms.py): that list is the company's own
portfolio, this one is what a venue, agency or company can buy.

Every feature listed here runs in production on Crush.lu today. Do not add a
capability that is only planned -- promo codes, invoices, ticket tiers, guest
checkout, digital scorecards and organiser self-service do not exist yet, and
a branded platform per client is offered to 2027 pilot partners only.
"""

from django.utils.translation import gettext_lazy as _

SOLUTIONS = [
    {
        "slug": "ticketing",
        "name": _("Ticketing & Sales"),
        "tagline": _("Sell seats online and run a smooth door"),
        "description": _(
            "Online card payments, capacity limits and an automatic waitlist, "
            "followed by QR tickets that your team scans at the door."
        ),
        "icon": "ticket",
        "features": [
            _("Online card payment"),
            _("Capacity limits and an automatic waitlist"),
            _("QR tickets with live door check-in"),
            _("Printed tickets at the door"),
            _("Apple Wallet and Google Wallet passes"),
            _("Confirmation and reminder emails"),
            _("Cancellation and refund handling"),
            _("Attendee list export"),
        ],
    },
    {
        "slug": "quiz",
        "name": _("Quiz Nights"),
        "tagline": _("A live quiz run from one host console"),
        "description": _(
            "Tables answer on their phones while questions and scores "
            "appear on the big screen. The host moves through the rounds, "
            "and table rotation and the leaderboard are handled for you."
        ),
        "icon": "quiz",
        "features": [
            _("Host console with a PIN-protected projector screen"),
            _("Multiple-choice, true/false and open questions"),
            _("Image, video and audio questions"),
            _("Table rotation and team scoring"),
            _("Live leaderboard"),
            _("Ready-made question packs in English, French and German"),
        ],
    },
    {
        "slug": "speed-dating",
        "name": _("Speed Dating"),
        "tagline": _("Singles evenings for venues and companies"),
        "description": _(
            "A structured evening that keeps every table balanced, from "
            "registration through the last round."
        ),
        "icon": "rounds",
        "features": [
            _("Balanced registration"),
            _("Three-part evening: activity vote, introductions, dating rounds"),
            _("Countdown on the projector for every round"),
            _("Printed table cards with anonymous numbers"),
        ],
        "beta_features": [
            _("Curated groups where nobody meets the same person twice"),
        ],
    },
]
