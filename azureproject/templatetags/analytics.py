"""
Custom template tags for multi-domain analytics (GA4, Facebook Pixel, App Insights).

Integrates with django-cookie-consent for GDPR compliance.
Analytics scripts only load when user has given consent.

Usage in templates:
    {% load analytics %}

    In <head>:
        {% analytics_head %}
        {% appinsights_head %}

    After <body>:
        {% analytics_body %}

The analytics IDs are provided by the analytics_context.analytics_ids context processor
which sets GOOGLE_ANALYTICS_GTAG_PROPERTY_ID, FACEBOOK_PIXEL_ID, and
APPLICATIONINSIGHTS_CONNECTION_STRING based on domain.
"""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def get_cookie_consent(request, cookie_group):
    """
    Check if user has consented to a specific cookie group.

    Returns True if:
    - Cookie consent is accepted for the group
    - No consent cookie exists (first visit - we'll show banner)

    Returns False if:
    - User explicitly declined the cookie group
    """
    try:
        from cookie_consent.util import get_cookie_value_from_request
        consent = get_cookie_value_from_request(request, cookie_group)
        # consent is True (accepted), False (declined), or None (not yet decided)
        # We return True for None to allow GA4 consent mode to handle it
        return consent is not False
    except Exception:
        # If cookie_consent not available, default to allowing analytics
        return True


@register.simple_tag(takes_context=True)
def analytics_head(context):
    """
    Render GA4 gtag.js script in the <head> section.

    Uses GA4 Consent Mode v2 for GDPR compliance:
    - Scripts always load (for consent mode to work)
    - Default consent is 'denied' until user accepts
    - Updates consent when user interacts with cookie banner

    This tag should be placed near the top of <head> for best performance.
    """
    ga4_id = context.get('GOOGLE_ANALYTICS_GTAG_PROPERTY_ID')

    if not ga4_id:
        return ''

    request = context.get('request')
    has_analytics_consent = get_cookie_consent(request, 'analytics') if request else True

    # Get CSP nonce from request (if available)
    nonce = getattr(request, 'csp_nonce', '') if request else ''
    nonce_attr = f' nonce="{nonce}"' if nonce else ''

    # GA4 gtag.js with Consent Mode v2
    # Default to denied, update based on cookie consent
    consent_default = 'granted' if has_analytics_consent else 'denied'

    script = f'''<!-- Google Analytics 4 with Consent Mode v2 -->
<script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
<script{nonce_attr}>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}

  // Default consent - denied until user accepts
  gtag('consent', 'default', {{
    'analytics_storage': '{consent_default}',
    'ad_storage': 'denied',
    'ad_user_data': 'denied',
    'ad_personalization': 'denied',
    'wait_for_update': 500
  }});

  gtag('js', new Date());
  gtag('config', '{ga4_id}', {{
    'anonymize_ip': true
  }});

  // Listen for cookie consent updates
  document.addEventListener('cookie_consent_updated', function(e) {{
    if (e.detail && e.detail.analytics) {{
      gtag('consent', 'update', {{
        'analytics_storage': 'granted'
      }});
    }}
  }});
</script>'''

    return mark_safe(script)


@register.simple_tag(takes_context=True)
def analytics_body(context):
    """
    Render Facebook Pixel script after <body> opening tag.

    Only loads if user has consented to marketing/analytics cookies.
    This tag should be placed right after the opening <body> tag.
    """
    fb_pixel_id = context.get('FACEBOOK_PIXEL_ID')

    if not fb_pixel_id:
        return ''

    request = context.get('request')
    has_marketing_consent = get_cookie_consent(request, 'marketing') if request else True

    # Get CSP nonce from request (if available)
    nonce = getattr(request, 'csp_nonce', '') if request else ''
    nonce_attr = f' nonce="{nonce}"' if nonce else ''

    if not has_marketing_consent:
        # Return placeholder that can be activated later
        return mark_safe(f'''<!-- Facebook Pixel (waiting for consent) -->
<script{nonce_attr}>
  window.fbPixelId = '{fb_pixel_id}';
  document.addEventListener('cookie_consent_updated', function(e) {{
    if (e.detail && e.detail.marketing && !window.fbq) {{
      !function(f,b,e,v,n,t,s)
      {{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
      n.callMethod.apply(n,arguments):n.queue.push(arguments)}};
      if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
      n.queue=[];t=b.createElement(e);t.async=!0;
      t.src=v;s=b.getElementsByTagName(e)[0];
      s.parentNode.insertBefore(t,s)}}(window, document,'script',
      'https://connect.facebook.net/en_US/fbevents.js');
      fbq('init', window.fbPixelId);
      fbq('track', 'PageView');
    }}
  }});
</script>''')

    # Full Facebook Pixel implementation
    script = f'''<!-- Facebook Pixel -->
<script{nonce_attr}>
  !function(f,b,e,v,n,t,s)
  {{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
  n.callMethod.apply(n,arguments):n.queue.push(arguments)}};
  if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
  n.queue=[];t=b.createElement(e);t.async=!0;
  t.src=v;s=b.getElementsByTagName(e)[0];
  s.parentNode.insertBefore(t,s)}}(window, document,'script',
  'https://connect.facebook.net/en_US/fbevents.js');
  fbq('init', '{fb_pixel_id}');
  fbq('track', 'PageView');
</script>
<noscript><img height="1" width="1" style="display:none"
  src="https://www.facebook.com/tr?id={fb_pixel_id}&ev=PageView&noscript=1"
/></noscript>'''

    return mark_safe(script)


@register.simple_tag(takes_context=True)
def ga4_event(context, event_name, **params):
    """
    Track a custom GA4 event.

    Usage:
        {% ga4_event "purchase" value=99.99 currency="EUR" %}
        {% ga4_event "sign_up" method="LinkedIn" %}
    """
    ga4_id = context.get('GOOGLE_ANALYTICS_GTAG_PROPERTY_ID')

    if not ga4_id:
        return ''

    request = context.get('request')
    nonce = getattr(request, 'csp_nonce', '') if request else ''
    nonce_attr = f' nonce="{nonce}"' if nonce else ''

    # Build params object
    if params:
        params_str = ', '.join(f"'{k}': '{v}'" for k, v in params.items())
        script = f"<script{nonce_attr}>gtag('event', '{event_name}', {{{params_str}}});</script>"
    else:
        script = f"<script{nonce_attr}>gtag('event', '{event_name}');</script>"

    return mark_safe(script)


@register.simple_tag(takes_context=True)
def fb_event(context, event_name, **params):
    """
    Track a custom Facebook Pixel event.

    Usage:
        {% fb_event "Purchase" value=99.99 currency="EUR" %}
        {% fb_event "Lead" %}
    """
    fb_pixel_id = context.get('FACEBOOK_PIXEL_ID')

    if not fb_pixel_id:
        return ''

    request = context.get('request')
    nonce = getattr(request, 'csp_nonce', '') if request else ''
    nonce_attr = f' nonce="{nonce}"' if nonce else ''

    # Build params object
    if params:
        params_str = ', '.join(f"'{k}': '{v}'" for k, v in params.items())
        script = f"<script{nonce_attr}>if(window.fbq)fbq('track', '{event_name}', {{{params_str}}});</script>"
    else:
        script = f"<script{nonce_attr}>if(window.fbq)fbq('track', '{event_name}');</script>"

    return mark_safe(script)


@register.simple_tag(takes_context=True)
def appinsights_head(context):
    """
    Render Azure Application Insights JavaScript SDK in the <head> section.

    This enables browser-side telemetry collection including:
    - Page views and navigation timing
    - Browser exceptions and errors
    - AJAX/fetch request tracking
    - User session tracking
    - Custom events via window.appInsights.trackEvent()

    The SDK automatically correlates browser telemetry with server-side
    telemetry using the same instrumentation key.

    PERFORMANCE: Loads asynchronously to avoid render-blocking.
    - Preconnect hint for faster connection establishment
    - SDK loaded with async attribute
    - Stub functions queue events until SDK is ready

    Usage in templates:
        {% load analytics %}
        {% appinsights_head %}

    Track custom events in JavaScript:
        window.appInsights.trackEvent({name: 'ButtonClicked', properties: {buttonId: 'signup'}});
        window.appInsights.trackPageView({name: 'Profile Page'});
    """
    connection_string = context.get('APPLICATIONINSIGHTS_CONNECTION_STRING')

    if not connection_string:
        return ''

    request = context.get('request')
    nonce = getattr(request, 'csp_nonce', '') if request else ''
    nonce_attr = f' nonce="{nonce}"' if nonce else ''

    # Get user ID for authenticated user tracking (anonymous if not logged in)
    user_id = ''
    if request and hasattr(request, 'user') and request.user.is_authenticated:
        # Use hashed user ID for privacy (don't expose actual user IDs)
        import hashlib
        user_id = hashlib.sha256(str(request.user.id).encode()).hexdigest()[:16]

    # Application Insights JavaScript SDK v3 (via CDN)
    # PERFORMANCE: Load asynchronously to avoid render-blocking
    # Uses simplified loader pattern - loads SDK and initializes with config
    # See: https://learn.microsoft.com/en-us/azure/azure-monitor/app/javascript-sdk
    user_context_js = f'''
    // Set authenticated user context for correlation
    if (window.appInsights && window.appInsights.setAuthenticatedUserContext) {{
        window.appInsights.setAuthenticatedUserContext("{user_id}");
    }}''' if user_id else ''

    script = f'''<!-- Azure Application Insights Browser SDK v3 -->
<link rel="preconnect" href="https://js.monitor.azure.com" crossorigin>
<script{nonce_attr}>
(function() {{
    "use strict";
    var sdkUrl = "https://js.monitor.azure.com/scripts/b/ai.3.gbl.min.js";
    var connectionString = "{connection_string}";

    // Create stub for queueing calls before SDK loads
    var appInsights = window.appInsights || {{
        queue: [],
        trackEvent: function(e) {{ this.queue.push(["trackEvent", e]); }},
        trackPageView: function(e) {{ this.queue.push(["trackPageView", e]); }},
        trackException: function(e) {{ this.queue.push(["trackException", e]); }},
        trackTrace: function(e) {{ this.queue.push(["trackTrace", e]); }},
        trackMetric: function(e) {{ this.queue.push(["trackMetric", e]); }},
        setAuthenticatedUserContext: function(id) {{ this.queue.push(["setAuthenticatedUserContext", id]); }},
        clearAuthenticatedUserContext: function() {{ this.queue.push(["clearAuthenticatedUserContext"]); }},
        flush: function() {{ this.queue.push(["flush"]); }}
    }};
    window.appInsights = appInsights;

    // Load SDK script
    var script = document.createElement("script");
    script.src = sdkUrl;
    script.crossOrigin = "anonymous";
    script.onload = function() {{
        // Initialize SDK after load
        var sdk = new Microsoft.ApplicationInsights.ApplicationInsights({{
            config: {{
                connectionString: connectionString,
                enableAutoRouteTracking: true,
                enableCorsCorrelation: true,
                enableRequestHeaderTracking: true,
                enableResponseHeaderTracking: true,
                enableAjaxPerfTracking: true,
                maxBatchInterval: 15000,
                disableFetchTracking: false,
                disableExceptionTracking: false,
                autoTrackPageVisitTime: false
            }}
        }});
        sdk.loadAppInsights();
        sdk.trackPageView();

        // Replace stub with real SDK
        window.appInsights = sdk;

        // Process queued calls
        if (appInsights.queue && appInsights.queue.length) {{
            appInsights.queue.forEach(function(call) {{
                var method = call[0];
                var args = call.slice(1);
                if (sdk[method]) {{
                    sdk[method].apply(sdk, args);
                }}
            }});
        }}{user_context_js}
    }};
    script.onerror = function() {{
        console.warn("Application Insights SDK failed to load");
    }};

    // Insert script
    var firstScript = document.getElementsByTagName("script")[0];
    firstScript.parentNode.insertBefore(script, firstScript);
}})();
</script>'''

    return mark_safe(script)


@register.simple_tag(takes_context=True)
def appinsights_event(context, event_name, **params):
    """
    Track a custom Application Insights event.

    Usage:
        {% appinsights_event "signup_started" method="email" %}
        {% appinsights_event "profile_created" %}

    This renders a script tag that calls trackEvent on the App Insights SDK.
    """
    connection_string = context.get('APPLICATIONINSIGHTS_CONNECTION_STRING')

    if not connection_string:
        return ''

    request = context.get('request')
    nonce = getattr(request, 'csp_nonce', '') if request else ''
    nonce_attr = f' nonce="{nonce}"' if nonce else ''

    # Build properties object
    if params:
        props_str = ', '.join(f'"{k}": "{v}"' for k, v in params.items())
        script = f'<script{nonce_attr}>if(window.appInsights)appInsights.trackEvent({{name: "{event_name}", properties: {{{props_str}}}}});</script>'
    else:
        script = f'<script{nonce_attr}>if(window.appInsights)appInsights.trackEvent({{name: "{event_name}"}});</script>'

    return mark_safe(script)
