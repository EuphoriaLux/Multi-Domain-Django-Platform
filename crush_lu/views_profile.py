"""
Profile creation views with step-by-step saving
"""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _
from django.db import transaction
import json
import logging
import traceback

from .models import CrushProfile, CrushCoach, ProfileSubmission
from .decorators import crush_login_required
from .coach_notifications import notify_coach_new_submission, notify_coach_user_revision
from .email_helpers import send_profile_submission_notifications
from .utils.image_processing import process_uploaded_image

logger = logging.getLogger(__name__)


@crush_login_required
@require_http_methods(["POST"])
def save_profile_step1(request):
    """Save Step 1 (Basic Info) via AJAX - Creates profile with phone number"""
    try:
        data = json.loads(request.body)

        # Get or create profile
        profile, created = CrushProfile.objects.get_or_create(user=request.user)

        # Check if user is an active coach trying to CREATE a new profile
        # Coaches with existing profiles can still edit them
        if created:
            try:
                coach = CrushCoach.objects.get(user=request.user, is_active=True)
                # Delete the just-created profile and block
                profile.delete()
                return JsonResponse({
                    'success': False,
                    'error': 'Coaches cannot create dating profiles.'
                }, status=403)
            except CrushCoach.DoesNotExist:
                pass

        # Validate date of birth and parse to date object
        date_of_birth_str = data.get('date_of_birth')
        date_of_birth = None  # Will be set to a date object if valid
        if date_of_birth_str:
            from datetime import datetime
            try:
                date_of_birth = datetime.strptime(date_of_birth_str, '%Y-%m-%d').date()

                # Calculate age
                today = timezone.now().date()
                age = today.year - date_of_birth.year - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))

                # Validate age range
                if age < 18:
                    # Preserve data in draft even though validation failed
                    if not profile.draft_data:
                        profile.draft_data = {}
                    profile.draft_data['step1'] = {
                        'phone_number': data.get('phone_number', ''),
                        'date_of_birth': date_of_birth_str,
                        'gender': data.get('gender', ''),
                        'location': data.get('location', ''),
                    }
                    profile.save(update_fields=['draft_data'])

                    return JsonResponse({
                        'success': False,
                        'error': 'You must be at least 18 years old to join Crush.lu'
                    }, status=400)

                if age > 99:
                    # Preserve data in draft
                    if not profile.draft_data:
                        profile.draft_data = {}
                    profile.draft_data['step1'] = {
                        'phone_number': data.get('phone_number', ''),
                        'date_of_birth': date_of_birth_str,
                        'gender': data.get('gender', ''),
                        'location': data.get('location', ''),
                    }
                    profile.save(update_fields=['draft_data'])

                    return JsonResponse({
                        'success': False,
                        'error': 'Please enter a valid date of birth'
                    }, status=400)

            except ValueError:
                # Preserve data in draft even with invalid date format
                if not profile.draft_data:
                    profile.draft_data = {}
                profile.draft_data['step1'] = {
                    'phone_number': data.get('phone_number', ''),
                    'date_of_birth': date_of_birth_str,
                    'gender': data.get('gender', ''),
                    'location': data.get('location', ''),
                }
                profile.save(update_fields=['draft_data'])

                return JsonResponse({
                    'success': False,
                    'error': 'Invalid date format. Please use YYYY-MM-DD'
                }, status=400)

        # Validate required fields: gender and location
        from .forms import CrushProfileForm
        from django.utils.translation import gettext as _

        errors = {}

        # Validate gender
        gender = data.get('gender', '').strip()
        if not gender:
            errors['gender'] = _('Please select your gender')
        else:
            valid_genders = [choice[0] for choice in CrushProfile.GENDER_CHOICES]
            if gender not in valid_genders:
                errors['gender'] = _('Invalid gender selection')

        # Validate location
        location = data.get('location', '').strip()
        if not location:
            errors['location'] = _('Please select your location')
        else:
            valid_locations = [choice[0] for choice in CrushProfileForm.LOCATION_CHOICES if choice[0]]
            if location not in valid_locations:
                errors['location'] = _('Invalid location selection')

        # Return validation errors if any
        if errors:
            # CRITICAL FIX: Preserve invalid data in draft for recovery
            if not profile.draft_data:
                profile.draft_data = {}
            profile.draft_data['step1'] = {
                'phone_number': data.get('phone_number', ''),
                'date_of_birth': date_of_birth_str or '',
                'gender': gender,
                'location': location,
            }
            profile.save(update_fields=['draft_data'])

            return JsonResponse({
                'success': False,
                'error': _('Please fill in all required fields'),
                'errors': errors
            }, status=400)

        # Update basic info
        new_phone_number = data.get('phone_number', '').strip()

        # Check if phone number changed - reset verification if so
        # Note: When phone is verified via Firebase, phone_number is set from the token
        # so we don't want users changing it afterwards without re-verification
        if profile.phone_number and new_phone_number != profile.phone_number:
            # Phone number changed - reset verification status
            profile.phone_verified = False
            profile.phone_verified_at = None
            profile.phone_verification_uid = None
            logger.info(f"Phone number changed for user {request.user.id}, resetting verification")

        profile.phone_number = new_phone_number
        profile.date_of_birth = date_of_birth
        profile.gender = data.get('gender', '')
        profile.location = data.get('location', '')

        # Set completion status
        profile.completion_status = 'step1'
        # Note: Screening call will happen during coach review after full submission

        # Clear step1 draft data on successful save (data now officially saved)
        if profile.draft_data and 'step1' in profile.draft_data:
            del profile.draft_data['step1']

        profile.save()

        # Note: Welcome email was already sent after signup
        # No need to send another email here
        logger.info(f"✅ Step 1 saved for {request.user.email}")

        return JsonResponse({
            'success': True,
            'message': 'Basic info saved! Continue to complete your profile.',
            'profile_id': profile.id
        })

    except Exception as e:
        logger.error(f"Error saving profile step 1: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'An error occurred while saving your profile. Please try again.'
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def save_profile_step2(request):
    """Save Step 2 (About You) via AJAX"""
    try:
        data = json.loads(request.body)

        profile = CrushProfile.objects.get(user=request.user)

        # SECURITY: Enforce phone verification before allowing Step 2
        if not profile.phone_verified:
            return JsonResponse({
                'success': False,
                'error': 'Phone verification required. Please verify your phone number before continuing.',
                'phone_verification_required': True
            }, status=403)

        # Validate looking_for is provided
        looking_for = data.get('looking_for', '').strip()
        if not looking_for:
            # Preserve data in draft
            if not profile.draft_data:
                profile.draft_data = {}
            profile.draft_data['step2'] = {
                'bio': data.get('bio', ''),
                'interests': data.get('interests', ''),
                'looking_for': looking_for,
            }
            profile.save(update_fields=['draft_data'])

            return JsonResponse({
                'success': False,
                'error': 'Please select what you\'re looking for'
            }, status=400)

        # Validate looking_for is a valid choice
        from .models import CrushProfile as ProfileModel
        valid_choices = [choice[0] for choice in ProfileModel.LOOKING_FOR_CHOICES]
        if looking_for not in valid_choices:
            # Preserve data in draft
            if not profile.draft_data:
                profile.draft_data = {}
            profile.draft_data['step2'] = {
                'bio': data.get('bio', ''),
                'interests': data.get('interests', ''),
                'looking_for': looking_for,
            }
            profile.save(update_fields=['draft_data'])

            return JsonResponse({
                'success': False,
                'error': 'Invalid selection for "looking for"'
            }, status=400)

        # Update profile content
        profile.bio = data.get('bio', '').strip()
        profile.interests = data.get('interests', '').strip()
        profile.looking_for = looking_for
        profile.completion_status = 'step2'

        # Clear step2 draft data on successful save, BUT preserve UI-only fields
        if profile.draft_data and 'step2' in profile.draft_data:
            # Preserve interest_category (UI-only, not stored in model)
            interest_category = profile.draft_data['step2'].get('interest_category')

            # Clear the entire step2 draft
            del profile.draft_data['step2']

            # Restore interest_category if it existed
            if interest_category:
                if 'step2' not in profile.draft_data:
                    profile.draft_data['step2'] = {}
                profile.draft_data['step2']['interest_category'] = interest_category

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
        logger.error(f"Error saving profile step 2: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'An error occurred while saving your profile. Please try again.'
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def upload_photo_draft(request):
    """
    Upload a single photo immediately (auto-save).

    This endpoint uploads a photo to Azure Blob Storage and saves the URL
    to the draft, allowing photo persistence across page refreshes.

    Request: multipart/form-data with 'photo' file and 'photo_number' (1-3)

    Response: {success: true, photo_url: '...', photo_number: 1}
    """
    try:
        photo_number = request.POST.get('photo_number')
        if not photo_number or photo_number not in ['1', '2', '3']:
            return JsonResponse({
                'success': False,
                'error': 'Invalid photo_number. Must be 1, 2, or 3.'
            }, status=400)

        photo_file = request.FILES.get('photo')
        if not photo_file:
            return JsonResponse({
                'success': False,
                'error': 'No photo file provided'
            }, status=400)

        # Get or create profile
        profile, created = CrushProfile.objects.get_or_create(user=request.user)

        # Process image: fix orientation, strip EXIF metadata, resize
        photo_file = process_uploaded_image(photo_file)

        # Save photo to the appropriate field
        photo_field = f'photo_{photo_number}'
        setattr(profile, photo_field, photo_file)
        profile.save(update_fields=[photo_field])

        # Get the URL of the uploaded photo
        photo_obj = getattr(profile, photo_field)
        photo_url = photo_obj.url if photo_obj else None

        # Store photo URL in draft for recovery
        if not profile.draft_data:
            profile.draft_data = {}
        if 'step3' not in profile.draft_data:
            profile.draft_data['step3'] = {}

        profile.draft_data['step3'][f'photo_{photo_number}_url'] = photo_url
        profile.save(update_fields=['draft_data'])

        return JsonResponse({
            'success': True,
            'photo_url': photo_url,
            'photo_number': int(photo_number),
            'message': f'Photo {photo_number} uploaded successfully'
        })

    except CrushProfile.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Profile not found'
        }, status=404)
    except Exception as e:
        logger.error(f"Error saving profile step: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': 'An error occurred while saving your profile'
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def save_profile_step3(request):
    """Save Step 3 (Photos & Privacy) via AJAX/Form"""
    try:
        profile = CrushProfile.objects.get(user=request.user)

        # Handle photos if uploaded (for backward compatibility with form submission)
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

        # Event languages (multiple checkboxes stored as JSON array) - REQUIRED
        event_languages = request.POST.getlist('event_languages')
        if not event_languages and profile.draft_data and 'step3' in profile.draft_data:
            # Not in POST, check if it's in the draft
            draft_event_languages = profile.draft_data['step3'].get('event_languages')
            if draft_event_languages and isinstance(draft_event_languages, list):
                event_languages = draft_event_languages

        if not event_languages and not profile.event_languages:
            return JsonResponse({
                'success': False,
                'error': _('Please select at least one event language. You will only be able to sign up for events in your selected languages.'),
            }, status=400)

        if event_languages:
            profile.event_languages = event_languages

        # Note: interest_category checkboxes are UI-only (not stored in model)
        # They're used for filtering/display purposes only

        profile.completion_status = 'step3'

        # Clear step3 draft data on successful save
        if profile.draft_data and 'step3' in profile.draft_data:
            del profile.draft_data['step3']

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
        logger.error(f"Error saving profile step 3: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'An error occurred while saving your profile. Please try again.'
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def complete_profile_submission(request):
    """Final submission - Mark as completed and submit for review"""
    try:
        profile = CrushProfile.objects.get(user=request.user)

        # Validate profile is complete before allowing submission
        missing_fields = profile.get_missing_fields()
        if missing_fields:
            missing_labels = [f['label'] for f in missing_fields]
            logger.warning(
                f"Incomplete profile submission attempt by {request.user.email}: "
                f"missing {', '.join(f['field'] for f in missing_fields)}"
            )
            messages.error(request, _('Please complete all required fields before submitting.'))
            return redirect('crush_lu:create_profile')

        # Mark as completed
        profile.completion_status = 'submitted'

        # Clear ALL draft data on successful submission
        profile.draft_data = {}
        profile.last_draft_saved = None
        profile.draft_expires_at = None

        profile.save()

        # Create profile submission for coach review
        submission, created = ProfileSubmission.objects.get_or_create(
            profile=profile,
            defaults={'status': 'pending'}
        )

        if created:
            submission.assign_coach()
            logger.info(f"NEW profile submission created for {request.user.email}")

            # Send push notification to assigned coach
            if submission.coach:
                try:
                    notify_coach_new_submission(submission.coach, submission)
                    logger.info(f"Coach push notification sent for submission {submission.id}")
                except Exception as e:
                    logger.warning(f"Failed to send coach push notification: {e}")

            # Send confirmation and coach notification emails
            send_profile_submission_notifications(
                submission,
                request,
                add_message_func=lambda msg: messages.warning(request, msg)
            )

        messages.success(request, _('Profile submitted for review! A coach will contact you soon.'))
        return redirect('crush_lu:profile_submitted')

    except CrushProfile.DoesNotExist:
        messages.error(request, _('Please complete your profile first.'))
        return redirect('crush_lu:create_profile')


@crush_login_required
def get_profile_progress(request):
    """Get current profile completion status"""
    try:
        profile = CrushProfile.objects.get(user=request.user)

        return JsonResponse({
            'exists': True,
            'completion_status': profile.completion_status,
            'phone_number': profile.phone_number or '',
            'phone_verified': profile.phone_verified,
            'has_basic_info': bool(profile.phone_number and profile.date_of_birth),
            'has_about': bool(profile.bio and profile.interests),
            'has_photos': bool(profile.photo_1 or profile.photo_2 or profile.photo_3),
        })
    except CrushProfile.DoesNotExist:
        return JsonResponse({
            'exists': False,
            'completion_status': None,
            'phone_verified': False
        })


# =============================================================================
# SOCIAL PHOTO IMPORT API
# =============================================================================

@crush_login_required
@require_http_methods(["GET"])
def get_social_photos_api(request):
    """
    API endpoint to get all available social photos for current user.

    GET /api/profile/social-photos/

    Returns:
        {
            "photos": [
                {
                    "provider": "facebook",
                    "provider_display": "Facebook",
                    "photo_url": "https://...",
                    "available": true,
                    "account_id": 123
                },
                {
                    "provider": "microsoft",
                    "provider_display": "Microsoft",
                    "photo_url": null,
                    "available": false,
                    "account_id": 456,
                    "reason": "No photo set or token expired"
                }
            ]
        }
    """
    from .social_photos import get_all_social_photos

    try:
        photos = get_all_social_photos(request.user)
        return JsonResponse({'photos': photos})
    except Exception as e:
        logger.error(f"Error fetching social photos: {str(e)}", exc_info=True)
        return JsonResponse({
            'error': 'Error fetching social photos'
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def import_social_photo(request):
    """
    API endpoint to import a social account photo to a profile slot.

    POST /api/profile/import-social-photo/
    Body: {
        "social_account_id": 123,
        "photo_slot": 1  // 1, 2, or 3
    }

    Returns:
        {"success": true, "photo_url": "https://..."}
        {"success": false, "error": "No photo available for this provider"}
    """
    from allauth.socialaccount.models import SocialAccount
    from .social_photos import download_and_save_social_photo

    try:
        data = json.loads(request.body)
        social_account_id = data.get('social_account_id')
        photo_slot = data.get('photo_slot')

        # Validate input
        if not social_account_id:
            return JsonResponse({
                'success': False,
                'error': 'Missing social_account_id'
            }, status=400)

        if photo_slot not in [1, 2, 3]:
            return JsonResponse({
                'success': False,
                'error': 'photo_slot must be 1, 2, or 3'
            }, status=400)

        # Get the social account and verify ownership
        try:
            social_account = SocialAccount.objects.get(
                id=social_account_id,
                user=request.user
            )
        except SocialAccount.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Social account not found'
            }, status=404)

        # Download and save the photo
        result = download_and_save_social_photo(
            request.user,
            social_account,
            photo_slot
        )

        if result['success']:
            # Render the updated photo card partial for seamless DOM replacement
            profile = CrushProfile.objects.get(user=request.user)
            html = render_to_string('crush_lu/partials/photo_card.html', {
                'slot': photo_slot,
                'photo': getattr(profile, f'photo_{photo_slot}'),
                'is_main': photo_slot == 1,
            }, request=request)
            return JsonResponse({
                'success': True,
                'photo_url': result['photo_url'],
                'html': html,
                'photo_slot': photo_slot,
            })
        else:
            return JsonResponse(result, status=400)

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Error importing social photo: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Unexpected error. Please try again.'
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def upload_profile_photo(request, slot):
    """
    HTMX endpoint for individual photo uploads.
    Returns the updated photo card partial for inline replacement.

    POST /api/profile/upload-photo/<slot>/
    Files: photo_<slot>
    """
    from .forms import CrushProfileForm

    # Validate slot
    if slot not in [1, 2, 3]:
        return render(request, 'crush_lu/partials/photo_card.html', {
            'slot': slot,
            'photo': None,
            'is_main': slot == 1,
            'error': 'Invalid photo slot',
        })

    try:
        # Use get_or_create to support photo uploads during profile creation
        # (when profile doesn't exist yet)
        profile, created = CrushProfile.objects.get_or_create(user=request.user)
        if created:
            logger.info(f"Created new profile for user {request.user.id} during photo upload")

        # Get uploaded file
        photo_file = request.FILES.get(f'photo_{slot}')
        if not photo_file:
            return render(request, 'crush_lu/partials/photo_card.html', {
                'slot': slot,
                'photo': getattr(profile, f'photo_{slot}'),
                'is_main': slot == 1,
                'error': 'No file provided',
            })

        # Validate photo using form validation
        temp_form = CrushProfileForm(
            data={},
            files={f'photo_{slot}': photo_file},
            instance=profile
        )

        # Check if the photo field is valid
        photo_field_name = f'photo_{slot}'
        if temp_form.fields.get(photo_field_name):
            try:
                cleaned_photo = temp_form.fields[photo_field_name].clean(photo_file)
                setattr(profile, photo_field_name, cleaned_photo)
                profile.save(update_fields=[photo_field_name])

                logger.info(f"Photo {slot} uploaded for user {request.user.id}")

                return render(request, 'crush_lu/partials/photo_card.html', {
                    'slot': slot,
                    'photo': getattr(profile, photo_field_name),
                    'is_main': slot == 1,
                    'just_uploaded': True,
                })
            except Exception as e:
                logger.warning(f"Photo validation failed: {e}", exc_info=True)
                return render(request, 'crush_lu/partials/photo_card.html', {
                    'slot': slot,
                    'photo': getattr(profile, photo_field_name),
                    'is_main': slot == 1,
                    'error': 'Photo validation failed. Please try a different image.',
                })

        # Fallback - save without validation (shouldn't happen)
        setattr(profile, photo_field_name, photo_file)
        profile.save(update_fields=[photo_field_name])

        return render(request, 'crush_lu/partials/photo_card.html', {
            'slot': slot,
            'photo': getattr(profile, photo_field_name),
            'is_main': slot == 1,
            'just_uploaded': True,
        })

    except Exception as e:
        logger.error(f"Error uploading photo {slot}: {str(e)}", exc_info=True)
        return render(request, 'crush_lu/partials/photo_card.html', {
            'slot': slot,
            'photo': None,
            'is_main': slot == 1,
            'error': 'Upload failed. Please try again.',
        })


@crush_login_required
@require_http_methods(["DELETE"])
def delete_profile_photo(request, slot):
    """
    HTMX endpoint for deleting a profile photo.
    Returns the updated photo card partial (empty state) for inline replacement.

    DELETE /api/profile/delete-photo/<slot>/
    """
    # Validate slot
    if slot not in [1, 2, 3]:
        return render(request, 'crush_lu/partials/photo_card.html', {
            'slot': slot,
            'photo': None,
            'is_main': slot == 1,
            'error': 'Invalid photo slot',
        })

    try:
        profile = CrushProfile.objects.get(user=request.user)
        photo_field_name = f'photo_{slot}'
        photo_field = getattr(profile, photo_field_name)

        if photo_field:
            # Delete the actual file
            photo_field.delete(save=False)
            # Clear the field
            setattr(profile, photo_field_name, None)
            profile.save(update_fields=[photo_field_name])
            logger.info(f"Photo {slot} deleted for user {request.user.id}")

        return render(request, 'crush_lu/partials/photo_card.html', {
            'slot': slot,
            'photo': None,
            'is_main': slot == 1,
        })

    except CrushProfile.DoesNotExist:
        return render(request, 'crush_lu/partials/photo_card.html', {
            'slot': slot,
            'photo': None,
            'is_main': slot == 1,
        })
    except Exception as e:
        logger.error(f"Error deleting photo {slot}: {str(e)}", exc_info=True)
        return render(request, 'crush_lu/partials/photo_card.html', {
            'slot': slot,
            'photo': None,
            'is_main': slot == 1,
            'error': 'Delete failed. Please try again.',
        })
