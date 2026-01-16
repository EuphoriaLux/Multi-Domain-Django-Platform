"""
Views for Baumwart - Tom Aakrann (arborist.lu).

Professional tree care services in Luxembourg.
All views are simple template renders with SEO context.
"""

from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET


# =============================================================================
# Home Page
# =============================================================================


@require_GET
def home(request):
    """Landing page with hero, services overview, and trust markers."""
    context = {
        "page_title": _("Baumwart Tom Aakrann - Professionelle Baumpflege Luxemburg"),
        "meta_description": _(
            "Zertifizierter Baumkontrolleur und Baumpfleger in Luxemburg. "
            "Obstbaumpflege, Baumpflege, Baumkontrolle nach FLL. "
            "Seilklettertechnik SKT-B."
        ),
    }
    return render(request, "arborist/home.html", context)


# =============================================================================
# Service Pages
# =============================================================================


@require_GET
def obstbaumpflege(request):
    """Fruit tree care services."""
    context = {
        "page_title": _("Obstbaumpflege Luxemburg - Baumwart Tom Aakrann"),
        "meta_description": _(
            "Professionelle Obstbaumpflege in Luxemburg. Obstbaumschnitt, "
            "Verjüngungsschnitt, Erziehungsschnitt. Für gesunde und ertragreiche Obstbäume."
        ),
        "service_name": _("Obstbaumpflege"),
        "service_type": "obstbaumpflege",
    }
    return render(request, "arborist/services/obstbaumpflege.html", context)


@require_GET
def baumpflege(request):
    """Native tree care services."""
    context = {
        "page_title": _("Baumpflege Luxemburg - Baumwart Tom Aakrann"),
        "meta_description": _(
            "Professionelle Baumpflege in Luxemburg. Kronenpflege, Totholzentfernung, "
            "Kroneneinkürzung. Seilklettertechnik SKT-B zertifiziert."
        ),
        "service_name": _("Baumpflege"),
        "service_type": "baumpflege",
    }
    return render(request, "arborist/services/baumpflege.html", context)


@require_GET
def baumkontrolle(request):
    """Certified tree inspection services."""
    context = {
        "page_title": _("Baumkontrolle Luxemburg - FLL zertifiziert - Baumwart"),
        "meta_description": _(
            "FLL-zertifizierte Baumkontrolle in Luxemburg. Visuelle Baumkontrolle, "
            "Baumkataster, Verkehrssicherheitsprüfung. Gutachten und Dokumentation."
        ),
        "service_name": _("Baumkontrolle"),
        "service_type": "baumkontrolle",
    }
    return render(request, "arborist/services/baumkontrolle.html", context)


@require_GET
def oekologie(request):
    """Ecological services."""
    context = {
        "page_title": _("Ökologische Maßnahmen - Baumwart Tom Aakrann"),
        "meta_description": _(
            "Ökologische Baumpflege in Luxemburg. Habitatbäume, Totholzmanagement, "
            "Nisthilfen, Artenschutz. Naturnahe Baumpflege für Biodiversität."
        ),
        "service_name": _("Ökologische Maßnahmen"),
        "service_type": "oekologie",
    }
    return render(request, "arborist/services/oekologie.html", context)


@require_GET
def technik(request):
    """Equipment and methods."""
    context = {
        "page_title": _("Technik & Methoden - Baumwart Tom Aakrann"),
        "meta_description": _(
            "Moderne Baumpflegetechnik in Luxemburg. Seilklettertechnik SKT-B, "
            "Hubarbeitsbühne, professionelle Ausrüstung für sichere Baumpflege."
        ),
        "service_name": _("Technik"),
        "service_type": "technik",
    }
    return render(request, "arborist/services/technik.html", context)


# =============================================================================
# About & Contact
# =============================================================================


@require_GET
def about(request):
    """About Tom Aakrann and credentials."""
    context = {
        "page_title": _("Über uns - Baumwart Tom Aakrann"),
        "meta_description": _(
            "Tom Aakrann - FLL-zertifizierter Baumkontrolleur und Baumpfleger "
            "in Luxemburg. Erfahrung, Qualifikationen und Leidenschaft für Bäume."
        ),
    }
    return render(request, "arborist/about.html", context)


@require_GET
def contact(request):
    """Contact information and form."""
    context = {
        "page_title": _("Kontakt - Baumwart Tom Aakrann"),
        "meta_description": _(
            "Kontaktieren Sie Baumwart Tom Aakrann für Baumpflege in Luxemburg. "
            "Kostenlose Beratung, schnelle Antwort. Telefon, WhatsApp, E-Mail."
        ),
    }
    return render(request, "arborist/contact.html", context)


# =============================================================================
# Gallery & FAQ
# =============================================================================


@require_GET
def gallery(request):
    """Photo gallery with before/after images."""
    context = {
        "page_title": _("Galerie - Baumwart Tom Aakrann"),
        "meta_description": _(
            "Fotos von Baumpflege-Projekten in Luxemburg. Vorher-Nachher Bilder "
            "von Obstbaumpflege, Kronenpflege und Baumfällung."
        ),
    }
    return render(request, "arborist/gallery.html", context)


@require_GET
def faq(request):
    """Frequently asked questions."""
    # FAQ items for structured data
    faq_items = [
        {
            "question": _("Was kostet eine Baumkontrolle?"),
            "answer": _(
                "Die Kosten für eine Baumkontrolle hängen von der Anzahl der Bäume "
                "und dem Aufwand ab. Kontaktieren Sie mich für ein individuelles Angebot."
            ),
        },
        {
            "question": _("Wann ist die beste Zeit für Obstbaumschnitt?"),
            "answer": _(
                "Der beste Zeitpunkt für den Obstbaumschnitt ist je nach Obstart "
                "verschieden. Kernobst (Apfel, Birne) wird meist im Winter geschnitten, "
                "Steinobst (Kirsche, Pflaume) nach der Ernte im Sommer."
            ),
        },
        {
            "question": _("Arbeiten Sie auch mit Seilklettertechnik?"),
            "answer": _(
                "Ja, ich bin SKT-B zertifiziert und arbeite mit professioneller "
                "Seilklettertechnik. Dies ermöglicht baumschonende Pflege auch ohne "
                "schwere Maschinen."
            ),
        },
        {
            "question": _("In welchen Regionen sind Sie tätig?"),
            "answer": _(
                "Ich bin in ganz Luxemburg tätig, mit Schwerpunkt auf dem Zentrum "
                "und Süden des Landes."
            ),
        },
        {
            "question": _("Bieten Sie auch Notfalldienst an?"),
            "answer": _(
                "Ja, bei Sturmschäden oder anderen Notfällen bin ich auch kurzfristig "
                "erreichbar. Kontaktieren Sie mich telefonisch für dringende Anfragen."
            ),
        },
    ]

    context = {
        "page_title": _("FAQ - Häufige Fragen - Baumwart Tom Aakrann"),
        "meta_description": _(
            "Häufig gestellte Fragen zu Baumpflege in Luxemburg. "
            "Antworten zu Kosten, Zeitpunkt, Methoden und Leistungen."
        ),
        "faq_items": faq_items,
    }
    return render(request, "arborist/faq.html", context)
