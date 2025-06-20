# in vinsdelux/views.py

from django.shortcuts import render
from django.utils.text import slugify
# Import only the models we actually need here
from .models import VdlCoffret, HomepageContent, VdlCategory

def home(request):
    """
    Displays the homepage, featuring a selection of the best Coffrets
    and their associated Adoption Plans.
    """
    homepage_content = HomepageContent.objects.first()
    
    # --- NEW, SIMPLIFIED & OPTIMIZED QUERY ---
    # The new logic is much cleaner. We fetch the featured Coffrets and tell Django
    # to also grab all related data (producer, category, and especially adoption plans)
    # in the most efficient way possible.
    
    featured_coffrets = VdlCoffret.objects.filter(
        is_featured=True, 
        is_available=True
    ).select_related(
        'producer', 'category'  # Use JOINs for one-to-one relationships
    ).prefetch_related(
        'adoption_plans'  # Use a separate, efficient query for many-to-one
    )[:4] # Limit to the first 4 featured coffrets for the homepage

    # The client journey steps are static and fine as they are.
    client_journey_steps = [
        {
            'id': 'step-1',
            'step': '01',
            'title': 'Plot Selection',
            'description': 'Choose the vineyard plot that matches your preferences and vision. Each plot offers a unique terroir and a story to tell.',
            'icon_class': 'fa-seedling'
        },
        {
            'id': 'step-2',
            'step': '02',
            'title': 'Personalize Your Wine',
            'description': 'Collaborate with our expert winemakers to personalize every aspect of your wine, from the grape variety to the winemaking techniques.',
            'icon_class': 'fa-wine-bottle'
        },
        {
            'id': 'step-3',
            'step': '03',
            'title': 'Follow the Production',
            'description': 'Receive regular updates on the growth of your vine and the winemaking process, complete with detailed photos and reports.',
            'icon_class': 'fa-chart-line'
        },
        {
            'id': 'step-4',
            'step': '04',
            'title': 'Receive and Taste',
            'description': 'Your personalized bottles are delivered directly to your home, ready to be tasted and shared with loved ones.',
            'icon_class': 'fa-glass-cheers'
        },
        {
            'id': 'step-5',
            'step': '05',
            'title': 'Create Your Legacy',
            'description': 'Your wine becomes a family legacy, a unique expression of your passion for wine, and an investment in the future.',
            'icon_class': 'fa-scroll'
        },
    ]

    context = {
        'homepage_content': homepage_content,
        # We pass this clean, powerful list of coffret objects to the template.
        # The template will handle the display logic.
        'featured_coffrets': featured_coffrets,
        'client_journey_steps': client_journey_steps,
    }
    return render(request, 'vinsdelux/index.html', context)