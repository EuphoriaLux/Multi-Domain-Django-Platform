# entreprinder/views.py

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .forms import EntrepreneurProfileForm
from .models import EntrepreneurProfile
from django.conf import settings
from matching.models import Like
import logging
import jwt
import datetime
from django.http import JsonResponse

from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.decorators import authentication_classes

from allauth.socialaccount.models import SocialAccount, SocialToken


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
        # No file uploads anymore => remove request.FILES
        form = EntrepreneurProfileForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            # Remove references to request.FILES or profile_picture
            profile.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('entreprinder:profile')
        else:
            messages.error(request, 'Error updating profile. Please check the form.')
    else:
        form = EntrepreneurProfileForm(instance=profile)

    return render(request, 'profile.html', {'form': form, 'profile': profile})



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

@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def protected_api(request):
    user = request.user
    return JsonResponse({'message': f'Hello, {user.email}!'}, status=200)



def login_complete(request):
    # After user logs in with LinkedIn
    from allauth.socialaccount.models import SocialAccount

    try:
        socialaccount = SocialAccount.objects.get(user=request.user, provider='openid_connect_linkedin')
        extra_data = socialaccount.extra_data  # e.g. {"picture": "https://..." }
    except SocialAccount.DoesNotExist:
        extra_data = {}

    linkedin_photo_url = extra_data.get("picture", "")

    if request.user.is_authenticated:
        from entreprinder.models import EntrepreneurProfile
        profile, _ = EntrepreneurProfile.objects.get_or_create(user=request.user)
        
        # Update the remote URL
        profile.linkedin_photo_url = linkedin_photo_url
        profile.save()

    # Then render or redirect as usual
    return render(request, 'login_complete.html')

