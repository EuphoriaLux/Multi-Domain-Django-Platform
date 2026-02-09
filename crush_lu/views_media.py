"""
Secure media serving views for Crush.lu
Handles photo access with authentication and privacy checks
"""

from django.shortcuts import get_object_or_404
from django.http import HttpResponse, HttpResponseForbidden, Http404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from PIL import Image, ImageFilter
import os
import logging

from .models import CrushProfile, CrushCoach

logger = logging.getLogger(__name__)


def can_view_profile_photo(viewer, profile_owner):
    """
    Determine if viewer can see profile_owner's photos

    Rules:
    - Owner can always see their own photos
    - Coaches can see all photos (for review)
    - Approved profiles: visible to other approved users
    - Unapproved profiles: only visible to owner and coaches
    - Blur setting: respected for non-mutual connections

    Args:
        viewer: User object of the person viewing
        profile_owner: CrushProfile object being viewed

    Returns:
        dict with 'allowed' (bool) and 'blur' (bool)
    """
    # Owner can always see their own photos
    if viewer == profile_owner.user:
        return {'allowed': True, 'blur': False}

    # Check if viewer is a coach
    try:
        from .models import CrushCoach
        coach = CrushCoach.objects.get(user=viewer, is_active=True)
        # Coaches can see all photos unblurred (for review)
        return {'allowed': True, 'blur': False}
    except CrushCoach.DoesNotExist:
        pass

    # Profile must be approved for others to see
    if not profile_owner.is_approved:
        return {'allowed': False, 'blur': False}

    # Check if viewer has an approved profile
    try:
        viewer_profile = CrushProfile.objects.get(user=viewer)
        if not viewer_profile.is_approved:
            return {'allowed': False, 'blur': False}
    except CrushProfile.DoesNotExist:
        return {'allowed': False, 'blur': False}

    # Check blur_photos privacy setting
    should_blur = profile_owner.blur_photos

    # Check for mutual connections - if connected, don't blur
    if should_blur:
        from .models import EventConnection
        # Check if there's a mutual connection between viewer and profile owner
        # A connection is mutual if both parties have requested to connect with each other
        # OR if one party requested and the other accepted
        has_mutual_connection = (
            # Viewer requested connection with profile owner (accepted or coach-approved status)
            EventConnection.objects.filter(
                requester=viewer,
                recipient=profile_owner.user,
                status__in=['accepted', 'coach_approved', 'shared']
            ).exists()
            or
            # Profile owner requested connection with viewer (accepted or coach-approved status)
            EventConnection.objects.filter(
                requester=profile_owner.user,
                recipient=viewer,
                status__in=['accepted', 'coach_approved', 'shared']
            ).exists()
        )
        if has_mutual_connection:
            should_blur = False

    return {'allowed': True, 'blur': should_blur}


def apply_blur_to_image(image_path):
    """
    Apply blur effect to an image file

    Args:
        image_path: Path to image file

    Returns:
        PIL Image object with blur applied
    """
    try:
        img = Image.open(image_path)
        # Apply Gaussian blur
        blurred = img.filter(ImageFilter.GaussianBlur(radius=20))
        return blurred
    except Exception as e:
        logger.error(f"Error blurring image {image_path}: {e}")
        raise


@login_required
def serve_profile_photo(request, user_id, photo_field):
    """
    Serve profile photos with authentication and privacy checks

    URL: /crush/media/profile/{user_id}/{photo_field}/
    Where photo_field is: photo_1, photo_2, or photo_3

    Rate limits: 60/min for regular users, 300/min for coaches.

    Args:
        user_id: ID of the profile owner
        photo_field: Which photo field (photo_1, photo_2, photo_3)

    Returns:
        Image file or 403/404 error
    """
    # Apply rate limit: higher for coaches who review many profiles
    is_coach = CrushCoach.objects.filter(user=request.user, is_active=True).exists()
    max_requests = 300 if is_coach else 60
    period_seconds = 60  # 1 minute window
    cache_key = f'ratelimit:serve_profile_photo:user_{request.user.id}'
    try:
        current = cache.get(cache_key, 0)
    except Exception:
        current = 0
    if current >= max_requests:
        return HttpResponse('Rate limit exceeded. Please try again later.', status=429)
    try:
        if current == 0:
            cache.set(cache_key, 1, period_seconds)
        else:
            try:
                cache.incr(cache_key)
            except ValueError:
                cache.set(cache_key, 1, period_seconds)
    except Exception:
        pass

    # Validate photo_field
    if photo_field not in ['photo_1', 'photo_2', 'photo_3']:
        raise Http404("Invalid photo field")

    # Get the profile
    profile = get_object_or_404(CrushProfile, user_id=user_id)

    # Check permissions
    permission = can_view_profile_photo(request.user, profile)
    if not permission['allowed']:
        logger.warning(
            f"User {request.user.id} denied access to {profile.user.id}'s {photo_field}"
        )
        raise PermissionDenied("You don't have permission to view this photo")

    # Get the photo field
    photo = getattr(profile, photo_field)
    if not photo:
        raise Http404("Photo not found")

    # AZURE BLOB STORAGE: Generate SAS URL and redirect
    if hasattr(settings, 'AZURE_ACCOUNT_NAME') and settings.AZURE_ACCOUNT_NAME:
        # Generate time-limited SAS URL
        from .storage import CrushProfilePhotoStorage
        storage = CrushProfilePhotoStorage()

        # Get secure URL with SAS token
        secure_url = storage.url(photo.name, expire=1800)  # 30 min expiry

        # For blurred photos, fetch from Azure, apply blur, and serve directly
        if permission['blur']:
            try:
                import requests
                from io import BytesIO

                # Fetch image from Azure blob storage
                response = requests.get(secure_url, timeout=10)
                response.raise_for_status()

                # Apply blur using PIL
                img = Image.open(BytesIO(response.content))
                blurred = img.filter(ImageFilter.GaussianBlur(radius=20))

                # Serve blurred image
                http_response = HttpResponse(content_type='image/jpeg')
                blurred.save(http_response, 'JPEG', quality=85)
                http_response['Cache-Control'] = 'private, max-age=300'  # 5 min cache
                return http_response
            except Exception as e:
                logger.error(f"Error blurring Azure photo {photo.name}: {e}")
                # Fall back to unblurred if blur fails (privacy vs availability trade-off)
                # For strict privacy, you could raise Http404 instead
                logger.warning(f"Serving unblurred photo due to blur failure: {photo.name}")

        # Redirect to Azure with SAS token (for non-blurred photos or blur fallback)
        from django.shortcuts import redirect
        return redirect(secure_url)

    # LOCAL FILESYSTEM: Serve directly with optional blur
    else:
        photo_path = photo.path

        # Check if file exists
        if not os.path.exists(photo_path):
            raise Http404("Photo file not found")

        # Apply blur if needed
        if permission['blur']:
            try:
                img = apply_blur_to_image(photo_path)
                response = HttpResponse(content_type='image/jpeg')
                img.save(response, 'JPEG', quality=85)
                return response
            except Exception as e:
                logger.error(f"Error serving blurred photo: {e}")
                raise Http404("Error processing photo")

        # Serve original photo
        try:
            with open(photo_path, 'rb') as f:
                content_type = 'image/jpeg'
                if photo_path.lower().endswith('.png'):
                    content_type = 'image/png'
                elif photo_path.lower().endswith('.webp'):
                    content_type = 'image/webp'

                response = HttpResponse(f.read(), content_type=content_type)
                response['Content-Disposition'] = 'inline'
                return response
        except Exception as e:
            logger.error(f"Error serving photo {photo_path}: {e}")
            raise Http404("Error loading photo")


def get_profile_photo_url(profile, photo_field, request=None):
    """
    Helper function to generate the correct photo URL

    Use this in templates and views instead of accessing photo.url directly

    Args:
        profile: CrushProfile instance
        photo_field: Which photo ('photo_1', 'photo_2', 'photo_3')
        request: Optional request object (for building absolute URLs)

    Returns:
        URL string to the photo (through the secure view)
    """
    from django.urls import reverse

    if not getattr(profile, photo_field):
        return None

    # Generate URL through the secure view
    url = reverse('crush_lu:serve_profile_photo', kwargs={
        'user_id': profile.user.id,
        'photo_field': photo_field
    })

    # Build absolute URL if request provided
    if request:
        return request.build_absolute_uri(url)

    return url
