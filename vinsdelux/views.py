from django.shortcuts import render
from django.utils.text import slugify # Import slugify
from .models import VdlProduct, VdlCategory, VdlProducer, HomepageContent

def home(request):
    homepage_content = HomepageContent.objects.first() # Get the single homepage content object

    featured_producers = VdlProducer.objects.filter(is_featured_on_homepage=True)
    featured_producer_products = []
    for producer in featured_producers:
        # Find one featured product for this producer
        featured_product = VdlProduct.objects.select_related('category').filter(producer=producer, is_featured=True).first()
        if featured_product:
            # Format category name for CSS class
            formatted_category_name = None
            if featured_product.category:
                formatted_category_name = slugify(featured_product.category.name) # Use slugify

            featured_producer_products.append({
                'producer': producer,
                'product': featured_product,
                'formatted_category_name': formatted_category_name # Add formatted name here
            })

    # Limit to 4 producer/product pairs for the homepage display
    featured_producer_products = featured_producer_products[:4]

    categories = VdlCategory.objects.filter(is_active=True) # Keep categories in context if needed elsewhere

    client_journey_steps = [
        {
            'id': 'step-1',
            'step': '01',
            'title': 'Sélection de la Parcelle',
            'description': 'Choisissez la parcelle de vigne qui correspond à vos préférences et à votre vision. Chaque parcelle offre un terroir unique et une histoire à raconter.',
            'icon_class': 'fa-seedling' # Font Awesome icon class
        },
        {
            'id': 'step-2',
            'step': '02',
            'title': 'Personnalisation de Votre Vin',
            'description': 'Collaborez avec nos vignerons experts pour personnaliser chaque aspect de votre vin, de la variété de raisin aux techniques de vinification.',
            'icon_class': 'fa-wine-bottle' # Font Awesome icon class
        },
        {
            'id': 'step-3',
            'step': '03',
            'title': 'Suivi de la Production',
            'description': 'Recevez des mises à jour régulières sur la croissance de votre vigne et le processus de vinification, avec des photos et des rapports détaillés.',
            'icon_class': 'fa-chart-line' # Font Awesome icon class
        },
        {
            'id': 'step-4',
            'step': '04',
            'title': 'Réception et Dégustation',
            'description': 'Vos bouteilles personnalisées sont livrées directement chez vous, prêtes à être dégustées et partagées avec vos proches.',
            'icon_class': 'fa-glass-cheers' # Font Awesome icon class
        },
        {
            'id': 'step-5',
            'step': '05',
            'title': 'Création de Votre Héritage',
            'description': 'Votre vin devient un héritage familial, une expression unique de votre passion pour le vin et un investissement dans l\'avenir.',
            'icon_class': 'fa-scroll' # Font Awesome icon class
        },
    ]

    context = {
        'homepage_content': homepage_content, # Add homepage content to context
        'featured_producer_products': featured_producer_products,
        'categories': categories,
        'client_journey_steps': client_journey_steps, # Add client journey steps to context
    }
    return render(request, 'vinsdelux/index.html', context)
