from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .forms import EntrepreneurProfileForm
from .models import EntrepreneurProfile
from matching.models import Like
import logging

logger = logging.getLogger(__name__)

def home(request):
    return render(request, 'landing_page.html')

def about_page(request):
    return render(request, 'about.html')

def contact_page(request):
    return render(request, 'contact.html')

@login_required
def profile(request):
    profile, created = EntrepreneurProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        form = EntrepreneurProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            if 'profile_picture' in request.FILES:
                profile.profile_picture = request.FILES['profile_picture']
            profile.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('entreprinder:profile')
        else:
            messages.error(request, 'Error updating profile. Please check the form.')
    else:
        form = EntrepreneurProfileForm(instance=profile)

    return render(request, 'profile.html', {'form': form})

@login_required
def entrepreneur_list(request):
    try:
        current_user_profile, created = EntrepreneurProfile.objects.get_or_create(user=request.user)
        if created:
            return redirect('entreprinder:profile')
        
        liked_profiles = Like.objects.filter(liker=current_user_profile).values_list('liked_id', flat=True)
        profiles = EntrepreneurProfile.objects.exclude(user=request.user).exclude(id__in=liked_profiles)
        
        return render(request, 'entrepreneur_list.html', {'profiles': profiles})
    except Exception as e:
        logger.exception("Error loading entrepreneur list")
        return render(request, 'error.html', {'error_message': f"An error occurred while loading the entrepreneur list. Please try again later. Error details: {e}"})
