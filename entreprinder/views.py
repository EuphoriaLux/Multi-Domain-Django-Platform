# entreprinder/views.py

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .forms import EntrepreneurProfileForm
from .models import EntrepreneurProfile, Like
from django.conf import settings
import logging
from django.http import JsonResponse

from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.decorators import authentication_classes

from allauth.socialaccount.models import SocialAccount
from allauth.account.models import EmailAddress # Import EmailAddress

logger = logging.getLogger(__name__)

def home(request):
    context = {
        'redirect_to': request.get_full_path()  # Add current path for language switcher
    }
    if request.user.is_authenticated:
        try:
            primary_email = EmailAddress.objects.get(user=request.user, primary=True)
            if not primary_email.verified:
                context['show_verification_alert'] = True
                context['unverified_email'] = primary_email.email
        except EmailAddress.DoesNotExist:
            # Handle case where primary email might not exist yet, though unlikely
            # with ACCOUNT_EMAIL_REQUIRED=True
            pass
    return render(request, 'landing_page.html', context)

def about_page(request):
    return render(request, 'about.html')

def contact_page(request):
    return render(request, 'contact.html')

@login_required
def profile(request):
    profile, created = EntrepreneurProfile.objects.get_or_create(user=request.user)
    context = {'form': None, 'profile': profile} # Initialize context

    # Add verification check logic
    try:
        primary_email = EmailAddress.objects.get(user=request.user, primary=True)
        if not primary_email.verified:
            context['show_verification_alert'] = True
            context['unverified_email'] = primary_email.email
    except EmailAddress.DoesNotExist:
        pass # Ignore if no primary email found

    if request.method == 'POST':
        form = EntrepreneurProfileForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('entreprinder:profile')
        else:
            messages.error(request, 'Error updating profile. Please check the form.')
    else:
        form = EntrepreneurProfileForm(instance=profile)

    context['form'] = form # Add form to context
    return render(request, 'entreprinder/profile.html', context)

@login_required
def entrepreneur_list(request):
    context = {} # Initialize context
    # Add verification check logic
    try:
        primary_email = EmailAddress.objects.get(user=request.user, primary=True)
        if not primary_email.verified:
            context['show_verification_alert'] = True
            context['unverified_email'] = primary_email.email
    except EmailAddress.DoesNotExist:
        pass # Ignore if no primary email found

    try:
        current_user_profile, created = EntrepreneurProfile.objects.get_or_create(user=request.user)
        if created:
            return redirect('entreprinder:profile')
        
        liked_profiles = Like.objects.filter(liker=current_user_profile).values_list('liked_id', flat=True)
        profiles = EntrepreneurProfile.objects.exclude(user=request.user).exclude(id__in=liked_profiles)
        
        context['profiles'] = profiles # Add profiles to context
        return render(request, 'entreprinder/entrepreneur_list.html', context)
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
    """Handle LinkedIn login completion and extract profile photo URL"""
    logger.info("Login complete view called")
    
    # Log request information
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request path: {request.path}")
    logger.info(f"Is user authenticated: {request.user.is_authenticated}")

    if request.user.is_authenticated:
        logger.info(f"Authenticated user: {request.user.username} (ID: {request.user.id})")
        
        # Check for existing EntrepreneurProfile
        existing_profile = EntrepreneurProfile.objects.filter(user=request.user).first()
        if existing_profile:
            logger.info(f"Existing profile found with linkedin_photo_url: {existing_profile.linkedin_photo_url}")
        else:
            logger.info("No existing EntrepreneurProfile found for this user")
        
        try:
            # Get all social accounts for debugging
            all_accounts = SocialAccount.objects.filter(user=request.user)
            logger.info(f"All social accounts for user: {[acc.provider for acc in all_accounts]}")
            
            # Focus only on OpenID Connect provider
            socialaccount = SocialAccount.objects.filter(
                user=request.user, 
                provider='openid_connect_linkedin'
            ).first()
            
            if socialaccount:
                logger.info(f"Found social account: {socialaccount.provider} (ID: {socialaccount.id})")
                extra_data = socialaccount.extra_data
                
                # Log the entire extra_data for debugging
                logger.info(f"Extra data from LinkedIn: {extra_data}")
                
                # OpenID Connect provides the picture URL directly
                linkedin_photo_url = extra_data.get('picture', '')
                logger.info(f"Extracted LinkedIn photo URL: {linkedin_photo_url}")
                
                # Update the profile with LinkedIn photo URL
                if linkedin_photo_url:
                    profile, created = EntrepreneurProfile.objects.get_or_create(user=request.user)
                    profile.linkedin_photo_url = linkedin_photo_url
                    profile.save()
                    logger.info(f"Updated profile linkedin_photo_url to: {profile.linkedin_photo_url}")
                    logger.info(f"Profile created: {created}")
                else:
                    logger.warning("No LinkedIn photo URL found in the data")
                
            else:
                logger.warning(f"No OpenID Connect LinkedIn account found for user {request.user.username}")
                
        except Exception as e:
            logger.error(f"Error retrieving LinkedIn account: {str(e)}", exc_info=True)
    else:
        logger.warning("User not authenticated in login_complete view")

    # Generate JWT tokens for the extension
    if request.user.is_authenticated:
        refresh = RefreshToken.for_user(request.user)
        context = {
            'access_token': str(refresh.access_token),
            'refresh_token': str(refresh),
        }
    else:
        context = {
            'access_token': '',
            'refresh_token': '',
        }
    
    return render(request, 'account/login_complete.html', context)
