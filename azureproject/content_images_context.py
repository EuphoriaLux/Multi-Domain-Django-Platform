"""
Context processor for content image URLs.

Provides stable Azure Blob URLs in production, static URLs in development.
This allows content images (social previews, default profiles)
to have stable URLs that don't change between deployments.

WhiteNoise hashes static files for cache busting, which causes issues with:
- Social media platforms caching old URLs
- PWA service workers needing predictable URLs
"""
from django.conf import settings


def content_images_context(request):
    """
    Make content image URLs available to all templates.

    In production: Azure Blob Storage URLs (stable, no hashing)
    In development: Static file URLs (served by WhiteNoise/Django)

    Returns:
        dict: Content image URLs organized by domain/feature
    """
    # Get base URL for Azure content (empty in development)
    azure_content_base = getattr(settings, 'AZURE_CONTENT_BASE_URL', '')

    # =========================================================================
    # CRUSH.LU IMAGES
    # =========================================================================
    crush_social_preview = getattr(
        settings, 'CRUSH_SOCIAL_PREVIEW_URL',
        'https://crush.lu/static/crush_lu/crush_social_preview.jpg'
    )

    # =========================================================================
    # POWERUP/ENTREPRINDER IMAGES
    # =========================================================================
    powerup_default_profile = getattr(
        settings, 'POWERUP_DEFAULT_PROFILE_URL',
        '/static/core/images/default-profile.png'
    )

    return {
        # Crush.lu
        'CRUSH_SOCIAL_PREVIEW_URL': crush_social_preview,

        # PowerUP/Entreprinder
        'POWERUP_DEFAULT_PROFILE_URL': powerup_default_profile,

        # Azure content base URL (for building custom URLs in templates/JS)
        'AZURE_CONTENT_BASE_URL': azure_content_base,
    }
