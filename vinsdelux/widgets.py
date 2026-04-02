from django import forms
from django.urls import reverse
from django.utils.html import format_html

class AddAdoptionPlanWidget(forms.Widget):
    def render(self, name, value, attrs=None, renderer=None):
        add_url = reverse('admin:vinsdelux_vdladoptionplan_add')
        return format_html('<a href="{}" target="_blank">Add Adoption Plan</a>', add_url)
