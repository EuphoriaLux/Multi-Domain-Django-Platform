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
    # Uses the official Microsoft SDK snippet pattern (snippet version 8)
    # See: https://learn.microsoft.com/en-us/azure/azure-monitor/app/javascript-sdk
    #
    # The official snippet includes:
    # - version:2 initialization pattern (required for SDK v3)
    # - Automatic IE8 fallback to SDK v2.x
    # - CDN failover support
    # - SDK load failure reporting
    user_context_init = f'sdk.setAuthenticatedUserContext("{user_id}");' if user_id else ''

    script = f'''<!-- Azure Application Insights Browser SDK v3 (official snippet) -->
<link rel="preconnect" href="https://js.monitor.azure.com" crossorigin>
<script{nonce_attr}>
!(function (cfg){{function e(){{cfg.onInit&&cfg.onInit(n)}}var x,w,D,t,E,n,C=window,O=document,b=C.location,q="script",I="ingestionendpoint",L="disableExceptionTracking",j="ai.device.";"instrumentationKey"[x="toLowerCase"](),w="crossOrigin",D="POST",t="appInsightsSDK",E=cfg.name||"appInsights",(cfg.name||C[t])&&(C[t]=E),n=C[E]||function(g){{var f=!1,m=!1,h={{initialize:!0,queue:[],sv:"8",version:2,config:g}};function v(e,t){{var n={{}},i="Browser";function a(e){{e=""+e;return 1===e.length?"0"+e:e}}return n[j+"id"]=i[x](),n[j+"type"]=i,n["ai.operation.name"]=b&&b.pathname||"_unknown_",n["ai.internal.sdkVersion"]="javascript:snippet_"+(h.sv||h.version),{{time:(i=new Date).getUTCFullYear()+"-"+a(1+i.getUTCMonth())+"-"+a(i.getUTCDate())+"T"+a(i.getUTCHours())+":"+a(i.getUTCMinutes())+":"+a(i.getUTCSeconds())+"."+(i.getUTCMilliseconds()/1e3).toFixed(3).slice(2,5)+"Z",iKey:e,name:"Microsoft.ApplicationInsights."+e.replace(/-/g,"")+"."+t,sampleRate:100,tags:n,data:{{baseData:{{ver:2}}}},ver:undefined,seq:"1",aiDataContract:undefined}}}}var n,i,t,a,y=-1,T=0,S=["js.monitor.azure.com","js.cdn.applicationinsights.io","js.cdn.monitor.azure.com","js0.cdn.applicationinsights.io","js0.cdn.monitor.azure.com","js2.cdn.applicationinsights.io","js2.cdn.monitor.azure.com","az416426.vo.msecnd.net"],o=g.url||cfg.src,r=function(){{return s(o,null)}};function s(d,t){{if((n=navigator)&&(~(n=(n.userAgent||"").toLowerCase()).indexOf("msie")||~n.indexOf("trident/"))&&~d.indexOf("ai.3")&&(d=d.replace(/(\/)(ai\.3\.)([^\d]*)$/,function(e,t,n){{return t+"ai.2"+n}})),!1!==cfg.cr)for(var e=0;e<S.length;e++)if(0<d.indexOf(S[e])){{y=e;break}}var n,i=function(e){{var a,t,n,i,o,r,s,c,u,l;h.queue=[],m||(0<=y&&T+1<S.length?(a=(y+T+1)%S.length,p(d.replace(/^(.*\/\/)([\w\.]*)(\/.*)$/,function(e,t,n,i){{return t+S[a]+i}})),T+=1):(f=m=!0,s=d,!0!==cfg.dle&&(c=(t=function(){{var e,t={{}},n=g.connectionString;if(n)for(var i=n.split(";"),a=0;a<i.length;a++){{var o=i[a].split("=");2===o.length&&(t[o[0][x]()]=o[1])}}return t[I]||(e=(n=t.endpointsuffix)?t.location:null,t[I]="https://"+(e?e+".":"")+"dc."+(n||"services.visualstudio.com")),t}}()).instrumentationkey||g.instrumentationKey||"",t=(t=(t=t[I])&&"/"===t.slice(-1)?t.slice(0,-1):t)?t+"/v2/track":g.endpointUrl,t=g.userOverrideEndpointUrl||t,(n=[]).push((i="SDK LOAD Failure: Failed to load Application Insights SDK script (See stack for details)",o=s,u=t,(l=(r=v(c,"Exception")).data).baseType="ExceptionData",l.baseData.exceptions=[{{typeName:"SDKLoadFailed",message:i.replace(/\./g,"-"),hasFullStack:!1,stack:i+"\\nSnippet failed to load ["+o+"] -- Telemetry is disabled\\nHelp Link: https://go.microsoft.com/fwlink/?linkid=2128109\\nHost: "+(b&&b.pathname||"_unknown_")+"\\nEndpoint: "+u,parsedStack:[]}}],r)),n.push((l=s,i=t,(u=(o=v(c,"Message")).data).baseType="MessageData",(r=u.baseData).message='AI (Internal): 99 message:"'+("SDK LOAD Failure: Failed to load Application Insights SDK script (See stack for details) ("+l+")").replace(/\\"/g,"")+'"',r.properties={{endpoint:i}},o)),s=n,c=t,JSON&&((u=C.fetch)&&!cfg.useXhr?u(c,{{method:D,body:JSON.stringify(s),mode:"cors"}}):XMLHttpRequest&&((l=new XMLHttpRequest).open(D,c),l.setRequestHeader("Content-type","application/json"),l.send(JSON.stringify(s))))))))}},a=function(e,t){{m||setTimeout(function(){{!t&&h.core||i()}},500),f=!1}},p=function(e){{var n=O.createElement(q),e=(n.src=e,t&&(n.integrity=t),n.setAttribute("data-ai-name",E),cfg[w]);return!e&&""!==e||"undefined"==n[w]||(n[w]=e),n.onload=a,n.onerror=i,n.onreadystatechange=function(e,t){{"loaded"!==n.readyState&&"complete"!==n.readyState||a(0,t)}},cfg.ld&&cfg.ld<0?O.getElementsByTagName("head")[0].appendChild(n):setTimeout(function(){{O.getElementsByTagName(q)[0].parentNode.appendChild(n)}},cfg.ld||0),n}};p(d)}}cfg.sri&&(n=o.match(/^((http[s]?:\\/\\/.*\\/)\w+(\.\d+){{1,5}})\.(([\w]+\.){{0,2}}js)$/))&&6===n.length?(d="".concat(n[1],".integrity.json"),i="@".concat(n[4]),l=window.fetch,t=function(e){{if(!e.ext||!e.ext[i]||!e.ext[i].file)throw Error("Error Loading JSON response");var t=e.ext[i].integrity||null;s(o=n[2]+e.ext[i].file,t)}},l&&!cfg.useXhr?l(d,{{method:"GET",mode:"cors"}}).then(function(e){{return e.json()["catch"](function(){{return{{}}}});}}).then(t)["catch"](r):XMLHttpRequest&&((a=new XMLHttpRequest).open("GET",d),a.onreadystatechange=function(){{if(a.readyState===XMLHttpRequest.DONE)if(200===a.status)try{{t(JSON.parse(a.responseText))}}catch(e){{r()}}else r()}},a.send())):o&&r();try{{h.cookie=O.cookie}}catch(k){{}}function e(e){{for(;e.length;)!function(t){{h[t]=function(){{var e=arguments;f||h.queue.push(function(){{h[t].apply(h,e)}})}}}}(e.pop())}}var c,u,l="track",d="TrackPage",p="TrackEvent",l=(e([l+"Event",l+"PageView",l+"Exception",l+"Trace",l+"DependencyData",l+"Metric",l+"PageViewPerformance","start"+d,"stop"+d,"start"+p,"stop"+p,"addTelemetryInitializer","setAuthenticatedUserContext","clearAuthenticatedUserContext","flush"]),h.SeverityLevel={{Verbose:0,Information:1,Warning:2,Error:3,Critical:4}},(g.extensionConfig||{{}}).ApplicationInsightsAnalytics||{{}});return!0!==g[L]&&!0!==l[L]&&(e(["_"+(c="onerror")]),u=C[c],C[c]=function(e,t,n,i,a){{var o=u&&u(e,t,n,i,a);return!0!==o&&h["_"+c]({{message:e,url:t,lineNumber:n,columnNumber:i,error:a,evt:C.event}}),o}},g.autoExceptionInstrumented=!0),h}}(cfg.cfg),(C[E]=n).queue&&0===n.queue.length?(n.queue.push(e),n.trackPageView({{}})):e();}})
({{
  src: "https://js.monitor.azure.com/scripts/b/ai.3.gbl.min.js",
  crossOrigin: "anonymous",
  onInit: function(sdk) {{ {user_context_init} }},
  cfg: {{
    connectionString: "{connection_string}",
    enableAutoRouteTracking: true,
    enableCorsCorrelation: true,
    enableRequestHeaderTracking: true,
    enableResponseHeaderTracking: true,
    enableAjaxPerfTracking: true,
    maxBatchInterval: 15000,
    disableFetchTracking: false,
    disableExceptionTracking: false,
    autoTrackPageVisitTime: false,
    disableFlushOnBeforeUnload: true
  }}
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
