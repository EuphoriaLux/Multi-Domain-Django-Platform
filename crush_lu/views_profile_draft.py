"""
Draft management API endpoints for Crush.lu profile creation.

These endpoints handle auto-save functionality and draft recovery,
allowing users to preserve their work without validation constraints.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from datetime import timedelta
from .decorators import crush_login_required
from .models import CrushProfile
import json
import logging
import traceback

logger = logging.getLogger(__name__)


@crush_login_required
@require_http_methods(["POST"])
def save_draft(request):
    """
    Auto-save draft data without validation.

    This endpoint stores partial/incomplete/invalid form data to preserve
    user work. Data is merged into profile.draft_data and can be recovered
    even after validation failures or browser crashes.

    Request JSON:
        {
            "step": 1,  # Step number (1-3)
            "data": {   # Field data for this step
                "bio": "I love hiking...",
                "interests": "hiking, coding"
            }
        }

    Response JSON:
        {
            "success": true,
            "saved_at": "2025-02-01T10:30:45Z",
            "message": "Draft saved"
        }
    """
    try:
        data = json.loads(request.body)
        step = data.get('step')
        step_data = data.get('data', {})

        logger.info(f"[DRAFT SAVE] User {request.user.id} saving step {step}")
        logger.debug(f"[DRAFT SAVE] Step data: {step_data}")

        # Validate step number
        if not step or not isinstance(step, int) or step < 1 or step > 3:
            logger.warning(f"[DRAFT SAVE] Invalid step number: {step}")
            return JsonResponse({
                'success': False,
                'error': 'Invalid step number. Must be 1, 2, or 3.'
            }, status=400)

        # Get or create profile
        profile, created = CrushProfile.objects.get_or_create(user=request.user)
        logger.info(f"[DRAFT SAVE] Profile {'created' if created else 'found'} for user {request.user.id}")

        # Initialize draft_data if needed
        if not profile.draft_data:
            profile.draft_data = {}
            logger.debug(f"[DRAFT SAVE] Initialized empty draft_data")

        # Merge step data into draft
        step_key = f'step{step}'
        if step_key not in profile.draft_data:
            profile.draft_data[step_key] = {}
            logger.debug(f"[DRAFT SAVE] Initialized empty {step_key}")

        # Log before merge
        logger.debug(f"[DRAFT SAVE] Before merge - {step_key}: {profile.draft_data[step_key]}")

        profile.draft_data[step_key].update(step_data)

        # Log after merge
        logger.debug(f"[DRAFT SAVE] After merge - {step_key}: {profile.draft_data[step_key]}")

        # Update timestamps
        profile.last_draft_saved = timezone.now()
        profile.draft_expires_at = timezone.now() + timedelta(days=30)

        # Save without validation (using update_fields to skip model validation)
        profile.save(update_fields=['draft_data', 'last_draft_saved', 'draft_expires_at'])

        logger.info(f"[DRAFT SAVE] ✅ Successfully saved draft for user {request.user.id}, step {step}")
        logger.debug(f"[DRAFT SAVE] Full draft_data: {profile.draft_data}")

        return JsonResponse({
            'success': True,
            'saved_at': profile.last_draft_saved.isoformat(),
            'message': 'Draft saved'
        })

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        logger.error(f"Error in profile draft operation: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': 'An error occurred while processing your request'
        }, status=500)


@crush_login_required
@require_http_methods(["GET"])
def get_draft(request):
    """
    Get merged draft + profile data.

    Returns the user's profile data merged with any draft data.
    Draft data takes priority over saved profile fields, allowing
    the UI to show the most recent user input even if it failed validation.

    Response JSON:
        {
            "success": true,
            "data": {
                "profile": {...},      # For backward compatibility
                "draft": {...},        # Raw draft_data from database
                "merged": {...},       # Draft merged over profile (use this)
                "last_saved": "2025-02-01T10:30:45Z"
            }
        }
    """
    try:
        logger.info(f"[DRAFT GET] User {request.user.id} requesting draft data")

        # Try to get existing profile
        try:
            profile = CrushProfile.objects.get(user=request.user)
            logger.info(f"[DRAFT GET] Profile found for user {request.user.id}")
            logger.debug(f"[DRAFT GET] Raw draft_data: {profile.draft_data}")
            logger.debug(f"[DRAFT GET] Profile event_languages: {profile.event_languages}")
            logger.debug(f"[DRAFT GET] Profile show_full_name: {profile.show_full_name}")
            logger.debug(f"[DRAFT GET] Profile show_exact_age: {profile.show_exact_age}")
            logger.debug(f"[DRAFT GET] Profile blur_photos: {profile.blur_photos}")
        except CrushProfile.DoesNotExist:
            # No profile yet - return empty data
            logger.warning(f"[DRAFT GET] No profile found for user {request.user.id}")
            return JsonResponse({
                'success': True,
                'data': {
                    'profile': {},
                    'draft': {},
                    'merged': {},
                    'last_saved': None
                }
            })

        # Start with saved profile fields
        merged = {}
        profile_fields = [
            'phone_number', 'date_of_birth', 'gender', 'location',
            'bio', 'interests', 'event_languages',
            'show_full_name', 'show_exact_age', 'blur_photos'
        ]

        logger.debug(f"[DRAFT GET] Building merged data from profile fields...")
        for field in profile_fields:
            value = getattr(profile, field, None)
            if value is not None:
                # Convert to appropriate JSON type
                if isinstance(value, bool):
                    # Keep boolean as-is (don't convert to string!)
                    merged[field] = value
                    logger.debug(f"[DRAFT GET] Field '{field}' (boolean): {value}")
                elif isinstance(value, (list, dict)):
                    merged[field] = value
                    logger.debug(f"[DRAFT GET] Field '{field}' (array/dict): {value}")
                elif hasattr(value, 'isoformat'):  # Date/DateTime
                    merged[field] = value.isoformat()
                    logger.debug(f"[DRAFT GET] Field '{field}' (date): {value}")
                else:
                    merged[field] = str(value) if value else ''
                    logger.debug(f"[DRAFT GET] Field '{field}' (string): {value}")

        logger.debug(f"[DRAFT GET] Merged data BEFORE draft override: {merged}")

        # Override with draft data (draft takes priority)
        if profile.draft_data:
            logger.info(f"[DRAFT GET] Applying draft data overrides...")
            for step_key, step_data in profile.draft_data.items():
                if isinstance(step_data, dict):
                    logger.debug(f"[DRAFT GET] Merging {step_key}: {step_data}")
                    merged.update(step_data)
        else:
            logger.warning(f"[DRAFT GET] No draft_data found for user {request.user.id}")

        logger.debug(f"[DRAFT GET] Merged data AFTER draft override: {merged}")
        logger.info(f"[DRAFT GET] ✅ Returning merged data with {len(merged)} fields")

        response_data = {
            'success': True,
            'data': {
                'profile': merged,  # For backward compatibility
                'draft': profile.draft_data or {},
                'merged': merged,
                'last_saved': profile.last_draft_saved.isoformat() if profile.last_draft_saved else None
            }
        }

        logger.debug(f"[DRAFT GET] Response data: {response_data}")

        return JsonResponse(response_data)

    except Exception as e:
        logger.error(f"Error in profile draft operation: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': 'An error occurred while processing your request'
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def clear_draft(request):
    """
    Clear draft after successful submission.

    Called when the user successfully completes profile creation.
    Removes all draft data since it's now officially saved in the profile.

    Response JSON:
        {
            "success": true
        }
    """
    try:
        # Try to get profile
        try:
            profile = CrushProfile.objects.get(user=request.user)
            profile.draft_data = {}
            profile.last_draft_saved = None
            profile.draft_expires_at = None
            profile.save(update_fields=['draft_data', 'last_draft_saved', 'draft_expires_at'])
        except CrushProfile.DoesNotExist:
            # No profile exists - nothing to clear
            pass

        return JsonResponse({'success': True})

    except Exception as e:
        logger.error(f"Error in profile draft operation: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': 'An error occurred while processing your request'
        }, status=500)
