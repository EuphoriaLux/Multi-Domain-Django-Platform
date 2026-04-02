# power_up/finops/forms.py
"""
FinOps Hub Forms
"""

from django import forms
from .models import CostExport


class SubscriptionIDForm(forms.ModelForm):
    """Form to collect Azure subscription ID for incomplete exports"""

    class Meta:
        model = CostExport
        fields = ['subscription_id']
        widgets = {
            'subscription_id': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
                'pattern': '[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}',
            })
        }
        labels = {
            'subscription_id': 'Azure Subscription ID'
        }
        help_texts = {
            'subscription_id': 'Enter the Azure subscription GUID (format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)'
        }
