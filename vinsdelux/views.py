from django.shortcuts import render
from .models import VdlProduct, VdlCategory

def home(request):
    featured_products = VdlProduct.objects.filter(is_featured=True)[:3]
    categories = VdlCategory.objects.filter(is_active=True)
    context = {
        'featured_products': featured_products,
        'categories': categories,
    }
    return render(request, 'vinsdelux/index.html', context)
