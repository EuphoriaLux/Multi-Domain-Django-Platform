"""
Views for Baumwart - Tom Aakrann (arborist.lu).

Professional tree care services in Luxembourg.
All views are simple template renders with SEO context.
"""

from django.shortcuts import render, redirect
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods
from django.contrib import messages
import logging

from azureproject.email_utils import send_domain_email
from .forms import ContactForm

logger = logging.getLogger(__name__)


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


@require_http_methods(["GET", "POST"])
def contact(request):
    """Contact information and form with email handling."""
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            # Extract form data
            name = form.cleaned_data["name"]
            email = form.cleaned_data["email"]
            phone = form.cleaned_data["phone"] or "Not provided"
            service = form.cleaned_data["service"] or "Not specified"
            message_text = form.cleaned_data["message"]

            # Service name mapping
            service_names = {
                "obstbaumpflege": "Fruit Tree Care",
                "baumpflege": "Tree Care",
                "baumkontrolle": "Tree Inspection",
                "oekologie": "Ecological Measures",
                "beratung": "General Consultation",
            }
            service_display = service_names.get(service, service)

            # Compose email
            subject = f"[Arborist.lu] New inquiry from {name}"
            email_body = f"""
New contact form submission from arborist.lu:

Name: {name}
Email: {email}
Phone: {phone}
Service Interest: {service_display}

Message:
{message_text}

---
This message was sent via the arborist.lu contact form.
"""

            try:
                # Send notification to Tom (with CC to other addresses)
                send_domain_email(
                    subject=subject,
                    message=email_body,
                    recipient_list=["tom@arborist.lu"],
                    cc=["tom@powerup.lu", "taakrann@pt.lu"],
                    request=request,  # Auto-detects arborist.lu domain
                    fail_silently=False,
                )

                # Send confirmation email to the sender (multi-language)
                confirmation_subject = "Arborist.lu - Merci / Danke / Thank you"
                confirmation_body = f"""
Moien {name},

Merci fir Är Noriicht! Ech hunn Är Ufro kritt an äntweren Iech esou séier wéi méiglech.

---

Hallo {name},

Vielen Dank für Ihre Nachricht! Ich habe Ihre Anfrage erhalten und werde mich so schnell wie möglich bei Ihnen melden.

---

Bonjour {name},

Merci pour votre message ! J'ai bien reçu votre demande et je vous répondrai dans les plus brefs délais.

---

Hello {name},

Thank you for your message! I have received your inquiry and will get back to you as soon as possible.

---

Mat frëndleche Gréiss / Mit freundlichen Grüßen / Cordialement / Best regards,

Tom Aakrann
Arborist.lu
+352 621 981 363
"""
                send_domain_email(
                    subject=confirmation_subject,
                    message=confirmation_body,
                    recipient_list=[email],
                    request=request,
                    fail_silently=True,  # Don't fail if confirmation fails
                )

                messages.success(
                    request,
                    _(
                        "Thank you for your message! I will get back to you as soon as possible."
                    ),
                )
                logger.info(f"Contact form submitted by {email} for {service}")
                return redirect("arborist:contact")
            except Exception as e:
                logger.error(f"Failed to send contact email: {e}")
                messages.error(
                    request,
                    _(
                        "Sorry, there was an error sending your message. "
                        "Please try calling or WhatsApp instead."
                    ),
                )
    else:
        form = ContactForm()

    context = {
        "page_title": _("Contact - Arborist Tom Aakrann"),
        "meta_description": _(
            "Contact Arborist Tom Aakrann for tree care in Luxembourg. "
            "Free consultation, quick response. Phone, WhatsApp, email."
        ),
        "form": form,
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
