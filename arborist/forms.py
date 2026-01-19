"""
Forms for the Arborist application.
"""

from django import forms
from django.utils.translation import gettext_lazy as _


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
