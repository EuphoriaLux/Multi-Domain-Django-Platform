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

    context = {
        'homepage_content': homepage_content, # Add homepage content to context
        'featured_producer_products': featured_producer_products,
        'categories': categories,
    }
    return render(request, 'vinsdelux/index.html', context)
