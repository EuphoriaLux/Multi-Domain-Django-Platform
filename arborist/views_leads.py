"""Public enquiries and session-scoped photo intake; no automated diagnosis."""

import logging
import uuid

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from crush_lu.decorators import ratelimit
from .forms_leads import LeadForm, LeadPhotoForm, PhotoNotesForm
from .models import ArboristLead, LeadEvent, LeadPhoto
from .services.leads import capture_attribution, notify_lead

logger = logging.getLogger(__name__)
TOKEN_KEY = "arborist_enquiry_tokens"
UPLOAD_KEY = "arborist_upload_tokens"


def issue_token(request, key):
    token = str(uuid.uuid4())
    request.session[key] = (request.session.get(key, []) + [token])[-20:]
    return token


def customer_lead(request, lead_id):
    lead = get_object_or_404(ArboristLead, pk=lead_id)
    if str(lead.submission_id) not in request.session.get(TOKEN_KEY, []):
        raise Http404
    return lead


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/h", method="POST")
def enquiry(request):
    attribution = capture_attribution(request)
    if request.method == "POST":
        form = LeadForm(request.POST)
        if form.is_valid():
            token = form.cleaned_data["submission_id"]
            if str(token) not in request.session.get(TOKEN_KEY, []):
                form.add_error(
                    None, _("This form has expired. Reload the page and try again.")
                )
            else:
                with transaction.atomic():
                    lead, created = ArboristLead.objects.get_or_create(
                        submission_id=token,
                        defaults={
                            **{
                                field: form.cleaned_data[field]
                                for field in (
                                    "name",
                                    "email",
                                    "phone",
                                    "postal_code",
                                    "service",
                                    "message",
                                    "is_urgent",
                                )
                            },
                            "language": (
                                request.LANGUAGE_CODE
                                if request.LANGUAGE_CODE in {"en", "de", "fr"}
                                else "en"
                            ),
                            "first_attribution": attribution.get("first", {}),
                            "last_attribution": attribution.get("last", {}),
                        },
                    )
                    if created:
                        LeadEvent.objects.create(
                            lead=lead, description="Enquiry received"
                        )
                        transaction.on_commit(lambda: notify_lead(lead.pk), robust=True)
                return redirect("arborist:lead_detail", lead_id=lead.pk)
    else:
        form = LeadForm(
            initial={
                "submission_id": issue_token(request, TOKEN_KEY),
                "service": request.GET.get("service", ""),
            }
        )
    return render(
        request,
        "arborist/contact.html",
        {
            "form": form,
            "page_title": _("Request a tree assessment — Arborist.lu"),
            "meta_description": _(
                "Describe your tree project and optionally add photos. On-site visits: 50 € or 100 €, credited against subsequent work."
            ),
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="30/h", method="POST")
def lead_detail(request, lead_id):
    lead = customer_lead(request, lead_id)
    photo_form = LeadPhotoForm(initial={"upload_id": issue_token(request, UPLOAD_KEY)})
    notes_form = PhotoNotesForm(initial={"photo_notes": lead.photo_notes})
    if request.method == "POST":
        if request.POST.get("action") == "notes":
            notes_form = PhotoNotesForm(request.POST)
            if notes_form.is_valid():
                lead.photo_notes = notes_form.cleaned_data["photo_notes"]
                lead.save(update_fields=["photo_notes", "updated_at"])
                return redirect("arborist:lead_detail", lead_id=lead.pk)
        else:
            photo_form = LeadPhotoForm(request.POST, request.FILES)
            if photo_form.is_valid():
                token = photo_form.cleaned_data["upload_id"]
                if str(token) not in request.session.get(UPLOAD_KEY, []):
                    photo_form.add_error(
                        None, _("This form has expired. Reload the page and try again.")
                    )
                else:
                    saved_name = None
                    storage = LeadPhoto._meta.get_field("image").storage
                    try:
                        with transaction.atomic():
                            locked_lead = ArboristLead.objects.select_for_update().get(
                                pk=lead.pk
                            )
                            if LeadPhoto.objects.filter(
                                lead=lead, upload_id=token
                            ).exists():
                                return redirect("arborist:lead_detail", lead_id=lead.pk)
                            if locked_lead.photos.count() >= 12:
                                photo_form.add_error(
                                    None,
                                    _(
                                        "You can add up to 12 photos. Please contact us if more are needed."
                                    ),
                                )
                            else:
                                photo = LeadPhoto(
                                    lead=lead,
                                    upload_id=token,
                                    category=photo_form.cleaned_data["category"],
                                    tree_label=photo_form.cleaned_data["tree_label"],
                                )
                                photo.image.save(
                                    "photo.jpg",
                                    photo_form.cleaned_data["image"],
                                    save=False,
                                )
                                saved_name = photo.image.name
                                photo.save()
                                LeadEvent.objects.create(
                                    lead=lead, description="Customer added a photo"
                                )
                        if saved_name:
                            return redirect("arborist:lead_detail", lead_id=lead.pk)
                    except Exception:
                        if saved_name:
                            try:
                                storage.delete(saved_name)
                            except Exception:
                                logger.warning(
                                    "Could not clean up an orphaned Arborist upload"
                                )
                        logger.warning(
                            "Arborist photo upload failed for lead %s", lead.pk
                        )
                        photo_form.add_error(
                            None,
                            _(
                                "Your enquiry is saved, but the photo could not be uploaded. Please try again later."
                            ),
                        )
    return render(
        request,
        "arborist/lead_detail.html",
        {
            "lead": lead,
            "photo_form": photo_form,
            "notes_form": notes_form,
            "categories": LeadPhoto.Category.choices,
            "page_title": _("Enquiry received — add photos"),
        },
    )


@never_cache
@require_GET
def lead_photo(request, photo_id):
    photo = get_object_or_404(LeadPhoto.objects.select_related("lead"), pk=photo_id)
    staff = (
        request.user.is_active
        and request.user.is_staff
        and request.user.has_perm("arborist.view_arboristlead")
    )
    if not staff and str(photo.lead.submission_id) not in request.session.get(
        TOKEN_KEY, []
    ):
        raise Http404
    try:
        file = photo.image.open("rb")
    except Exception:
        raise Http404
    response = FileResponse(file, content_type="image/jpeg", filename="tree-photo.jpg")
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
