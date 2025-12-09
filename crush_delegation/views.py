from django.shortcuts import render


def home(request):
    """Home page for Crush Delegation subdomain."""
    return render(request, 'crush_delegation/home.html')
