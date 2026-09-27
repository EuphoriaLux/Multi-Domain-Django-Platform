"""Social-signup completion form shared by every domain.

On crush.lu the completion page asks for Terms/Privacy consent. The checkbox
has to be validated here, on the server, before allauth creates the user and
social account: an HTML ``required`` attribute only binds a cooperating
browser. Other domains keep allauth's form unchanged.
"""

from allauth.core import context
from allauth.socialaccount.forms import SignupForm
from django import forms
from django.utils.translation import gettext_lazy as _


class MultiDomainSocialSignupForm(SignupForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from azureproject.adapters import _is_crush_domain

        request = context.request
        if request is not None and _is_crush_domain(request):
            # Same field and message as CrushSignupForm, so the stored consent
            # and the existing translations line up with the password signup.
            self.fields["crushlu_consent"] = forms.BooleanField(
                required=True,
                label=_("I agree to the Terms of Service and Privacy Policy"),
                error_messages={
                    "required": _("You must consent to use Crush.lu services")
                },
            )
