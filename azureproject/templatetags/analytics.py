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
    Render GA4 gtag.js script in the <head> section with Google Consent Mode v2.

    Implements Google's advanced consent mode requirements:
    1. Sets default consent state BEFORE gtag.js loads
    2. Configures all 4 required consent types:
       - ad_storage, ad_user_data, ad_personalization, analytics_storage
    3. Respects existing cookie consent preferences

    The cookie_banner.html handles gtag('consent', 'update', ...) when user
    changes their preferences.

    See: https://developers.google.com/tag-platform/security/guides/consent

    This tag should be placed near the top of <head> for best performance.
    """
    ga4_id = context.get('GOOGLE_ANALYTICS_GTAG_PROPERTY_ID')

    if not ga4_id:
        return ''

    request = context.get('request')

    # Get CSP nonce from request (if available)
    nonce = getattr(request, 'csp_nonce', '') if request else ''
    nonce_attr = f' nonce="{nonce}"' if nonce else ''

    # Check existing consent from cookies
    # Returns True if consented, False if declined, True if not yet decided
    # (we default to denied for new visitors per GDPR best practice)
    has_analytics = get_cookie_consent(request, 'analytics') if request else False
    has_marketing = get_cookie_consent(request, 'marketing') if request else False

    # For first-time visitors (no consent cookie), default to denied
    # get_cookie_consent returns True for None (not decided), but we want denied
    try:
        from cookie_consent.util import get_cookie_value_from_request
        analytics_value = get_cookie_value_from_request(request, 'analytics')
        marketing_value = get_cookie_value_from_request(request, 'marketing')
        # Only grant if explicitly accepted (True), deny for None or False
        analytics_granted = 'granted' if analytics_value is True else 'denied'
        marketing_granted = 'granted' if marketing_value is True else 'denied'
    except Exception:
        # Fallback: denied by default for GDPR compliance
        analytics_granted = 'denied'
        marketing_granted = 'denied'

    # Google Consent Mode v2 + GA4 gtag.js
    # CRITICAL: Default consent MUST be set BEFORE gtag.js loads
    script = f'''<!-- Google Consent Mode v2 + gtag.js -->
<script{nonce_attr}>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}

  // Set default consent state BEFORE gtag.js loads (Google Consent Mode v2)
  gtag('consent', 'default', {{
    'ad_storage': '{marketing_granted}',
    'ad_user_data': '{marketing_granted}',
    'ad_personalization': '{marketing_granted}',
    'analytics_storage': '{analytics_granted}',
    'wait_for_update': 500
  }});
</script>
<script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
<script{nonce_attr}>
  gtag('js', new Date());
  gtag('config', '{ga4_id}');
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
    # Uses the modern SDK Loader script pattern (recommended approach)
    # See: https://learn.microsoft.com/en-us/azure/azure-monitor/app/javascript-sdk
    user_context_js = f'''
        // Set authenticated user context for correlation
        if (window.appInsights && window.appInsights.context && window.appInsights.context.user) {{
            window.appInsights.context.user.authenticatedId = "{user_id}";
        }}''' if user_id else ''

    script = f'''<!-- Azure Application Insights Browser SDK v3 -->
<link rel="preconnect" href="https://js.monitor.azure.com" crossorigin>
<script type="text/javascript"{nonce_attr}>
!function(v,y,T){{var S=v.location,k="script",D="connectionString",C="ingestionendpoint",I="disableExceptionTracking",E="ai.device.",b="toLowerCase",w="crossOrigin",N="POST",e="appInsightsSDK",t=T.name||"appInsights";(T.name||v[e])&&(v[e]=t);var n=v[t]||function(l){{var u=!1,d=!1,g={{initialize:!0,queue:[],sv:"8",version:2,config:l}};function m(e,t){{var n={{}},a="Browser";return n[E+"id"]=a[b](),n[E+"type"]=a,n["ai.operation.name"]=S&&S.pathname||"_unknown_",n["ai.internal.sdkVersion"]="javascript:snippet_"+(g.sv||g.version),n}}function e(n){{var a=y.createElement(k);a.src=n;var e=T[w];return!e&&""!==e||"undefined"==n[w]||(a[w]=e),a.onload=function(){{if(u)try{{d=!0;var e=v[t];c(n),e.queue&&0<e.queue.length&&e.emptyQueue()}}catch(e){{}}else r("SDK Load Timeout",null,n)}},a.onerror=function(){{r("SDK Load Failed",null,n)}},a}}function a(e,t){{d||setTimeout(function(){{!d&&u&&r("SDK Load Timeout",null,e)}},t)}}function r(e,t,n){{u=!1,d=!0;var a=y.createElement("div");a.innerHTML="<img src='https://dc.services.visualstudio.com/v2/track?name=SDK+Load+Failure&properties={{%22sdkVersion%22:%22"+(g.sv||g.version)+"%22,%22message%22:%22"+encodeURIComponent(e)+"%22,%22url%22:%22"+encodeURIComponent(n)+"%22}}'/>"}}"{{0}}".replace("{{0}}",l[D])===l[D]&&(l[D]="");function c(e){{for(var t,n,a,i,o,s=0;s<T.featureOptIn.length;s++)T.featureOptIn[s]===e&&(i=T.featureOptIn,o=s,i.splice(o,1))}}try{{u=!0;var i=T.url||"https://js.monitor.azure.com/scripts/b/ai.3.gbl.min.js";if(-1<(l[D]||"").indexOf(C)&&-1<i.indexOf("/scripts/b/")){{"string"==typeof T.ld&&(l[D]=function(e){{var t,n=e.split(C);return 2<=n.length&&(t=n[1].split("/"),n[0]+C+t[0]),n[0]}}(l[D]));var o="https://js.monitor.azure.com/scripts/b/ext/ai.clck.3.min.js";T.cr=!0,T.featureOptIn=T.featureOptIn||[],T.featureOptIn.push(o)}}T.ld=T.ld||-1;var s=y.getElementsByTagName(k)[0],f=y.createElement(k);if(!0===T.ld)s.parentNode.insertBefore(e(i),s),a(i,T.ld);else{{var h=function(){{s.parentNode.insertBefore(e(i),s),a(i,T.ld)}};-1!==T.ld?setTimeout(h,T.ld):h()}}if(T.featureOptIn&&T.featureOptIn.length)for(var p=0;p<T.featureOptIn.length;p++)s.parentNode.insertBefore(e(T.featureOptIn[p]),s)}}catch(e){{u=!1,r("SDK Load Failure",e,i)}}return g}}({{
    connectionString: "{connection_string}",
    enableAutoRouteTracking: true,
    enableCorsCorrelation: true,
    enableRequestHeaderTracking: true,
    enableResponseHeaderTracking: true,
    enableAjaxPerfTracking: true,
    maxBatchInterval: 15000,
    disableFetchTracking: false,
    disableExceptionTracking: false,
    autoTrackPageVisitTime: true
}}),w=v[t];w.queue&&0===w.queue.length&&w.trackPageView({{}}),function(){{if(v[t])try{{{user_context_js}
    }}catch(e){{}}}}()}}(window,document,{{
    src: "https://js.monitor.azure.com/scripts/b/ai.3.gbl.min.js",
    crossOrigin: "anonymous",
    ld: -1,
    name: "appInsights"
}});
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
