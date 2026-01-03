"""
Context processor for content image URLs.

Provides stable Azure Blob URLs in production, static URLs in development.
This allows content images (social previews, journey steps, default profiles)
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
    # VINSDELUX IMAGES
    # =========================================================================
    vinsdelux_journey_base = getattr(
        settings, 'VINSDELUX_JOURNEY_BASE_URL',
        '/static/vinsdelux/images/journey/'
    )

    vinsdelux_vineyard_defaults = getattr(
        settings, 'VINSDELUX_VINEYARD_DEFAULTS_URL',
        '/static/vinsdelux/images/vineyard-defaults/'
    )

    # =========================================================================
    # POWERUP/ENTREPRINDER IMAGES
    # =========================================================================
    powerup_default_profile = getattr(
        settings, 'POWERUP_DEFAULT_PROFILE_URL',
        '/static/vinsdelux/images/default-profile.png'
    )

    return {
        # Crush.lu
        'CRUSH_SOCIAL_PREVIEW_URL': crush_social_preview,

        # VinsDelux
        'VINSDELUX_JOURNEY_BASE_URL': vinsdelux_journey_base,
        'VINSDELUX_VINEYARD_DEFAULTS_URL': vinsdelux_vineyard_defaults,
        # Individual journey step URLs for convenience
        'VINSDELUX_JOURNEY_STEP_1': f'{vinsdelux_journey_base}step_01.png',
        'VINSDELUX_JOURNEY_STEP_2': f'{vinsdelux_journey_base}step_02.png',
        'VINSDELUX_JOURNEY_STEP_3': f'{vinsdelux_journey_base}step_03.png',
        'VINSDELUX_JOURNEY_STEP_4': f'{vinsdelux_journey_base}step_04.png',
        'VINSDELUX_JOURNEY_STEP_5': f'{vinsdelux_journey_base}step_05.png',

        # PowerUP/Entreprinder
        'POWERUP_DEFAULT_PROFILE_URL': powerup_default_profile,

        # Azure content base URL (for building custom URLs in templates/JS)
        'AZURE_CONTENT_BASE_URL': azure_content_base,
    }
