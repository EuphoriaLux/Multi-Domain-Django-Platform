from django.shortcuts import render, redirect
from django.utils import timezone
from .models import MeetupEvent


def home(request):
    """Landing page - redirects authenticated users to dashboard"""
    if request.user.is_authenticated:
        return redirect("crush_lu:dashboard")

    upcoming_events = MeetupEvent.objects.filter(
        is_published=True, is_cancelled=False, date_time__gte=timezone.now()
    )[:3]

    context = {
        "upcoming_events": upcoming_events,
    }
    return render(request, "crush_lu/home.html", context)


def about(request):
    """About page"""
    return render(request, "crush_lu/about.html")


def how_it_works(request):
    """How it works page"""
    return render(request, "crush_lu/how_it_works.html")


def privacy_policy(request):
    """Privacy policy page"""
    return render(request, "crush_lu/privacy_policy.html")


def terms_of_service(request):
    """Terms of service page"""
    return render(request, "crush_lu/terms_of_service.html")


def data_deletion_request(request):
    """Data deletion instructions page"""
    return render(request, "crush_lu/data_deletion.html")


def crush_coach(request):
    """Crush Coach recruitment landing page"""
    return render(request, "crush_lu/crush_coach.html")


