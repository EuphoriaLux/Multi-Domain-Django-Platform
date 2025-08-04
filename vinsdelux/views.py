# in vinsdelux/views.py

from django.shortcuts import render, get_object_or_404
from django.utils.text import slugify
from django.core.paginator import Paginator
from django.db.models import Q
# Import only the models we actually need here
from .models import VdlCoffret, VdlAdoptionPlan, HomepageContent, VdlCategory, VdlProducer

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


def coffret_list(request):
    """
    Display paginated list of coffrets with filtering options.
    """
    coffrets = VdlCoffret.objects.filter(is_available=True).select_related('producer', 'category').prefetch_related('images')
    
    # Search functionality
    search_query = request.GET.get('search')
    if search_query:
        coffrets = coffrets.filter(
            Q(name__icontains=search_query) |
            Q(short_description__icontains=search_query) |
            Q(producer__name__icontains=search_query)
        )
    
    # Category filter
    category_filter = request.GET.get('category')
    if category_filter:
        coffrets = coffrets.filter(category__slug=category_filter)
    
    # Producer filter
    producer_filter = request.GET.get('producer')
    if producer_filter:
        coffrets = coffrets.filter(producer__slug=producer_filter)
    
    # Pagination
    paginator = Paginator(coffrets, 12)  # Show 12 coffrets per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get categories and producers for filters
    categories = VdlCategory.objects.filter(is_active=True)
    producers = VdlProducer.objects.all()
    
    context = {
        'page_obj': page_obj,
        'categories': categories,
        'producers': producers,
        'search_query': search_query,
        'current_category': category_filter,
        'current_producer': producer_filter,
    }
    return render(request, 'vinsdelux/coffret_list.html', context)


def coffret_detail(request, slug):
    """
    Display detailed view of a specific coffret with its adoption plans.
    """
    coffret = get_object_or_404(
        VdlCoffret.objects.select_related('producer', 'category')
                          .prefetch_related('images', 'adoption_plans__images'),
        slug=slug,
        is_available=True
    )
    
    adoption_plans = coffret.adoption_plans.filter(is_available=True)
    
    # Get related coffrets from same producer
    related_coffrets = VdlCoffret.objects.filter(
        producer=coffret.producer,
        is_available=True
    ).exclude(id=coffret.id)[:4]
    
    context = {
        'coffret': coffret,
        'adoption_plans': adoption_plans,
        'related_coffrets': related_coffrets,
    }
    return render(request, 'vinsdelux/coffret_detail.html', context)


def adoption_plan_detail(request, slug):
    """
    Display detailed view of a specific adoption plan.
    """
    adoption_plan = get_object_or_404(
        VdlAdoptionPlan.objects.select_related('associated_coffret', 'producer', 'category')
                               .prefetch_related('images'),
        slug=slug,
        is_available=True
    )
    
    context = {
        'adoption_plan': adoption_plan,
    }
    return render(request, 'vinsdelux/adoption_plan_detail.html', context)


def producer_list(request):
    """
    Display list of all producers.
    """
    producers = VdlProducer.objects.prefetch_related('vdlcoffret_set').order_by('name')
    
    context = {
        'producers': producers,
    }
    return render(request, 'vinsdelux/producer_list.html', context)


def producer_detail(request, slug):
    """
    Display detailed view of a producer with their products.
    """
    producer = get_object_or_404(VdlProducer, slug=slug)
    coffrets = producer.vdlcoffret_set.filter(is_available=True).prefetch_related('images')
    adoption_plans = producer.vdladoptionplan_set.filter(is_available=True).prefetch_related('images')
    
    context = {
        'producer': producer,
        'coffrets': coffrets,
        'adoption_plans': adoption_plans,
    }
    return render(request, 'vinsdelux/producer_detail.html', context)
