"""
Language preference views for Crush.lu.

This module provides a custom set_language view that:
1. Uses Django's built-in set_language to set the session/cookie language
2. Also updates the user's CrushProfile.preferred_language if authenticated

This ensures email notifications and push notifications use the user's
preferred language, not just the browsing language.
"""
import logging
from django.views.decorators.http import require_POST
from django.views.i18n import set_language as django_set_language
from django.conf import settings

logger = logging.getLogger(__name__)


@require_POST
def set_language_with_profile(request):
    """
    Custom set_language view that also updates user's profile preference.

    This wraps Django's built-in set_language view and additionally:
    - Updates CrushProfile.preferred_language for authenticated users
    - Logs the language change for debugging

    The view reads the 'language' POST parameter (same as Django's view).
    """
    # Get the language being set
    new_language = request.POST.get('language', '')

    # Validate language is supported
    valid_languages = [code for code, name in settings.LANGUAGES]

    if new_language and new_language in valid_languages:
        # Update user's profile if authenticated and has a CrushProfile
        if request.user.is_authenticated:
            try:
                profile = getattr(request.user, 'crushprofile', None)
                if profile:
                    old_language = profile.preferred_language
                    if old_language != new_language:
                        profile.preferred_language = new_language
                        profile.save(update_fields=['preferred_language'])
                        logger.info(
                            f"Updated preferred_language for user {request.user.id}: "
                            f"{old_language} -> {new_language}"
                        )
            except Exception as e:
                # Don't fail the language switch if profile update fails
                logger.warning(
                    f"Failed to update preferred_language for user {request.user.id}: {e}"
                )

    # Call Django's built-in set_language view to handle session/cookie
    return django_set_language(request)
