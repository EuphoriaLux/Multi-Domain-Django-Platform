# in vinsdelux/views.py

from django.shortcuts import render
from django.utils.text import slugify
# Import only the models we actually need here
from .models import VdlCoffret, HomepageContent, VdlCategory, VdlProducer

def home(request):
    """
    Displays the homepage, featuring a selection of featured Producers.
    """
    homepage_content = HomepageContent.objects.first()

    featured_producers = VdlProducer.objects.filter(
        is_featured_on_homepage=True
    ).prefetch_related(
        'vdlcoffret_set__adoption_plans'
    )[:4]

# In your views.py or context processor

    client_journey_steps = [
        {
            'step': '01',
            'title': 'Plot Selection',
            'description': 'Choose the vineyard plot that matches your preferences and vision. Each plot offers a unique terroir and a story to tell.',
            'image_url': 'images/journey/step_01.png'
        },
        {
            'step': '02',
            'title': 'Personalize Your Wine',
            'description': 'Collaborate with our expert winemakers to personalize every aspect of your wine, from the grape variety to the winemaking techniques.',
            'image_url': 'images/journey/step_02.png'
        },
        {
            'step': '03',
            'title': 'Follow the Production',
            'description': 'Receive regular updates on the growth of your vine and the winemaking process, complete with detailed photos and reports.',
            'image_url': 'images/journey/step_03.png'
        },
        {
            'step': '04',
            'title': 'Receive and Taste',
            'description': 'Your personalized bottles are delivered directly to your home, ready to be tasted and shared with loved ones.',
            'image_url': 'images/journey/step_04.png'
        },
        {
            'step': '05',
            'title': 'Create Your Legacy',
            'description': 'Your wine becomes a family legacy, a unique expression of your passion for wine, and an investment in the future.',
            'image_url': 'images/journey/step_05.png'
        }
    ]

    # Then pass this list to your template context

    context = {
        'homepage_content': homepage_content,
        'featured_producers': featured_producers,
        'client_journey_steps': client_journey_steps,
    }
    return render(request, 'vinsdelux/index.html', context)
