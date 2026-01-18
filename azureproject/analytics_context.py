"""
Analytics context processor for multi-domain analytics configuration.

This module provides domain-specific analytics IDs (GA4, Facebook Pixel)
and Azure Application Insights configuration to templates via Django's
context processor system.

django-analytical reads these context variables to inject the appropriate
tracking scripts into templates.
"""

import os


def analytics_ids(request):
    """
    Return domain-specific analytics IDs for django-analytical.

    This context processor sets the appropriate GA4 and Facebook Pixel IDs
    based on the current request's host domain. Also provides the Azure
    Application Insights connection string for browser telemetry.

    Environment Variables:
        GA4_CRUSH_LU: Google Analytics 4 Measurement ID for crush.lu
        GA4_DELEGATIONS: Google Analytics 4 Measurement ID for delegations.lu
        GA4_VINSDELUX: Google Analytics 4 Measurement ID for vinsdelux.com
        GA4_POWERUP: Google Analytics 4 Measurement ID for powerup.lu/entreprinder
        GA4_ARBORIST: Google Analytics 4 Measurement ID for arborist.lu
        FB_PIXEL_CRUSH_LU: Facebook Pixel ID for crush.lu
        FB_PIXEL_DELEGATIONS: Facebook Pixel ID for delegations.lu
        APPLICATIONINSIGHTS_CONNECTION_STRING: Azure App Insights connection string

    Returns:
        dict: Context variables for django-analytical template tags
    """
    host = request.get_host().lower()

    # Default: no analytics (will render nothing in templates)
    context = {
        'GOOGLE_ANALYTICS_GTAG_PROPERTY_ID': None,
        'FACEBOOK_PIXEL_ID': None,
        # Azure Application Insights connection string for browser SDK
        'APPLICATIONINSIGHTS_CONNECTION_STRING': os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING'),
    }

    # Domain-specific analytics configuration
    if 'crush.lu' in host:
        # Crush.lu dating platform
        context['GOOGLE_ANALYTICS_GTAG_PROPERTY_ID'] = os.getenv('GA4_CRUSH_LU')
        context['FACEBOOK_PIXEL_ID'] = os.getenv('FB_PIXEL_CRUSH_LU')

    elif 'delegations.lu' in host:
        # Delegations.lu - separate domain with its own analytics
        context['GOOGLE_ANALYTICS_GTAG_PROPERTY_ID'] = os.getenv('GA4_DELEGATIONS')
        context['FACEBOOK_PIXEL_ID'] = os.getenv('FB_PIXEL_DELEGATIONS')

    elif 'vinsdelux.com' in host:
        # VinsDelux wine platform
        context['GOOGLE_ANALYTICS_GTAG_PROPERTY_ID'] = os.getenv('GA4_VINSDELUX')

    elif 'arborist.lu' in host:
        # Arborist Tom Aakrann tree care services
        context['GOOGLE_ANALYTICS_GTAG_PROPERTY_ID'] = os.getenv('GA4_ARBORIST')

    elif 'powerup.lu' in host or 'entreprinder' in host:
        # PowerUP / Entreprinder business networking
        context['GOOGLE_ANALYTICS_GTAG_PROPERTY_ID'] = os.getenv('GA4_POWERUP')

    elif 'localhost' in host or '127.0.0.1' in host:
        # Development: Use Crush.lu analytics for local testing (since DEV_DEFAULT is crush.lu)
        # Set to None to disable tracking in development, or use a test property
        context['GOOGLE_ANALYTICS_GTAG_PROPERTY_ID'] = os.getenv('GA4_CRUSH_LU')
        context['FACEBOOK_PIXEL_ID'] = os.getenv('FB_PIXEL_CRUSH_LU')

    elif 'azurewebsites.net' in host:
        # Azure staging/production default (PowerUP)
        context['GOOGLE_ANALYTICS_GTAG_PROPERTY_ID'] = os.getenv('GA4_POWERUP')

    return context
