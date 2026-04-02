from django import forms
from .models import VdlCoffret
from .widgets import AddAdoptionPlanWidget


class VdlCoffretAdminForm(forms.ModelForm):
    class Meta:
        model = VdlCoffret
        fields = "__all__"
        widgets = {
            "adoption_plan": AddAdoptionPlanWidget,
        }
