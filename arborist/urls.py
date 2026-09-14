"""
URL patterns for Baumwart - Tom Aakrann (arborist.lu).

Professional tree care services in Luxembourg.
Supports internationalization with language-prefixed URLs (/en/, /de/, /fr/).
"""

from django.urls import path
from . import views

app_name = "arborist"

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

    # About (with multi-language aliases)
    path("ueber-uns/", views.about, name="about"),
    path("about/", views.about, name="about_en"),
    path("a-propos/", views.about, name="about_fr"),

    # Contact (with multi-language aliases)
    path("kontakt/", views.contact, name="contact"),
    path("contact/", views.contact, name="contact_en"),

    # Booking Flow & API
    path("termin/", views.booking, name="booking"),
    path("booking/", views.booking, name="booking_en"),
    path("rendez-vous/", views.booking, name="booking_fr"),
    path("booking/success/<str:reference>/", views.booking_success, name="booking_success"),
    path("api/zone-lookup/", views.api_calculate_zone, name="api_calculate_zone"),

    # Gallery & FAQ
    path("galerie/", views.gallery, name="gallery"),
    path("gallery/", views.gallery, name="gallery_en"),
    path("faq/", views.faq, name="faq"),
]
