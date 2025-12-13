"""
Custom template tags for multi-domain analytics (GA4 and Facebook Pixel).

Usage in templates:
    {% load analytics %}

    In <head>:
        {% analytics_head %}

    After <body>:
        {% analytics_body %}

The analytics IDs are provided by the analytics_context.analytics_ids context processor
which sets GOOGLE_ANALYTICS_GTAG_PROPERTY_ID and FACEBOOK_PIXEL_ID based on domain.
"""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def analytics_head(context):
    """
    Render GA4 gtag.js script in the <head> section.

    This tag should be placed near the top of <head> for best performance.
    """
    ga4_id = context.get('GOOGLE_ANALYTICS_GTAG_PROPERTY_ID')

    if not ga4_id:
        return ''

    # GA4 gtag.js implementation with consent mode
    script = f'''<!-- Google Analytics 4 -->
<script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', '{ga4_id}');
</script>'''

    return mark_safe(script)


@register.simple_tag(takes_context=True)
def analytics_body(context):
    """
    Render Facebook Pixel script after <body> opening tag.

    This tag should be placed right after the opening <body> tag.
    """
    fb_pixel_id = context.get('FACEBOOK_PIXEL_ID')

    if not fb_pixel_id:
        return ''

    # Facebook Pixel implementation
    script = f'''<!-- Facebook Pixel -->
<script>
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

    # Build params object
    if params:
        params_str = ', '.join(f"'{k}': '{v}'" for k, v in params.items())
        script = f"<script>gtag('event', '{event_name}', {{{params_str}}});</script>"
    else:
        script = f"<script>gtag('event', '{event_name}');</script>"

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

    # Build params object
    if params:
        params_str = ', '.join(f"'{k}': '{v}'" for k, v in params.items())
        script = f"<script>fbq('track', '{event_name}', {{{params_str}}});</script>"
    else:
        script = f"<script>fbq('track', '{event_name}');</script>"

    return mark_safe(script)
