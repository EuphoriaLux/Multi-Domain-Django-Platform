"""
Forms for the Arborist application (arborist.lu).
"""

from datetime import timedelta
from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import ArboristBooking
from .services.zones import clean_postal_code as normalize_postal_code
from .services.zones import is_valid_postal_code

# A Rush Order's preferred date may be at most this many days after today.
RUSH_WINDOW_DAYS = 2


class LuxembourgPostalCodeMixin:
    """Normalize ``postal_code`` and reject anything but exactly four digits."""

    def clean_postal_code(self):
        code = normalize_postal_code(self.cleaned_data.get("postal_code", ""))
        if not is_valid_postal_code(code):
            raise forms.ValidationError(
                _(
                    "Please enter a valid 4-digit Luxembourg postal code (e.g. 6211, 6110)."
                )
            )
        return code


class ContactForm(forms.Form):
    """Contact form for arborist.lu inquiries."""

    name = forms.CharField(
        label=_("Name"),
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent",
                "placeholder": _("Your name"),
            }
        ),
    )

    email = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(
            attrs={
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent",
                "placeholder": _("your.email@example.com"),
            }
        ),
    )

    phone = forms.CharField(
        label=_("Phone"),
        max_length=30,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent",
                "placeholder": _("+352 ..."),
            }
        ),
    )

    SERVICE_CHOICES = [
        ("", _("Please select")),
        ("obstbaumpflege", _("Fruit Tree Care")),
        ("baumpflege", _("Tree Care")),
        ("baumkontrolle", _("Tree Inspection")),
        ("oekologie", _("Ecological Measures")),
        ("beratung", _("General Consultation")),
    ]

    service = forms.ChoiceField(
        label=_("Interested in"),
        choices=SERVICE_CHOICES,
        required=False,
        widget=forms.Select(
            attrs={
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent bg-white",
            }
        ),
    )

    message = forms.CharField(
        label=_("Your Message"),
        widget=forms.Textarea(
            attrs={
                "class": "w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent resize-y",
                "rows": 5,
                "placeholder": _("Briefly describe your request..."),
            }
        ),
    )


class BookingForm(LuxembourgPostalCodeMixin, forms.ModelForm):
    """
    Form for booking on-site consultations and tree care services.
    Includes distance-based prepayment calculation and rush scheduling.
    """

    class Meta:
        model = ArboristBooking
        fields = [
            "name",
            "email",
            "phone",
            "street_address",
            "postal_code",
            "city_or_commune",
            "service_type",
            "number_of_trees",
            "preferred_date",
            "preferred_time_slot",
            "is_rush",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent",
                    "placeholder": _("Jean Dupont / Tom Weber"),
                    "required": True,
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent",
                    "placeholder": _("name@example.lu"),
                    "required": True,
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent",
                    "placeholder": _("+352 621 123 456"),
                    "required": True,
                }
            ),
            "street_address": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent",
                    "placeholder": _("12, Rue Principale"),
                    "required": True,
                }
            ),
            "postal_code": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent font-mono",
                    "placeholder": _("6211"),
                    "id": "id_postal_code",
                    "required": True,
                }
            ),
            "city_or_commune": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent",
                    "placeholder": _("Altrier / Junglinster"),
                    "id": "id_city_or_commune",
                    "required": True,
                }
            ),
            "service_type": forms.Select(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent bg-white",
                    "id": "id_service_type",
                }
            ),
            "number_of_trees": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent",
                    "placeholder": _("e.g. 1 Apple tree, 3 large Oaks"),
                }
            ),
            "preferred_date": forms.DateInput(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent",
                    "type": "date",
                }
            ),
            "preferred_time_slot": forms.Select(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent bg-white",
                }
            ),
            "is_rush": forms.CheckboxInput(
                attrs={
                    "class": "h-5 w-5 text-red-600 focus:ring-red-500 border-gray-300 rounded cursor-pointer",
                    "id": "id_is_rush",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-600 focus:border-transparent resize-y",
                    "rows": 4,
                    "placeholder": _(
                        "Describe the situation: tree health, height, obstacles (fence, power line), accessibility..."
                    ),
                }
            ),
        }

    # Honeypot: booking.html renders it hidden and aria-hidden, so people never
    # fill it in; form-spamming bots that fill every input get rejected.
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("honeypot")
        return ""

    def clean_phone(self):
        val = self.cleaned_data.get("phone", "").strip()
        if len(val) < 6:
            raise forms.ValidationError(_("Please enter a valid telephone number."))
        return val

    def clean_preferred_date(self):
        val = self.cleaned_data.get("preferred_date")
        if val and val < timezone.localdate():
            raise forms.ValidationError(_("Preferred date cannot be in the past."))
        return val

    def clean(self):
        cleaned = super().clean()
        preferred = cleaned.get("preferred_date")
        # The rush surcharge buys a visit within 24-48 hours, so a rush booking
        # may not ask for a later date (no date at all means "as soon as possible").
        if cleaned.get("is_rush") and preferred:
            latest = timezone.localdate() + timedelta(days=RUSH_WINDOW_DAYS)
            if preferred > latest:
                self.add_error(
                    "preferred_date",
                    _(
                        "A Rush Order is scheduled within 24-48 hours: please choose a date within the next two days, or untick Rush Order."
                    ),
                )
        return cleaned


class ArboristBookingAdminForm(LuxembourgPostalCodeMixin, forms.ModelForm):
    """Admin form with the public form's postal-code rule, so staff-entered
    bookings are priced from a real postcode."""

    class Meta:
        model = ArboristBooking
        fields = "__all__"
