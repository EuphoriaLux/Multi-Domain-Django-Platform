"""
URL patterns for Baumwart - Tom Aakrann (arborist.lu).

Professional tree care services in Luxembourg.
Supports internationalization with language-prefixed URLs (/en/, /de/, /fr/).
"""

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "arborist"


def _alias(pattern_name):
    """
    301 an alias slug to its page's one canonical route.

    Every route is mounted under i18n_patterns, so each language accepts every
    slug; serving the page on all of them would make each copy advertise itself
    as canonical. reverse() runs under the request's /<lang>/ prefix, so the
    redirect stays in the visitor's language, and the query string is kept
    (e.g. /en/booking/?service=notdienst&rush=1).
    """
    return RedirectView.as_view(
        pattern_name=f"arborist:{pattern_name}", permanent=True, query_string=True
    )


urlpatterns = [
    # Home
    path("", views.home, name="home"),

    # Services Overview & Details
    path("services/", views.services, name="services"),
    path("obstbaumpflege/", views.obstbaumpflege, name="obstbaumpflege"),
    path("baumpflege/", views.baumpflege, name="baumpflege"),
    path("baumkontrolle/", views.baumkontrolle, name="baumkontrolle"),
    path("oekologie/", views.oekologie, name="oekologie"),
    path("technik/", views.technik, name="technik"),

    # About (other-language slugs redirect to the canonical route)
    path("ueber-uns/", views.about, name="about"),
    path("about/", _alias("about")),
    path("a-propos/", _alias("about")),

    # Contact
    path("kontakt/", views.contact, name="contact"),
    path("contact/", _alias("contact")),

    # Booking Flow & API
    path("termin/", views.booking, name="booking"),
    path("booking/", _alias("booking")),
    path("rendez-vous/", _alias("booking")),
    path("booking/success/<str:reference>/", views.booking_success, name="booking_success"),
    path("api/zone-lookup/", views.api_calculate_zone, name="api_calculate_zone"),

    # Gallery & FAQ
    path("galerie/", views.gallery, name="gallery"),
    path("gallery/", _alias("gallery")),
    path("faq/", views.faq, name="faq"),
]
