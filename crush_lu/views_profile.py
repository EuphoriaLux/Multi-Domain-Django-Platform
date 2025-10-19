"""
Profile creation views with step-by-step saving
"""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db import transaction
import json
import logging

from .models import CrushProfile, CrushCoach, ProfileSubmission
from .decorators import crush_login_required

logger = logging.getLogger(__name__)


@crush_login_required
@require_http_methods(["POST"])
def save_profile_step1(request):
    """Save Step 1 (Basic Info) via AJAX - Creates profile with phone number"""
    try:
        data = json.loads(request.body)

        # Check if user is an active coach
        try:
            coach = CrushCoach.objects.get(user=request.user, is_active=True)
            return JsonResponse({
                'success': False,
                'error': 'Coaches cannot create dating profiles.'
            }, status=403)
        except CrushCoach.DoesNotExist:
            pass

        # Get or create profile
        profile, created = CrushProfile.objects.get_or_create(user=request.user)

        # Validate date of birth
        date_of_birth = data.get('date_of_birth')
        if date_of_birth:
            from datetime import datetime
            try:
                dob = datetime.strptime(date_of_birth, '%Y-%m-%d').date()

                # Calculate age
                today = timezone.now().date()
                age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

                # Validate age range
                if age < 18:
                    return JsonResponse({
                        'success': False,
                        'error': 'You must be at least 18 years old to join Crush.lu'
                    }, status=400)

                if age > 99:
                    return JsonResponse({
                        'success': False,
                        'error': 'Please enter a valid date of birth'
                    }, status=400)

            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid date format. Please use YYYY-MM-DD'
                }, status=400)

        # Check if this is the first time completing Step 1
        is_first_time = profile.completion_status != 'step1'

        # Update basic info
        profile.phone_number = data.get('phone_number', '').strip()
        profile.date_of_birth = date_of_birth
        profile.gender = data.get('gender', '')
        profile.location = data.get('location', '')

        # Set completion status and screening flag
        profile.completion_status = 'step1'
        profile.needs_screening_call = True  # Flag for coach to call

        profile.save()

        # Note: Welcome email was already sent after signup
        # No need to send another email here
        logger.info(f"✅ Step 1 saved for {request.user.email}")

        return JsonResponse({
            'success': True,
            'message': 'Basic info saved! Continue to complete your profile.',
            'profile_id': profile.id,
            'needs_call': True
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@crush_login_required
@require_http_methods(["POST"])
def save_profile_step2(request):
    """Save Step 2 (About You) via AJAX"""
    try:
        data = json.loads(request.body)

        profile = CrushProfile.objects.get(user=request.user)

        # Validate looking_for is provided
        looking_for = data.get('looking_for', '').strip()
        if not looking_for:
            return JsonResponse({
                'success': False,
                'error': 'Please select what you\'re looking for'
            }, status=400)

        # Validate looking_for is a valid choice
        from .models import CrushProfile as ProfileModel
        valid_choices = [choice[0] for choice in ProfileModel.LOOKING_FOR_CHOICES]
        if looking_for not in valid_choices:
            return JsonResponse({
                'success': False,
                'error': 'Invalid selection for "looking for"'
            }, status=400)

        # Update profile content
        profile.bio = data.get('bio', '').strip()
        profile.interests = data.get('interests', '').strip()
        profile.looking_for = looking_for
        profile.completion_status = 'step2'

        profile.save()

        return JsonResponse({
            'success': True,
            'message': 'About section saved!'
        })

    except CrushProfile.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Please complete Step 1 first'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@crush_login_required
@require_http_methods(["POST"])
def save_profile_step3(request):
    """Save Step 3 (Photos & Privacy) via AJAX/Form"""
    try:
        profile = CrushProfile.objects.get(user=request.user)

        # Handle photos if uploaded
        if request.FILES.get('photo_1'):
            profile.photo_1 = request.FILES['photo_1']
        if request.FILES.get('photo_2'):
            profile.photo_2 = request.FILES['photo_2']
        if request.FILES.get('photo_3'):
            profile.photo_3 = request.FILES['photo_3']

        # Privacy settings
        profile.show_full_name = request.POST.get('show_full_name') == 'on'
        profile.show_exact_age = request.POST.get('show_exact_age') == 'on'
        profile.blur_photos = request.POST.get('blur_photos') == 'on'
        profile.completion_status = 'step3'

        profile.save()

        return JsonResponse({
            'success': True,
            'message': 'Photos and privacy settings saved!'
        })

    except CrushProfile.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Please complete Step 1 first'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@crush_login_required
def complete_profile_submission(request):
    """Final submission - Mark as completed and submit for review"""
    try:
        profile = CrushProfile.objects.get(user=request.user)

        # Mark as completed
        profile.completion_status = 'submitted'
        profile.save()

        # Create profile submission for coach review
        submission, created = ProfileSubmission.objects.get_or_create(
            profile=profile,
            defaults={'status': 'pending'}
        )

        if created:
            submission.assign_coach()

        messages.success(request, 'Profile submitted for review! A coach will contact you soon.')
        return redirect('crush_lu:profile_submitted')

    except CrushProfile.DoesNotExist:
        messages.error(request, 'Please complete your profile first.')
        return redirect('crush_lu:create_profile')


@crush_login_required
def get_profile_progress(request):
    """Get current profile completion status"""
    try:
        profile = CrushProfile.objects.get(user=request.user)

        return JsonResponse({
            'exists': True,
            'completion_status': profile.completion_status,
            'needs_screening_call': profile.needs_screening_call,
            'screening_call_completed': profile.screening_call_completed,
            'phone_number': profile.phone_number or '',
            'has_basic_info': bool(profile.phone_number and profile.date_of_birth),
            'has_about': bool(profile.bio and profile.interests),
            'has_photos': bool(profile.photo_1 or profile.photo_2 or profile.photo_3),
        })
    except CrushProfile.DoesNotExist:
        return JsonResponse({
            'exists': False,
            'completion_status': None
        })


# Coach views for screening calls

@crush_login_required
def coach_screening_dashboard(request):
    """Coach dashboard for pending screening calls"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    # Get profiles needing screening calls
    pending_calls = CrushProfile.objects.filter(
        needs_screening_call=True,
        screening_call_completed=False
    ).select_related('user').order_by('-created_at')

    # Get completed screening calls
    completed_calls = CrushProfile.objects.filter(
        screening_call_completed=True
    ).select_related('user').order_by('-screening_call_scheduled')[:20]

    context = {
        'coach': coach,
        'pending_calls': pending_calls,
        'completed_calls': completed_calls,
    }
    return render(request, 'crush_lu/coach_screening_dashboard.html', context)


@crush_login_required
def coach_mark_screening_complete(request, profile_id):
    """Mark screening call as completed"""
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
    except CrushCoach.DoesNotExist:
        messages.error(request, 'You do not have coach access.')
        return redirect('crush_lu:dashboard')

    if request.method == 'POST':
        profile = CrushProfile.objects.get(id=profile_id)

        profile.screening_call_completed = True
        profile.screening_call_scheduled = timezone.now()
        profile.screening_notes = request.POST.get('screening_notes', '')
        profile.needs_screening_call = False
        profile.save()

        messages.success(request, f'Screening call completed for {profile.user.first_name}')
        return redirect('crush_lu:coach_screening_dashboard')

    return redirect('crush_lu:coach_screening_dashboard')
