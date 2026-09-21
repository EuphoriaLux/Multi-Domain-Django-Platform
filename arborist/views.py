"""
Views for Baumwart - Tom Aakrann (arborist.lu).

Professional tree care services in Luxembourg.
Includes landing pages, service showcases, gallery, and interactive booking system
with distance-based prepayment zones around Altrier (Junglinster) and emergency Rush Orders.
"""

import logging
import urllib.parse
from django.http import JsonResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from azureproject.email_utils import send_domain_email
from crush_lu.decorators import ratelimit
from .forms import BookingForm
from .models import ArboristBooking
from .services.payment import get_prepayment_bank_details
from .services.phone import to_whatsapp_number
from .services.zones import calculate_zone, clean_postal_code, is_valid_postal_code

logger = logging.getLogger(__name__)

# Both public forms mail through the shared Graph sender, including an
# auto-reply to whatever address was typed in, so cap POSTs per client IP.
PUBLIC_FORM_RATE = "10/h"


# =============================================================================
# Home & General Pages
# =============================================================================


@require_GET
def home(request):
    """Landing page with hero, services overview, zone pricing teaser, and trust markers."""
    context = {
        "page_title": _("Arborist Tom Aakrann - Professional Tree Care Luxembourg"),
        "meta_description": _(
            "Certified tree inspector and arborist in Luxembourg. "
            "Fruit tree care, tree care, tree inspection according to FLL standards. "
            "SKT-B rope climbing technique. Based in Altrier (Junglinster)."
        ),
    }
    return render(request, "arborist/home.html", context)


@require_GET
def services(request):
    """Overview of all tree care services."""
    context = {
        "page_title": _("Services - Arborist Tom Aakrann Luxembourg"),
        "meta_description": _(
            "Overview of professional tree care services in Luxembourg: "
            "Fruit tree care, tree pruning, FLL tree inspection, tree felling, and ecological measures."
        ),
    }
    return render(request, "arborist/services.html", context)


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
def gallery(request):
    """Photo gallery with authentic project photos."""
    context = {
        "page_title": _("Gallery - Arborist Tom Aakrann"),
        "meta_description": _(
            "Photos of tree care projects in Luxembourg: Seilklettertechnik (SKT-B), "
            "spider crawler platform, crown maintenance, and tree diagnostics."
        ),
    }
    return render(request, "arborist/gallery.html", context)


@require_GET
def faq(request):
    """Frequently asked questions."""
    faq_items = [
        {
            "question": _("How much does an on-site consultation or inspection cost?"),
            "answer": _(
                "To provide qualified planning and traffic-safety inspection, we charge a fixed "
                "prepayment based on distance from our base in Altrier: Zone 1 (around Altrier/Junglinster) is 50 €, "
                "and Zone 2 (rest of Luxembourg) is 100 €. This fee is 100% credited against your final service invoice upon execution!"
            ),
        },
        {
            "question": _("What is a Rush Order appointment?"),
            "answer": _(
                "For urgent situations such as storm damage, fallen branches, or imminent tree hazards, "
                "please call us to discuss availability. A rush appointment request does not guarantee immediate attendance."
            ),
        },
        {
            "question": _("When is the best time for fruit tree pruning?"),
            "answer": _(
                "Pome fruits (apple, pear) are typically pruned during late winter dormancy, "
                "while stone fruits (cherry, plum) are pruned in summer after harvest."
            ),
        },
        {
            "question": _("Do you work with rope climbing technique?"),
            "answer": _(
                "Yes, Tom Aakrann is SKT-B certified and uses professional rope climbing techniques "
                "as well as a compact tracked spider crawler lift for hard-to-reach locations."
            ),
        },
        {
            "question": _("In which regions of Luxembourg do you operate?"),
            "answer": _(
                "We operate throughout all of Luxembourg, with fast local coverage in the East, "
                "Center, and Müllerthal from our Altrier base."
            ),
        },
    ]

    context = {
        "page_title": _("FAQ - Frequently Asked Questions - Arborist Tom Aakrann"),
        "meta_description": _(
            "Frequently asked questions about tree care, distance zones, rush orders, and methods in Luxembourg."
        ),
        "faq_items": faq_items,
    }
    return render(request, "arborist/faq.html", context)


# =============================================================================
# Booking System & Zone Calculator Views
# =============================================================================

# Booking references this browser session submitted; only these receipts open.
BOOKING_SESSION_KEY = "arborist_booking_refs"
MAX_REMEMBERED_BOOKINGS = 10


def _remember_booking(request, reference):
    """Let this browser session, and only it, reopen the booking's receipt page."""
    refs = [r for r in request.session.get(BOOKING_SESSION_KEY, []) if r != reference]
    refs.append(reference)
    request.session[BOOKING_SESSION_KEY] = refs[-MAX_REMEMBERED_BOOKINGS:]


def _client_confirmation_body(booking_obj, quote):
    """
    The customer's confirmation email in the active language: i18n_patterns has
    activated the /en/, /de/ or /fr/ of the form they submitted, so every _()
    below resolves in it.
    """
    ref = booking_obj.booking_reference
    urgency = _("Rush Order (24-48h)") if booking_obj.is_rush else _("Standard")
    lines = [
        _("Hello %(name)s,") % {"name": booking_obj.name},
        "",
        str(_("Thank you for your appointment request with Arborist Tom Aakrann!")),
        "",
        "%s: %s" % (_("Booking reference"), ref),
        "%s: %s" % (_("Service"), booking_obj.get_service_type_display()),
        "%s: %s, %s %s"
        % (
            _("Address"),
            booking_obj.street_address,
            booking_obj.postal_code,
            booking_obj.city_or_commune,
        ),
        "%s: Zone %s (~%s km)" % (_("Zone"), quote.zone, quote.distance_km),
        "%s: %s" % (_("Urgency"), urgency),
        "",
        "%s: %.2f €" % (_("Prepayment"), quote.total_prepayment_eur),
        str(
            _(
                "This amount is credited 100% against the final invoice once the work is carried out."
            )
        ),
        "",
    ]
    bank = get_prepayment_bank_details()
    if bank:
        lines += [
            str(_("Bank details for the prepayment:")),
            "%s: %s" % (_("Recipient"), bank.account_holder),
            "IBAN: %s" % bank.iban_display,
        ]
        if bank.bic:
            lines.append("BIC: %s" % bank.bic)
        lines.append("%s: %s" % (_("Payment reference"), ref))
    else:
        lines.append(
            str(
                _(
                    "You will receive the bank details for the prepayment with the final appointment confirmation."
                )
            )
        )
    lines += [
        "",
        str(_("We will contact you shortly to confirm the exact appointment.")),
        "",
        str(_("Kind regards,")),
        "Tom Aakrann",
        "Arborist.lu",
        "+352 621 981 363",
    ]
    return "\n".join(lines) + "\n"


@require_GET
def api_calculate_zone(request):
    """
    JSON API for real-time frontend zone and prepayment calculation.
    Query params: postal_code, is_rush (true/false), city (optional).
    """
    postal_code = clean_postal_code(request.GET.get("postal_code", ""))
    if not is_valid_postal_code(postal_code):
        return JsonResponse(
            {"error": "postal_code must be a 4-digit Luxembourg postal code"},
            status=400,
        )
    is_rush = request.GET.get("is_rush", "").lower() in ("true", "1", "yes", "on")
    city = request.GET.get("city", "")

    quote = calculate_zone(
        postal_code=postal_code, city_or_commune=city, is_rush=is_rush
    )
    return JsonResponse(quote.to_dict())


@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate=PUBLIC_FORM_RATE, method="POST")
def booking(request):
    """
    Interactive appointment booking view with distance-based prepayment zones.
    """
    initial = {}
    lead = None
    if request.GET.get("lead"):
        import uuid
        from .views_leads import customer_lead

        try:
            lead_id = uuid.UUID(request.GET["lead"])
        except ValueError:
            raise Http404
        lead = customer_lead(request, lead_id)
        initial.update(
            name=lead.name,
            email=lead.email,
            phone=lead.phone,
            postal_code=lead.postal_code,
            notes=lead.message,
        )
    service_param = request.GET.get("service")
    if service_param in dict(ArboristBooking.SERVICE_CHOICES):
        initial["service_type"] = service_param
    # The emergency CTAs link here with ?service=notdienst&rush=1: tick the rush
    # box for them, so the quote the customer sees already has the surcharge.
    if service_param == "notdienst" or request.GET.get("rush") == "1":
        initial["is_rush"] = True

    if request.method == "POST":
        form = BookingForm(request.POST)
        if form.is_valid():
            booking_obj = form.save(commit=False)
            booking_obj.lead = lead
            quote = booking_obj.apply_quote()
            booking_obj.save()
            ref = booking_obj.booking_reference
            _remember_booking(request, ref)

            # Prepare notification emails
            whatsapp_number = to_whatsapp_number(booking_obj.phone)
            if whatsapp_number:
                whatsapp_msg = urllib.parse.quote(
                    f"Moien {booking_obj.name}, hei ass den Tom Aakrann vun Arborist.lu wéinst Ärer Buchung {ref}."
                )
                whatsapp_url = f"https://wa.me/{whatsapp_number}?text={whatsapp_msg}"
            else:
                whatsapp_url = "n/a (phone number has no country code - call instead)"

            if booking_obj.is_rush:
                urgency_tag, urgency = "🚨 RUSH ORDER", "🚨 RUSH ORDER (24-48h)"
            elif booking_obj.is_urgent:
                urgency_tag = "🚨 EMERGENCY"
                urgency = "🚨 Emergency service (rush surcharge not selected)"
            else:
                urgency_tag, urgency = "New Booking", "Standard"
            admin_subject = f"[{urgency_tag}] Arborist.lu: {ref} - {booking_obj.name} ({quote.zone_name})"
            admin_body = f"""Arborist.lu Booking Request:

Reference: {ref}
Urgency: {urgency}
Client: {booking_obj.name}
Email: {booking_obj.email}
Phone: {booking_obj.phone}
Address: {booking_obj.street_address}, {booking_obj.postal_code} {booking_obj.city_or_commune}

Service: {booking_obj.get_service_type_display()}
Number of Trees: {booking_obj.number_of_trees or 'Not specified'}
Preferred Date: {booking_obj.preferred_date or 'Flexible'}
Preferred Time: {booking_obj.get_preferred_time_slot_display()}

Calculated Zone: Zone {quote.zone} (~{quote.distance_km} km from Altrier)
Prepayment Amount: {quote.total_prepayment_eur:.2f} €
(Base: {quote.base_prepayment_eur:.2f} €, Rush: {quote.rush_fee_eur:.2f} €)

Client Notes:
{booking_obj.notes or 'No additional notes.'}

---
WhatsApp Quick Action: {whatsapp_url}
Admin Dashboard: https://arborist.lu/arborist-admin/arborist/arboristbooking/{booking_obj.id}/change/
"""

            try:
                send_domain_email(
                    subject=admin_subject,
                    message=admin_body,
                    recipient_list=["tom@arborist.lu"],
                    cc=["tom@powerup.lu", "taakrann@pt.lu"],
                    request=request,
                    fail_silently=False,
                )
            except Exception as e:
                logger.error("Failed to send booking notification to Tom: %s", e)

            # Send client confirmation, in the language the form was used in
            client_subject = f"Arborist.lu - {_('Booking Confirmation')} {ref}"
            client_body = _client_confirmation_body(booking_obj, quote)
            try:
                send_domain_email(
                    subject=client_subject,
                    message=client_body,
                    recipient_list=[booking_obj.email],
                    request=request,
                    fail_silently=True,
                )
            except Exception as e:
                logger.warning("Failed to send booking confirmation to client: %s", e)

            # No flash message: the success page is the confirmation and renders
            # no messages, so one queued here would surface on the next page.
            return redirect("arborist:booking_success", reference=ref)
    else:
        form = BookingForm(initial=initial)

    context = {
        "page_title": _("Book an Appointment - Arborist Tom Aakrann"),
        "meta_description": _(
            "Book an on-site tree care appointment or inspection. "
            "Transparent distance-based prepayment (Zone 1: 50€, Zone 2: 100€). Rush orders available."
        ),
        "form": form,
    }
    return render(request, "arborist/booking.html", context)


@never_cache
@require_GET
def booking_success(request, reference):
    """
    Booking confirmation screen with summary and payment details.

    The page shows the customer's home address, and the reference is short and
    partly predictable, so it is not a credential: only the browser session
    that submitted the booking can open it. Anyone else gets the same 404 as
    for an unknown reference.
    """
    if reference not in request.session.get(BOOKING_SESSION_KEY, []):
        raise Http404
    booking_obj = get_object_or_404(ArboristBooking, booking_reference=reference)
    context = {
        "page_title": _("Booking Received - Arborist Tom Aakrann"),
        "booking": booking_obj,
        "bank": get_prepayment_bank_details(),
    }
    return render(request, "arborist/booking_success.html", context)
