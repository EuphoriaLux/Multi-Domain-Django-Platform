from django import forms
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.urls import reverse
from django.utils.html import format_html
from .models import VdlCoffret
from .widgets import AddAdoptionPlanWidget

class VdlCoffretAdminForm(forms.ModelForm):
    class Meta:
        model = VdlCoffret
        fields = '__all__'
        widgets = {
            'adoption_plan': AddAdoptionPlanWidget,
        }
