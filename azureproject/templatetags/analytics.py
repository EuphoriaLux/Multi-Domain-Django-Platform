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

import json
from decimal import Decimal
from urllib.parse import unquote

from django import template
from django.middleware.csp import get_nonce
from django.utils.dateparse import parse_datetime
from django.utils.safestring import mark_safe

register = template.Library()


def _json_default(value):
    """JSON serializer fallback for analytics params.

    Model fields like ``DecimalField`` (e.g. event ``registration_fee``) are
    common ``ga4_event``/``fb_event`` params but aren't JSON-serializable.
    Convert Decimal to float so GA4/Pixel receive a numeric ``value``; fall
    back to str for anything else exotic.
    """
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


BANNER_COOKIE = "cookie_consent"
FLAG_ACCEPT = "accept"
FLAG_DECLINE = "decline"


def _cookie_group_version(cookie_group):
    """
    django-cookie-consent's current version of a group, or None if unknown here.

    The library dates a group by its newest cookie (``CookieGroup.get_version``,
    "" while the group has no cookies) and treats an acceptance recorded before
    that date as undecided, so adding a cookie to a group asks everyone again.
    None (no such group, or the lookup failed) means no version to check against.
    """
    try:
        from cookie_consent.cache import get_cookie_group

        group = get_cookie_group(cookie_group)
    except Exception:
        return None
    return group.get_version() if group is not None else None


def _stamp_is_current(stamp, reference):
    """
    Whether an acceptance dated ``stamp`` still covers a group at ``reference``.

    The library's own rule: the acceptance stands until a cookie is added to
    the group after it. No reference (the group has no cookies, or is unknown
    here) leaves nothing to renew; an acceptance with no date at all (a flag
    written before flags carried one) is older than any cookie.
    Both sides are ISO 8601 but not the same shape (the banner's script writes
    ``...123Z``, the library ``...123456+00:00``), so compare parsed datetimes
    and fall back to the strings only when one side does not parse.
    """
    if not reference:
        return True
    if not stamp:
        return False
    try:
        stamped, current = parse_datetime(stamp), parse_datetime(reference)
    except (TypeError, ValueError):
        stamped = current = None
    if stamped is not None and current is not None:
        try:
            return stamped >= current
        except TypeError:  # naive against aware
            pass
    return stamp >= reference


def stored_cookie_choice(request, cookie_group):
    """
    The visitor's stored choice for a cookie group: True, False or None.

    A choice can live in three places, checked in this order:
    1. the banner's per-group flag ``cookie_consent_<group>=accept:<version>``
       (``decline`` for a refusal; flags written before this carry a bare
       ``accept``);
    2. the banner's JSON object in the ``cookie_consent`` cookie
       (``{"analytics": true, "marketing": false, "timestamp": ...}``);
    3. django-cookie-consent's own cookie (``group=version|...``, HttpOnly),
       written by its /cookies/ views.
    The banner writes 1 and 2 on every save and also posts the choice to the
    library so 3 follows; when that post did not complete (navigation right
    after the save, a network error) the banner's copy is the newer one, so
    it wins. 3 alone is what a visitor who only used the library's own
    /cookies/ pages has.

    An acceptance only counts while it is current for the group
    (_stamp_is_current): the flag carries the group version it was given
    under, the JSON its own date. A stale acceptance is skipped, not turned
    into a refusal: the next source is consulted, and when every source is
    stale the visitor is undecided and the banner asks again, which is what
    the library does with its own cookie. A refusal never goes stale.
    """
    reference, looked_up = None, False

    flag = request.COOKIES.get(f"cookie_consent_{cookie_group}", "")
    action, _, stamp = flag.partition(":")
    if action == FLAG_DECLINE:
        return False
    if action == FLAG_ACCEPT:
        reference, looked_up = _cookie_group_version(cookie_group), True
        if _stamp_is_current(unquote(stamp), reference):
            return True

    raw = request.COOKIES.get(BANNER_COOKIE, "")
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict) and cookie_group in data:
            if data[cookie_group] is not True:
                return False
            if not looked_up:
                reference = _cookie_group_version(cookie_group)
            stamp = data.get("timestamp")
            if _stamp_is_current(stamp if isinstance(stamp, str) else "", reference):
                return True

    try:
        from cookie_consent.util import get_cookie_value_from_request
        consent = get_cookie_value_from_request(request, cookie_group)
    except Exception:
        consent = None
    if consent is not None:
        return consent is True
    return None


def get_cookie_consent(request, cookie_group, undecided=True):
    """
    Check if user has consented to a specific cookie group.

    Returns True if the group is accepted and False if it was declined.
    ``undecided`` is the answer while no choice is stored yet (first visit,
    the banner is showing). Every tag in this module passes False: the GA4
    Consent Mode defaults, the Facebook Pixel and Application Insights all
    wait for the banner's answer. The True default is kept for a caller that
    only wants to know a group was not refused.
    """
    choice = stored_cookie_choice(request, cookie_group)
    return undecided if choice is None else choice


@register.simple_tag(takes_context=True)
def cookie_consent_state(context):
    """
    The stored choice as the server sees it, as JSON for the cookie banner.

    django-cookie-consent's cookie is HttpOnly, so the banner's script cannot
    read a choice made through the library's /cookies/ views from
    document.cookie; it reads this instead (``data-consent-state``). This is
    also version-checked (stored_cookie_choice) where a readable cookie is
    not, so the script takes it as the truth whenever it is present and only
    falls back to document.cookie when the page was rendered without a
    request (empty output). ``versions`` carries the groups' current versions
    for the flags a save on this page writes.
    """
    request = context.get('request')
    if request is None:
        return ""  # rendered without a request: the server has no view to offer
    state = {"analytics": None, "marketing": None}
    for group in state:
        state[group] = stored_cookie_choice(request, group)
    # Decided only when every optional group holds a choice. A group whose
    # acceptance went stale (a cookie was added to it) is None here while the
    # other may still be current: the banner must ask for that group again,
    # and the modal keeps showing the other group's choice.
    state["decided"] = all(value is not None for value in state.values())
    state["versions"] = {
        group: _cookie_group_version(group) or "" for group in ("analytics", "marketing")
    }
    return json.dumps(state)


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
    nonce = get_nonce(request) if request else None
    # `is not None`, not truthiness: request._csp_nonce is a django.utils.csp.LazyNonce
    # whose __bool__ is False until the value has actually been generated. analytics_head
    # renders first in <head>, before any {{ csp_nonce }}, so a truthiness test always
    # saw False and emitted the gtag scripts with no nonce at all. Harmless while the
    # policy is report-only; the moment SECURE_CSP_REPORT_ONLY becomes SECURE_CSP those
    # scripts get blocked (CSP3 ignores 'unsafe-inline' once a nonce is present) and
    # analytics goes dark. Interpolating the nonce below is what forces generation.
    nonce_attr = f' nonce="{nonce}"' if nonce is not None else ''

    # The stored choice as the server sees it (stored_cookie_choice: the
    # banner's flag, then its JSON, then the library's HttpOnly cookie, each
    # checked against the group's version). Only a current acceptance grants;
    # undecided, or no request, is denied until the banner answers. Reading
    # the library's cookie alone here would let a stale acceptance in it
    # outrank a newer refusal the banner recorded while its post to the
    # library was lost, and gtag('config') would send the page view.
    def granted(group):
        if request is None:
            return 'denied'
        return 'granted' if get_cookie_consent(request, group, undecided=False) else 'denied'

    analytics_granted = granted('analytics')
    marketing_granted = granted('marketing')

    # Get current language for multi-language tracking
    # This allows GA4 to track page views with language context
    language_code = context.get('LANGUAGE_CODE', 'en')

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
<script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"{nonce_attr}></script>
<script{nonce_attr}>
  gtag('js', new Date());
  gtag('config', '{ga4_id}', {{
    // Custom dimension for language tracking (prevents traffic split across /en/, /de/, /fr/ URLs)
    'custom_map': {{'dimension1': 'content_language'}},
    'content_language': '{language_code}'
  }});
</script>'''

    return mark_safe(script)


@register.simple_tag(takes_context=True)
def analytics_body(context):
    """
    Render Facebook Pixel script after <body> opening tag.

    Only loads once the visitor has accepted marketing cookies. An undecided
    visitor (no consent cookie yet) gets the placeholder that waits for the
    banner's cookie_consent_updated event: the Pixel has no consent mode of
    its own, so emitting it earlier would fire PageView before any choice.
    This tag should be placed right after the opening <body> tag.
    """
    fb_pixel_id = context.get('FACEBOOK_PIXEL_ID')

    if not fb_pixel_id:
        return ''

    request = context.get('request')
    has_marketing_consent = (
        get_cookie_consent(request, 'marketing', undecided=False) if request else False
    )

    # Get CSP nonce from request (if available)
    nonce = get_nonce(request) if request else None
    # `is not None`: LazyNonce is falsy until generated — see analytics_head above.
    nonce_attr = f' nonce="{nonce}"' if nonce is not None else ''

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
    nonce = get_nonce(request) if request else None
    # `is not None`: LazyNonce is falsy until generated — see analytics_head above.
    nonce_attr = f' nonce="{nonce}"' if nonce is not None else ''

    # Build params object — use json.dumps for safe JS serialization (prevents XSS)
    if params:
        params_json = json.dumps(params, default=_json_default)
        script = f"<script{nonce_attr}>gtag('event', {json.dumps(event_name)}, {params_json});</script>"
    else:
        script = f"<script{nonce_attr}>gtag('event', {json.dumps(event_name)});</script>"

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
    nonce = get_nonce(request) if request else None
    # `is not None`: LazyNonce is falsy until generated — see analytics_head above.
    nonce_attr = f' nonce="{nonce}"' if nonce is not None else ''

    # Build params object — use json.dumps for safe JS serialization (prevents XSS)
    if params:
        params_json = json.dumps(params, default=_json_default)
        script = f"<script{nonce_attr}>if(window.fbq)fbq('track', {json.dumps(event_name)}, {params_json});</script>"
    else:
        script = f"<script{nonce_attr}>if(window.fbq)fbq('track', {json.dumps(event_name)});</script>"

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
    nonce = get_nonce(request) if request else None
    # `is not None`: LazyNonce is falsy until generated — see analytics_head above.
    nonce_attr = f' nonce="{nonce}"' if nonce is not None else ''

    # Browser telemetry is an analytics cookie category: the SDK loads only
    # once the visitor accepted analytics. Until then a placeholder waits for
    # the banner's cookie_consent_updated event (no preconnect either: the
    # hint alone opens a connection to Microsoft).
    has_analytics_consent = (
        get_cookie_consent(request, 'analytics', undecided=False) if request else False
    )

    # Get user ID for authenticated user tracking (anonymous if not logged in)
    user_id = ''
    if request and hasattr(request, 'user') and request.user.is_authenticated:
        # Use hashed user ID for privacy (don't expose actual user IDs)
        import hashlib
        user_id = hashlib.sha256(str(request.user.id).encode()).hexdigest()[:16]

    # Application Insights JavaScript SDK v3 - Official Snippet Pattern
    # See: https://learn.microsoft.com/en-us/azure/azure-monitor/app/javascript-sdk
    # The onInit callback is used to set authenticated user context after SDK loads
    user_init_js = f'sdk.setAuthenticatedUserContext("{user_id}");' if user_id else ''

    snippet = f'''!(function (cfg){{function e(){{cfg.onInit&&cfg.onInit(n)}}var x,w,D,t,E,n,C=window,O=document,b=C.location,q="script",I="ingestionendpoint",L="disableExceptionTracking",j="ai.device.";"instrumentationKey"[x="toLowerCase"](),w="crossOrigin",D="POST",t="appInsightsSDK",E=cfg.name||"appInsights",(cfg.name||C[t])&&(C[t]=E),n=C[E]||function(g){{var f=!1,m=!1,h={{initialize:!0,queue:[],sv:"8",version:2,config:g}};function v(e,t){{var n={{}},i="Browser";function a(e){{e=""+e;return 1===e.length?"0"+e:e}}return n[j+"id"]=i[x](),n[j+"type"]=i,n["ai.operation.name"]=b&&b.pathname||"_unknown_",n["ai.internal.sdkVersion"]="javascript:snippet_"+(h.sv||h.version),{{time:(i=new Date).getUTCFullYear()+"-"+a(1+i.getUTCMonth())+"-"+a(i.getUTCDate())+"T"+a(i.getUTCHours())+":"+a(i.getUTCMinutes())+":"+a(i.getUTCSeconds())+"."+(i.getUTCMilliseconds()/1e3).toFixed(3).slice(2,5)+"Z",iKey:e,name:"Microsoft.ApplicationInsights."+e.replace(/-/g,"")+"."+t,sampleRate:100,tags:n,data:{{baseData:{{ver:2}}}},ver:undefined,seq:"1",aiDataContract:undefined}}}}var n,i,t,a,y=-1,T=0,S=["js.monitor.azure.com","js.cdn.applicationinsights.io","js.cdn.monitor.azure.com","js0.cdn.applicationinsights.io","js0.cdn.monitor.azure.com","js2.cdn.applicationinsights.io","js2.cdn.monitor.azure.com","az416426.vo.msecnd.net"],o=g.url||cfg.src,r=function(){{return s(o,null)}};function s(d,t){{if((n=navigator)&&(~(n=(n.userAgent||"").toLowerCase()).indexOf("msie")||~n.indexOf("trident/"))&&~d.indexOf("ai.3")&&(d=d.replace(/(\\/)(ai\\.3\\.)([^\\d]*)$/,function(e,t,n){{return t+"ai.2"+n}})),!1!==cfg.cr)for(var e=0;e<S.length;e++)if(0<d.indexOf(S[e])){{y=e;break}}var n,i=function(e){{var a,t,n,i,o,r,s,c,u,l;h.queue=[],m||(0<=y&&T+1<S.length?(a=(y+T+1)%S.length,p(d.replace(/^(.*\\/\\/)([\\w\\.]*)(\\/.*)\\$/,function(e,t,n,i){{return t+S[a]+i}})),T+=1):(f=m=!0,s=d,cfg.dle||!1))}},a=function(e,t){{m||setTimeout(function(){{!t&&h.core||i()}},500),f=!1}},p=function(e){{var n=O.createElement(q),e=(n.src=e,t&&(n.integrity=t),n.setAttribute("data-ai-name",E),cfg[w]);return!e&&""!==e||"undefined"==n[w]||(n[w]=e),n.onload=a,n.onerror=i,n.onreadystatechange=function(e,t){{"loaded"!==n.readyState&&"complete"!==n.readyState||a(0,t)}},cfg.ld&&cfg.ld<0?O.getElementsByTagName("head")[0].appendChild(n):setTimeout(function(){{O.getElementsByTagName(q)[0].parentNode.appendChild(n)}},cfg.ld||0),n}};p(d)}}cfg.sri&&(n=o.match(/^((http[s]?:\\/\\/.*\\/)\\w+(\\.\\d+){{1,5}})\\.(([\\w]+\\.){{0,2}}js)$/))&&6===n.length?(d="".concat(n[1],".integrity.json"),i="@".concat(n[4]),l=window.fetch,t=function(e){{if(!e.ext||!e.ext[i]||!e.ext[i].file)throw Error("Error Loading JSON response");var t=e.ext[i].integrity||null;s(o=n[2]+e.ext[i].file,t)}},l&&!cfg.useXhr?l(d,{{method:"GET",mode:"cors"}}).then(function(e){{return e.json()["catch"](function(){{return{{}}}})}} ).then(t)["catch"](r):XMLHttpRequest&&((a=new XMLHttpRequest).open("GET",d),a.onreadystatechange=function(){{if(a.readyState===XMLHttpRequest.DONE)if(200===a.status)try{{t(JSON.parse(a.responseText))}}catch(e){{r()}}else r()}},a.send())):o&&r();try{{h.cookie=O.cookie}}catch(k){{}}function e(e){{for(;e.length;)!function(t){{h[t]=function(){{var e=arguments;f||h.queue.push(function(){{h[t].apply(h,e)}})}}}}(e.pop())}}var c,u,l="track",d="TrackPage",p="TrackEvent",l=(e([l+"Event",l+"PageView",l+"Exception",l+"Trace",l+"DependencyData",l+"Metric",l+"PageViewPerformance","start"+d,"stop"+d,"start"+p,"stop"+p,"addTelemetryInitializer","setAuthenticatedUserContext","clearAuthenticatedUserContext","flush"]),h.SeverityLevel={{Verbose:0,Information:1,Warning:2,Error:3,Critical:4}},(g.extensionConfig||{{}}).ApplicationInsightsAnalytics||{{}});return!0!==g[L]&&!0!==l[L]&&(e(["_"+(c="onerror")]),u=C[c],C[c]=function(e,t,n,i,a){{var o=u&&u(e,t,n,i,a);return!0!==o&&h["_"+c]({{message:e,url:t,lineNumber:n,columnNumber:i,error:a,evt:C.event}}),o}},g.autoExceptionInstrumented=!0),h}}(cfg.cfg),(C[E]=n).queue&&0===n.queue.length?(n.queue.push(e),n.trackPageView({{}})):e();}})( {{
  src: "https://js.monitor.azure.com/scripts/b/ai.3.gbl.min.js",
  crossOrigin: "anonymous",
  dle: true,
  onInit: function(sdk) {{ {user_init_js} }},
  cfg: {{
    connectionString: "{connection_string}",
    enableAutoRouteTracking: true,
    enableCorsCorrelation: true,
    autoTrackPageVisitTime: true,
    disablePageUnloadEvents: ["unload"]
  }}
}});'''

    if not has_analytics_consent:
        return mark_safe(f'''<!-- Azure Application Insights (waiting for analytics consent) -->
<script type="text/javascript"{nonce_attr}>
(function () {{
  function flushPendingEvents() {{
    var pending = window.__appInsightsPendingEvents;
    if (!window.appInsights || typeof window.appInsights.trackEvent !== 'function' || !pending) return;
    while (pending.length) {{
      window.appInsights.trackEvent(pending.shift());
    }}
  }}
  function load() {{
    if (window.appInsights) {{
      flushPendingEvents();
      return;
    }}
    {snippet}
    // The SDK snippet installs its queueing stub synchronously. Hand it the
    // events rendered earlier in this page; the SDK drains its own queue once
    // the external script finishes loading.
    flushPendingEvents();
  }}
  document.addEventListener('cookie_consent_updated', function (e) {{
    if (e.detail && e.detail.analytics === true) load();
  }});
}})();
</script>''')

    script = f'''<!-- Azure Application Insights Browser SDK v3 -->
<link rel="preconnect" href="https://js.monitor.azure.com" crossorigin>
<script type="text/javascript"{nonce_attr}>
{snippet}
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
    nonce = get_nonce(request) if request else None
    # `is not None`: LazyNonce is falsy until generated — see analytics_head above.
    nonce_attr = f' nonce="{nonce}"' if nonce is not None else ''

    # Build properties object — use json.dumps for safe JS serialization (prevents XSS)
    if params:
        props_json = json.dumps(params)
        event_json = f'{{name: {json.dumps(event_name)}, properties: {props_json}}}'
    else:
        event_json = f'{{name: {json.dumps(event_name)}}}'

    # A page event can be rendered before the visitor grants analytics. Keep
    # it in memory until the consent-driven head placeholder creates the SDK
    # queueing stub, then replay it through trackEvent after consent.
    script = f'''<script{nonce_attr}>(function (event) {{
  if (window.appInsights && typeof window.appInsights.trackEvent === 'function') {{
    window.appInsights.trackEvent(event);
    return;
  }}
  window.__appInsightsPendingEvents = window.__appInsightsPendingEvents || [];
  window.__appInsightsPendingEvents.push(event);
}})({event_json});</script>'''

    return mark_safe(script)
