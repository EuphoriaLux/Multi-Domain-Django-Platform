"""
URL patterns for Baumwart - Tom Aakrann (arborist.lu).

Professional tree care services in Luxembourg.
These are included by azureproject/urls_arborist.py for the arborist.lu domain.
"""

from django.urls import path

from . import views

app_name = "arborist"

urlpatterns = [
    # Home
    path("", views.home, name="home"),
    # Services
    path("obstbaumpflege/", views.obstbaumpflege, name="obstbaumpflege"),
    path("baumpflege/", views.baumpflege, name="baumpflege"),
    path("baumkontrolle/", views.baumkontrolle, name="baumkontrolle"),
    path("oekologie/", views.oekologie, name="oekologie"),
    path("technik/", views.technik, name="technik"),
    # About & Contact
    path("ueber-uns/", views.about, name="about"),
    path("kontakt/", views.contact, name="contact"),
    # Gallery & FAQ
    path("galerie/", views.gallery, name="gallery"),
    path("faq/", views.faq, name="faq"),
]
