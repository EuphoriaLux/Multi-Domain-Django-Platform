from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

# Create your views here.
def index(request):
    """
    Renders the main page for the Vibe Coding section.
    """
    context = {
        'page_title': _('Vibe Coding Projects')
    }
    return render(request, 'vibe_coding/index.html', context)
