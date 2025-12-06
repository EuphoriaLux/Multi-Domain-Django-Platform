from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.contrib import messages  # Import messages framework
from allauth.account.models import EmailAddress # Import EmailAddress for checking verification
from entreprinder.models import EntrepreneurProfile
from .models import Like, Dislike, Match
from .forms import SwipeForm
import json

@login_required
def swipe_action(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        profile_id = data.get('profile_id')
        action = data.get('action')
        
        liked_profile = get_object_or_404(EntrepreneurProfile, id=profile_id)
        current_user = request.user.entrepreneurprofile
                
        match_found = False
        if action == 'like':
            like, created = Like.objects.get_or_create(liker=current_user, liked=liked_profile)
            
            # Check if it's a match
            if Like.objects.filter(liker=liked_profile, liked=current_user).exists():
                Match.objects.get_or_create(entrepreneur1=current_user, entrepreneur2=liked_profile)
                match_found = True
        elif action == 'dislike':
            Dislike.objects.get_or_create(disliker=current_user, disliked=liked_profile)
        
        # Fetch the next profile
        excluded_profiles = Like.objects.filter(liker=current_user).values_list('liked', flat=True)
        excluded_profiles = list(excluded_profiles) + [current_user.id]
        next_profile = EntrepreneurProfile.objects.exclude(id__in=excluded_profiles).first()
        
        response_data = {
            'status': 'match' if match_found else 'success',
            'match_found': match_found,
        }

        if match_found:
            match_profile_picture = liked_profile.get_profile_picture_url()
            response_data['match_profile'] = {
                'id': liked_profile.id,
                'full_name': liked_profile.user.get_full_name() or liked_profile.user.username,
                'profile_picture': request.build_absolute_uri(match_profile_picture),
            }
        
        if next_profile:
            next_profile_picture = next_profile.get_profile_picture_url()
            response_data['next_profile'] = {
                'id': next_profile.id,
                'full_name': next_profile.user.get_full_name() or next_profile.user.username,
                'industry': next_profile.industry.name if next_profile.industry else '',
                'company': next_profile.company,
                'bio': next_profile.bio,
                'profile_picture': request.build_absolute_uri(next_profile_picture),
            }
        else:
            response_data['status'] = 'no_more_profiles'
            response_data['redirect_url'] = reverse('matching:no_more_profiles')
        
        return JsonResponse(response_data)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=400)

@login_required
def no_more_profiles(request):
    context = {} # Initialize context
    # Add verification check logic
    try:
        primary_email = EmailAddress.objects.get(user=request.user, primary=True)
        if not primary_email.verified:
            context['show_verification_alert'] = True
            context['unverified_email'] = primary_email.email
    except EmailAddress.DoesNotExist:
        pass # Ignore if no primary email found
    return render(request, 'matching/no_more_profiles.html', context)

@login_required
def matches(request):
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
        user_profile = request.user.entrepreneurprofile
        matches = Match.objects.filter(Q(entrepreneur1=user_profile) | Q(entrepreneur2=user_profile))
        context['matches'] = matches # Add matches to context
        return render(request, 'matching/matches.html', context)
    except EntrepreneurProfile.DoesNotExist:
        return render(request, 'error.html', {'error_message': "Your entrepreneur profile doesn't exist. Please create one."})
    except Exception as e:
        return render(request, 'error.html', {'error_message': "An error occurred while loading your matches. Please try again later."})

@login_required
def swipe(request):
    context = {} # Initialize context
    # --- Start Verification Check ---
    try:
        primary_email = EmailAddress.objects.get(user=request.user, primary=True)
        if not primary_email.verified:
            messages.warning(request, 'Please verify your email address before you can start swiping.')
            # Redirect to profile page - KEEPING REDIRECT HERE as swipe is restricted
            return redirect('entreprinder:profile')
        # If verified, still add alert context in case user navigates here directly
        # (though unlikely if they passed verification)
        # context['show_verification_alert'] = False # Or simply don't set it
    except EmailAddress.DoesNotExist:
        # This case should ideally not happen if ACCOUNT_EMAIL_REQUIRED is True,
        # but handle it defensively.
        messages.error(request, 'Could not find your primary email address. Please contact support.')
        return redirect('entreprinder:profile') # Or home page
    # --- End Verification Check ---

    # Check if user has an EntrepreneurProfile
    try:
        current_user = request.user.entrepreneurprofile
    except EntrepreneurProfile.DoesNotExist:
         messages.error(request, "Please complete your entrepreneur profile before swiping.")
         return redirect('entreprinder:profile') # Redirect to profile creation/edit

    # Get profiles that haven't been interacted with
    interacted_profiles = Like.objects.filter(liker=current_user).values_list('liked', flat=True)
    disliked_profiles = Dislike.objects.filter(disliker=current_user).values_list('disliked', flat=True)
    profile_to_display = EntrepreneurProfile.objects.exclude(
        Q(user=request.user) | Q(id__in=interacted_profiles) | Q(id__in=disliked_profiles)
    ).first()

    if not profile_to_display:
        return redirect('matching:no_more_profiles')

    form = SwipeForm(initial={'entrepreneur_id': profile_to_display.id})

    context['form'] = form
    context['profile'] = profile_to_display
    
    # Add verification check context (will be false if check passed)
    try:
        primary_email = EmailAddress.objects.get(user=request.user, primary=True)
        if not primary_email.verified:
             # This part is technically redundant due to the redirect above,
             # but included for completeness if the redirect logic were removed.
            context['show_verification_alert'] = True
            context['unverified_email'] = primary_email.email
    except EmailAddress.DoesNotExist:
        pass

    return render(request, 'matching/swipe.html', context)
