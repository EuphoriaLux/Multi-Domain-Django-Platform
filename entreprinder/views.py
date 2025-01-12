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
                print(f"Uploaded profile picture to: {profile.profile_picture.path}")
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

@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def protected_api(request):
    user = request.user
    return JsonResponse({'message': f'Hello, {user.email}!'}, status=200)


@login_required
def login_complete(request):
    # Generate JWT tokens
    refresh = RefreshToken.for_user(request.user)
    access_token = str(refresh.access_token)
    refresh_token = str(refresh)

    # Render the callback template with the tokens
    return render(request, 'login_complete.html', {
        'access_token': access_token,
        'refresh_token': refresh_token,
    })

