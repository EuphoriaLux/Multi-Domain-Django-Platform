# vinsdelux/views.py
"""VinsDelux concept-explainer views."""

from django.shortcuts import render


def home(request):
    """Concept-explainer homepage for the VinsDelux wine adoption idea."""
    return render(request, 'vinsdelux/index.html')
