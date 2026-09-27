from django import forms
from django.utils.translation import gettext_lazy as _


class DeletionEmailConfirmForm(forms.Form):
    """The "type your email to confirm" field on the account-deletion pages.

    Only renders the labelled input (through ``components/form_field.html``);
    the views still compare ``confirm_email`` with the account email. Pages
    with two deletion forms pass a distinct ``auto_id`` to each instance so
    the label ``for``/input ``id`` pairs stay unique while the field name stays
    ``confirm_email``.
    """

    confirm_email = forms.EmailField(
        label=_("Type your email address to confirm"),
        widget=forms.EmailInput(attrs={"class": "input-crush", "autocomplete": "off"}),
    )
