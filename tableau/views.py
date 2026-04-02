# tableau/views.py
"""
Views for Tableau AI Art e-commerce site.

Simple static views for the landing page and about page.
No authentication, no forms, no database queries.
"""
from django.shortcuts import render


def home(request):
    """
    Tableau landing page.

    Displays hero section, featured artwork, and call-to-action.
    """
    context = {
        "page_title": "Tableau - AI-Generated Art That Moves You",
        "page_description": "Discover unique AI-generated artwork that creates emotional impact. "
                           "Each piece is crafted to inspire and transform your space.",
    }
    return render(request, "tableau/home.html", context)


def about(request):
    """
    About Tableau page.

    Explains the concept of AI-generated art and the vision behind Tableau.
    """
    context = {
        "page_title": "About Tableau - The Future of Art",
        "page_description": "Learn about our mission to create AI-generated art that "
                           "resonates emotionally and transforms spaces.",
    }
    return render(request, "tableau/about.html", context)
