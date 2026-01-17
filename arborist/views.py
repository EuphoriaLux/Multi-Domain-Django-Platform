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
        "page_title": _("Arborist Tom Aakrann - Professional Tree Care Luxembourg"),
        "meta_description": _(
            "Certified tree inspector and arborist in Luxembourg. "
            "Fruit tree care, tree care, tree inspection according to FLL standards. "
            "SKT-B rope climbing technique."
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
        "page_title": _("Fruit Tree Care Luxembourg - Arborist Tom Aakrann"),
        "meta_description": _(
            "Professional fruit tree care in Luxembourg. Fruit tree pruning, "
            "rejuvenation pruning, training pruning. For healthy and productive fruit trees."
        ),
        "service_name": _("Fruit Tree Care"),
        "service_type": "obstbaumpflege",
    }
    return render(request, "arborist/services/obstbaumpflege.html", context)


@require_GET
def baumpflege(request):
    """Native tree care services."""
    context = {
        "page_title": _("Tree Care Luxembourg - Arborist Tom Aakrann"),
        "meta_description": _(
            "Professional tree care in Luxembourg. Crown maintenance, deadwood removal, "
            "crown reduction. SKT-B rope climbing technique certified."
        ),
        "service_name": _("Tree Care"),
        "service_type": "baumpflege",
    }
    return render(request, "arborist/services/baumpflege.html", context)


@require_GET
def baumkontrolle(request):
    """Certified tree inspection services."""
    context = {
        "page_title": _("Tree Inspection Luxembourg - FLL Certified - Arborist"),
        "meta_description": _(
            "FLL-certified tree inspection in Luxembourg. Visual tree inspection, "
            "tree inventory, traffic safety assessment. Reports and documentation."
        ),
        "service_name": _("Tree Inspection"),
        "service_type": "baumkontrolle",
    }
    return render(request, "arborist/services/baumkontrolle.html", context)


@require_GET
def oekologie(request):
    """Ecological services."""
    context = {
        "page_title": _("Ecological Measures - Arborist Tom Aakrann"),
        "meta_description": _(
            "Ecological tree care in Luxembourg. Habitat trees, deadwood management, "
            "nesting aids, species protection. Nature-friendly tree care for biodiversity."
        ),
        "service_name": _("Ecological Measures"),
        "service_type": "oekologie",
    }
    return render(request, "arborist/services/oekologie.html", context)


@require_GET
def technik(request):
    """Equipment and methods."""
    context = {
        "page_title": _("Techniques & Methods - Arborist Tom Aakrann"),
        "meta_description": _(
            "Modern tree care techniques in Luxembourg. SKT-B rope climbing technique, "
            "aerial work platform, professional equipment for safe tree care."
        ),
        "service_name": _("Techniques"),
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
        "page_title": _("About Me - Arborist Tom Aakrann"),
        "meta_description": _(
            "Tom Aakrann - FLL-certified tree inspector and arborist "
            "in Luxembourg. Experience, qualifications, and passion for trees."
        ),
    }
    return render(request, "arborist/about.html", context)


@require_GET
def contact(request):
    """Contact information and form."""
    context = {
        "page_title": _("Contact - Arborist Tom Aakrann"),
        "meta_description": _(
            "Contact Arborist Tom Aakrann for tree care in Luxembourg. "
            "Free consultation, quick response. Phone, WhatsApp, email."
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
        "page_title": _("Gallery - Arborist Tom Aakrann"),
        "meta_description": _(
            "Photos of tree care projects in Luxembourg. Before and after images "
            "of fruit tree care, crown maintenance, and tree removal."
        ),
    }
    return render(request, "arborist/gallery.html", context)


@require_GET
def faq(request):
    """Frequently asked questions."""
    # FAQ items for structured data
    faq_items = [
        {
            "question": _("How much does a tree inspection cost?"),
            "answer": _(
                "The cost of a tree inspection depends on the number of trees "
                "and the effort required. Contact me for an individual quote."
            ),
        },
        {
            "question": _("When is the best time for fruit tree pruning?"),
            "answer": _(
                "The best time for fruit tree pruning varies depending on the type of fruit. "
                "Pome fruit (apple, pear) is usually pruned in winter, "
                "stone fruit (cherry, plum) after harvest in summer."
            ),
        },
        {
            "question": _("Do you also work with rope climbing technique?"),
            "answer": _(
                "Yes, I am SKT-B certified and work with professional "
                "rope climbing technique. This enables tree-friendly care even without "
                "heavy machinery."
            ),
        },
        {
            "question": _("In which regions do you operate?"),
            "answer": _(
                "I operate throughout Luxembourg, with a focus on the center "
                "and south of the country."
            ),
        },
        {
            "question": _("Do you also offer emergency services?"),
            "answer": _(
                "Yes, for storm damage or other emergencies I am also available on short notice. "
                "Contact me by phone for urgent inquiries."
            ),
        },
    ]

    context = {
        "page_title": _("FAQ - Frequently Asked Questions - Arborist Tom Aakrann"),
        "meta_description": _(
            "Frequently asked questions about tree care in Luxembourg. "
            "Answers about costs, timing, methods, and services."
        ),
        "faq_items": faq_items,
    }
    return render(request, "arborist/faq.html", context)
