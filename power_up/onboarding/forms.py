from django import forms

from power_up.crm.forms import tw
from power_up.crm.models import AuthorizedContact, ServiceExpert, Tenant
from .models import OnboardingSession
from .utils.meeting_slots import format_slot, generate_meeting_slots


class TenantOnboardingForm(forms.ModelForm):
    """Quick-edit form for tenant fields relevant to onboarding (GDAP, Azure)."""

    class Meta:
        model = Tenant
        fields = [
            "tenant_id_azure",
            "primary_domain",
            "has_azure",
            "gdap_link",
        ]
        widgets = {
            "tenant_id_azure": forms.TextInput(
                attrs={
                    "class": tw["text"],
                    "placeholder": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                }
            ),
            "primary_domain": forms.TextInput(
                attrs={
                    "class": tw["text"],
                    "placeholder": "contoso.onmicrosoft.com",
                }
            ),
            "has_azure": forms.CheckboxInput(attrs={"class": tw["checkbox"]}),
            "gdap_link": forms.URLInput(
                attrs={
                    "class": tw["text"],
                    "placeholder": "https://admin.microsoft.com/...",
                }
            ),
        }


class OnboardingConfigForm(forms.ModelForm):
    meeting_slots = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        help_text="Select one or more meeting slots to propose.",
    )

    class Meta:
        model = OnboardingSession
        fields = [
            "language",
            "recipient",
            "tenants",
            "include_gdap",
            "include_rbac",
            "include_conditional_access",
            "additional_notes",
            "sender",
        ]
        widgets = {
            "language": forms.Select(attrs={"class": tw["select"]}),
            "recipient": forms.Select(attrs={"class": tw["select"]}),
            "tenants": forms.CheckboxSelectMultiple(),
            "include_gdap": forms.CheckboxInput(attrs={"class": tw["checkbox"]}),
            "include_rbac": forms.CheckboxInput(attrs={"class": tw["checkbox"]}),
            "include_conditional_access": forms.CheckboxInput(
                attrs={"class": tw["checkbox"]}
            ),
            "additional_notes": forms.Textarea(
                attrs={
                    "class": tw["textarea"],
                    "rows": 4,
                    "placeholder": "Any additional information for the customer...",
                }
            ),
            "sender": forms.Select(attrs={"class": tw["select"]}),
        }

    def __init__(self, *args, group=None, **kwargs):
        self._group = group
        super().__init__(*args, **kwargs)

        # --- Recipient: authorized contacts for this group, admin-role first ---
        if group:
            contacts = AuthorizedContact.objects.filter(
                group=group, is_active=True
            ).select_related("group")
            self.fields["recipient"].queryset = contacts

            # Pre-select the admin-role contact if creating a new session
            if not self.instance.recipient_id:
                admin_role = group.user_roles.filter(role="admin").first()
                if admin_role:
                    self.initial.setdefault("recipient", admin_role.contact_id)
                elif contacts.exists():
                    self.initial.setdefault("recipient", contacts.first().pk)
        else:
            self.fields["recipient"].queryset = AuthorizedContact.objects.filter(
                is_active=True
            )

        # --- Tenants: from the group's entities, shown as checkboxes ---
        if group:
            group_tenants = Tenant.objects.filter(
                entity__group=group, is_active=True
            ).select_related("entity")
            self.fields["tenants"].queryset = group_tenants

            # Custom labels showing Tenant ID + GDAP/Azure status
            self.fields["tenants"].label_from_instance = self._tenant_label

            # Pre-select all tenants on new session
            if not self.instance.pk:
                self.initial.setdefault(
                    "tenants", list(group_tenants.values_list("pk", flat=True))
                )
        else:
            self.fields["tenants"].queryset = Tenant.objects.filter(is_active=True)

        # --- Sender: active service experts ---
        self.fields["sender"].queryset = ServiceExpert.objects.filter(is_active=True)
        self.fields["sender"].required = False

        # Pre-select the group's primary service expert
        if group and not self.instance.sender_id:
            primary = group.service_expert_assignments.filter(is_primary=True).first()
            if primary:
                self.initial.setdefault("sender", primary.service_expert_id)

        # --- Meeting slot choices ---
        slots = generate_meeting_slots()
        self.fields["meeting_slots"].choices = [
            (slot.isoformat(), f"{slot.strftime('%A, %B %d')} — {format_slot(slot)}")
            for slot in slots
        ]

        # Pre-select existing slots on edit
        if self.instance.pk and self.instance.meeting_slots:
            self.initial["meeting_slots"] = self.instance.meeting_slots

    @staticmethod
    def _tenant_label(tenant):
        """Rich label for tenant checkboxes showing ID and status indicators."""
        label = f"{tenant.company_name}"
        if tenant.tenant_id_azure:
            short_id = tenant.tenant_id_azure[:8] + "..."
            label += f" ({short_id})"
        elif tenant.primary_domain:
            label += f" ({tenant.primary_domain})"
        tags = []
        if tenant.has_azure:
            tags.append("Azure")
        if tenant.gdap_link:
            tags.append("GDAP")
        else:
            tags.append("No GDAP link")
        if tags:
            label += " — " + ", ".join(tags)
        return label

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.meeting_slots = self.cleaned_data.get("meeting_slots", [])

        # Snapshot recipient details
        contact = instance.recipient
        if contact:
            instance.contact_name = contact.display_name
            instance.contact_email = contact.email
        else:
            instance.contact_name = ""
            instance.contact_email = ""

        # Snapshot sender details from selected ServiceExpert
        expert = instance.sender
        if expert:
            instance.sender_name = expert.display_name
            instance.sender_email = expert.email
            instance.sender_phone = expert.phone_dedicated
            instance.sender_title = "Service Delivery Expert"
        else:
            instance.sender_name = ""
            instance.sender_email = ""
            instance.sender_phone = ""
            instance.sender_title = ""

        if commit:
            instance.save()
            # M2M (tenants) needs save_m2m after save
            self.save_m2m()
        return instance
