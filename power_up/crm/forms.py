from django import forms

from .models import AccountManager, AuthorizedContact, ServiceExpert, Tenant, Ticket

tw = {
    "text": "block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-powerup-orange focus:ring-1 focus:ring-powerup-orange",
    "select": "block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-powerup-orange focus:ring-1 focus:ring-powerup-orange",
    "textarea": "block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-powerup-orange focus:ring-1 focus:ring-powerup-orange",
    "checkbox": "rounded border-gray-300 text-powerup-orange focus:ring-powerup-orange",
}


class TicketCreateForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = [
            "tenant",
            "requester",
            "title",
            "description",
            "severity",
            "priority",
            "product_category",
            "assigned_to",
            "assigned_expert",
        ]
        widgets = {
            "tenant": forms.Select(attrs={"class": tw["select"]}),
            "requester": forms.Select(attrs={"class": tw["select"]}),
            "title": forms.TextInput(
                attrs={"class": tw["text"], "placeholder": "Brief summary of the issue"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": tw["textarea"],
                    "rows": 5,
                    "placeholder": "Detailed description...",
                }
            ),
            "severity": forms.Select(attrs={"class": tw["select"]}),
            "priority": forms.Select(attrs={"class": tw["select"]}),
            "product_category": forms.Select(attrs={"class": tw["select"]}),
            "assigned_to": forms.Select(attrs={"class": tw["select"]}),
            "assigned_expert": forms.Select(attrs={"class": tw["select"]}),
        }

    def __init__(self, *args, **kwargs):
        group = kwargs.pop("group", None)
        super().__init__(*args, **kwargs)

        if group:
            self.fields["tenant"].queryset = Tenant.objects.filter(
                entity__group=group, is_active=True
            ).select_related("entity__group")
            self.fields["requester"].queryset = AuthorizedContact.objects.filter(
                group=group, is_active=True
            ).select_related("group")
        else:
            self.fields["tenant"].queryset = Tenant.objects.filter(
                is_active=True
            ).select_related("entity__group")
            self.fields["requester"].queryset = AuthorizedContact.objects.filter(
                is_active=True
            ).select_related("group")

        self.fields["assigned_to"].queryset = AccountManager.objects.filter(
            is_active=True
        )
        self.fields["assigned_to"].required = False
        self.fields["assigned_expert"].queryset = ServiceExpert.objects.filter(
            is_active=True
        )
        self.fields["assigned_expert"].required = False


class TicketUpdateForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = [
            "status",
            "priority",
            "assigned_to",
            "assigned_expert",
            "microsoft_case_id",
            "is_billable",
            "internal_notes",
        ]
        widgets = {
            "status": forms.Select(attrs={"class": tw["select"]}),
            "priority": forms.Select(attrs={"class": tw["select"]}),
            "assigned_to": forms.Select(attrs={"class": tw["select"]}),
            "assigned_expert": forms.Select(attrs={"class": tw["select"]}),
            "microsoft_case_id": forms.TextInput(
                attrs={"class": tw["text"], "placeholder": "e.g. MS-2026-001234"}
            ),
            "is_billable": forms.CheckboxInput(attrs={"class": tw["checkbox"]}),
            "internal_notes": forms.Textarea(
                attrs={"class": tw["textarea"], "rows": 3}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = AccountManager.objects.filter(
            is_active=True
        )
        self.fields["assigned_to"].required = False
        self.fields["assigned_expert"].queryset = ServiceExpert.objects.filter(
            is_active=True
        )
        self.fields["assigned_expert"].required = False


class TicketCommentForm(forms.Form):
    message = forms.CharField(
        widget=forms.Textarea(
            attrs={
                "class": tw["textarea"],
                "rows": 3,
                "placeholder": "Add a comment...",
            }
        )
    )
    is_internal = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": tw["checkbox"]}),
        label="Internal only (staff-visible)",
    )
