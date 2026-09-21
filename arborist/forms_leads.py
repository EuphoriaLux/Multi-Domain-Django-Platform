import warnings
from io import BytesIO

from django import forms
from django.core.files.base import ContentFile
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps, UnidentifiedImageError

from .forms import ContactForm
from .models import ArboristBooking, LeadPhoto
from .services.zones import clean_postal_code, is_valid_postal_code


class LeadForm(ContactForm):
    field_order = [
        "service",
        "postal_code",
        "message",
        "is_urgent",
        "name",
        "email",
        "phone",
        "submission_id",
        "website",
    ]
    submission_id = forms.UUIDField(widget=forms.HiddenInput)
    email = forms.EmailField(label=_("Email"), required=False)
    phone = forms.CharField(label=_("Phone"), max_length=50, required=False)
    postal_code = forms.CharField(label=_("Postal code"), max_length=10)
    service = forms.ChoiceField(
        label=_("Interested in"),
        choices=[("", _("Not sure yet"))] + ArboristBooking.SERVICE_CHOICES,
        required=False,
    )
    is_urgent = forms.BooleanField(label=_("Urgent situation"), required=False)
    message = forms.CharField(
        label=_("Project description"),
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )

    def clean_postal_code(self):
        code = clean_postal_code(self.cleaned_data["postal_code"])
        if not is_valid_postal_code(code):
            raise forms.ValidationError(
                _(
                    "Please enter a valid 4-digit Luxembourg postal code (e.g. 6211, 6110)."
                )
            )
        return code

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("honeypot")
        return ""

    def clean(self):
        data = super().clean()
        if not data.get("email") and not data.get("phone"):
            raise forms.ValidationError(
                _("Please provide an email address or telephone number.")
            )
        if data.get("phone") and len(data["phone"]) < 6:
            self.add_error("phone", _("Please enter a valid telephone number."))
        return data


class LeadPhotoForm(forms.Form):
    upload_id = forms.UUIDField(widget=forms.HiddenInput)
    tree_label = forms.CharField(
        label=_("Tree label"),
        max_length=50,
        initial="1",
        help_text=_("Use the same label for photos of the same tree."),
    )
    category = forms.ChoiceField(
        label=_("Photo category"), choices=LeadPhoto.Category.choices
    )
    image = forms.FileField(
        label=_("Photo"),
        help_text=_("JPEG, PNG or WebP, up to 8 MB."),
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )

    def clean_image(self):
        upload = self.cleaned_data["image"]
        if upload.size > 8 * 1024 * 1024:
            raise forms.ValidationError(_("Please choose a photo smaller than 8 MB."))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(upload) as source:
                    if (
                        source.format not in {"JPEG", "PNG", "WEBP"}
                        or source.width * source.height > 24_000_000
                    ):
                        raise ValueError("unsupported image")
                    source.load()
                    photo = ImageOps.exif_transpose(source).convert("RGB")
                    photo.thumbnail((2400, 2400))
                    # Rebuild pixels, rather than propagating EXIF/GPS or comments.
                    clean = Image.new("RGB", photo.size)
                    clean.paste(photo)
                    output = BytesIO()
                    clean.save(output, format="JPEG", quality=88)
            return ContentFile(output.getvalue(), name="photo.jpg")
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            raise forms.ValidationError(
                _(
                    "Please choose a valid JPEG, PNG or WebP photo (maximum 24 megapixels)."
                )
            )


class PhotoNotesForm(forms.Form):
    photo_notes = forms.CharField(
        label=_("Missing photos / access notes"),
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
