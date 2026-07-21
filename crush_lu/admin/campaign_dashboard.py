"""
Campaign & Remarketing Dashboard for the Crush.lu Coach Panel.

Unified multi-channel campaign management: one place to compose a campaign
(segment → channels → content → preview/estimate → send now or schedule),
watch it send, and read per-channel delivery + click results. Sibling of
``crush_admin_dashboard`` / ``profile_reminders_panel`` — function views
mounted in ``azureproject/urls_crush.py`` before the ``crush-admin/`` site,
guarded by the shared coach-or-superuser check.
"""
import logging
from datetime import timedelta, timezone as dt_timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F
from django.db.models.functions import TruncDay, TruncWeek
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from crush_lu.admin_views import _check_admin_access
from crush_lu.admin.user_segments import get_segment_definitions
from crush_lu.models import (
    Campaign,
    CampaignClick,
    CampaignRecipient,
    ProfileReminder,
)
from crush_lu.models.newsletter import Newsletter, NewsletterRecipient
from crush_lu.services import campaigns as campaign_service
from hub.models import WhatsAppInboundMessage, WhatsAppMessage
from hub.whatsapp_service import TemplatesFetchError, fetch_approved_templates

logger = logging.getLogger(__name__)

LANGUAGE_CHOICES = [
    ('all', 'All languages'),
    ('en', 'English'),
    ('de', 'German'),
    ('fr', 'French'),
]

AUDIENCE_CHOICES = Newsletter.AUDIENCE_CHOICES

# Number of key/value parameter slots offered by the WhatsApp composer form.
WHATSAPP_PARAM_SLOTS = 5


def _segment_options():
    """Flat [(key, label, count)] list for the composer's segment picker."""
    options = []
    for group in get_segment_definitions().values():
        for segment in group.get('segments', []):
            options.append({
                'key': segment['key'],
                'name': segment['name'],
                'group': group['title'],
                'count': segment['count'],
                'description': segment.get('description', ''),
            })
    return options


def _safe_template_list():
    try:
        return fetch_approved_templates(), None
    except TemplatesFetchError as exc:
        return [], exc.detail


@login_required
def campaign_dashboard(request):
    """Main tabbed dashboard: Overview / Campaigns / WhatsApp / Reminders / Segments."""
    error = _check_admin_access(request)
    if error:
        return error

    campaigns = list(
        Campaign.objects.select_related('created_by')
        .order_by('-created_at')[:20]
    )
    campaign_rows = [
        {'campaign': campaign, 'stats': campaign.stats}
        for campaign in campaigns
    ]

    completed = Campaign.objects.filter(status__in=['sent', 'partial'])
    active = Campaign.objects.filter(status__in=['scheduled', 'sending'])

    email_sent = NewsletterRecipient.objects.filter(
        status='sent', newsletter__campaign__isnull=False,
    ).count()
    channel_sent = dict(
        CampaignRecipient.objects.filter(status='sent')
        .values_list('channel')
        .annotate(n=Count('id'))
        .values_list('channel', 'n')
    )
    total_clicks = CampaignClick.objects.count()
    messages_sent = (
        email_sent
        + channel_sent.get('whatsapp', 0)
        + channel_sent.get('push', 0)
    )
    click_rate = (
        round(total_clicks / messages_sent * 100, 1) if messages_sent else 0
    )

    # WhatsApp tab — all outreach (campaign and CRM), matching the hub inbox.
    whatsapp_funnel = dict(
        WhatsAppMessage.objects.values_list('status')
        .annotate(n=Count('id'))
        .values_list('status', 'n')
    )
    whatsapp_outbound = (
        WhatsAppMessage.objects.select_related('user')
        .order_by('-created_at')[:20]
    )
    whatsapp_inbound = WhatsAppInboundMessage.objects.order_by(
        '-received_at'
    )[:10]
    whatsapp_unread = WhatsAppInboundMessage.objects.filter(
        is_read=False
    ).count()

    # Reminders tab
    reminder_counts = dict(
        ProfileReminder.objects.values_list('reminder_type')
        .annotate(n=Count('id'))
        .values_list('reminder_type', 'n')
    )
    reminded_users = ProfileReminder.objects.values('user').distinct().count()
    converted_users = (
        ProfileReminder.objects.filter(
            user__crushprofile__verification_status='verified',
            user__crushprofile__approved_at__gt=F('sent_at'),
        )
        .values('user')
        .distinct()
        .count()
    )
    reminder_conversion = (
        round(converted_users / reminded_users * 100, 1)
        if reminded_users else 0
    )
    recent_reminders = (
        ProfileReminder.objects.select_related('user', 'user__crushprofile')
        .order_by('-sent_at')[:20]
    )

    context = {
        'title': 'Campaign Dashboard',
        'campaign_rows': campaign_rows,
        'overview': {
            'campaigns_completed': completed.count(),
            'campaigns_active': active.count(),
            'email_sent': email_sent,
            'whatsapp_sent': channel_sent.get('whatsapp', 0),
            'push_sent': channel_sent.get('push', 0),
            'messages_sent': messages_sent,
            'total_clicks': total_clicks,
            'click_rate': click_rate,
        },
        'whatsapp': {
            'funnel': {
                status: whatsapp_funnel.get(status, 0)
                for status in ('queued', 'sent', 'delivered', 'read', 'failed')
            },
            'total': sum(whatsapp_funnel.values()),
            'outbound': whatsapp_outbound,
            'inbound': whatsapp_inbound,
            'unread': whatsapp_unread,
        },
        'reminders': {
            'counts': {
                '24h': reminder_counts.get('24h', 0),
                '72h': reminder_counts.get('72h', 0),
                '7d': reminder_counts.get('7d', 0),
            },
            'total': sum(reminder_counts.values()),
            'reminded_users': reminded_users,
            'converted_users': converted_users,
            'conversion': reminder_conversion,
            'recent': recent_reminders,
        },
        'segment_groups': get_segment_definitions(),
    }
    return render(
        request, 'admin/crush_lu/campaign_dashboard.html', context,
    )


@login_required
def campaign_composer(request):
    """Campaign creation wizard (segment → channels → content → review)."""
    error = _check_admin_access(request)
    if error:
        return error

    templates, template_error = _safe_template_list()
    # Only APPROVED variants are pickable — Meta rejects sends for DRAFT/
    # PENDING/REJECTED templates, so offering them would create campaigns
    # that fail for every recipient. (The preview still shows all variant
    # statuses for the chosen name, as diagnostics.)
    approved_templates = [
        t for t in templates if t.get('status') == 'APPROVED'
    ]
    context = {
        'title': 'New Campaign',
        'segment_options': _segment_options(),
        'audience_choices': AUDIENCE_CHOICES,
        'language_choices': LANGUAGE_CHOICES,
        'whatsapp_templates': approved_templates,
        'whatsapp_template_error': template_error,
        'whatsapp_param_slots': range(1, WHATSAPP_PARAM_SLOTS + 1),
        'preselected_segment': request.GET.get('segment', ''),
    }
    return render(
        request, 'admin/crush_lu/campaign_composer.html', context,
    )


def _validate_whatsapp_template(template_name, language):
    """Server-side re-check that the chosen template is actually approved.

    The composer only *renders* approved templates, but a stale page or a
    modified form can still submit a DRAFT/PENDING/REJECTED name — which the
    dispatcher would then attempt for every eligible recipient.
    """
    try:
        # Uncached: a template Meta just rejected must not slip through on a
        # stale 10-minute cache entry at creation time.
        templates = fetch_approved_templates(use_cache=False)
    except TemplatesFetchError as exc:
        raise ValueError(
            f"Could not verify the WhatsApp template with Meta "
            f"({exc.detail}) — try again in a moment."
        )
    approved_languages = [
        t['language'] for t in templates
        if t['name'] == template_name and t.get('status') == 'APPROVED'
    ]
    if not approved_languages:
        raise ValueError(
            f'WhatsApp template "{template_name}" has no approved variant.'
        )
    if language != 'all' and language not in approved_languages:
        raise ValueError(
            f'WhatsApp template "{template_name}" has no approved '
            f'"{language}" variant, but the campaign targets that language.'
        )


def _parse_composer_form(request):
    """Extract create_campaign kwargs from the composer POST. Raises ValueError."""
    name = (request.POST.get('name') or '').strip()
    if not name:
        raise ValueError("Give the campaign a name.")

    channels = request.POST.getlist('channels')
    if not channels:
        raise ValueError("Pick at least one channel.")

    audience = request.POST.get('audience') or 'segment'
    segment_key = (request.POST.get('segment_key') or '').strip()
    if audience == 'segment' and not segment_key:
        raise ValueError("Pick a segment (or a different audience).")
    language = request.POST.get('language') or 'all'

    email_content = None
    if Campaign.CHANNEL_EMAIL in channels:
        subject = (request.POST.get('email_subject') or '').strip()
        body_html = (request.POST.get('email_body_html') or '').strip()
        if not subject or not body_html:
            raise ValueError("Email needs a subject and a body.")
        email_content = {'subject_en': subject, 'body_html_en': body_html}
        for lang in ('de', 'fr'):
            lang_subject = (request.POST.get(f'email_subject_{lang}') or '').strip()
            lang_body = (request.POST.get(f'email_body_html_{lang}') or '').strip()
            if lang_subject:
                email_content[f'subject_{lang}'] = lang_subject
            if lang_body:
                email_content[f'body_html_{lang}'] = lang_body

    whatsapp = None
    if Campaign.CHANNEL_WHATSAPP in channels:
        template_name = (request.POST.get('whatsapp_template_name') or '').strip()
        if not template_name:
            raise ValueError("Pick an approved WhatsApp template.")
        _validate_whatsapp_template(template_name, language)
        parameters = {}
        for slot in range(1, WHATSAPP_PARAM_SLOTS + 1):
            value = (request.POST.get(f'whatsapp_param_{slot}') or '').strip()
            if value:
                parameters[str(slot)] = value
        whatsapp = {'template_name': template_name, 'parameters': parameters}

    push = None
    if Campaign.CHANNEL_PUSH in channels:
        push_title = (request.POST.get('push_title') or '').strip()
        push_body = (request.POST.get('push_body') or '').strip()
        if not push_title or not push_body:
            raise ValueError("Push needs a title and a body.")
        push = {
            'title_en': push_title,
            'body_en': push_body,
            'url': (request.POST.get('push_url') or '/').strip() or '/',
        }
        for lang in ('de', 'fr'):
            lang_title = (request.POST.get(f'push_title_{lang}') or '').strip()
            lang_body = (request.POST.get(f'push_body_{lang}') or '').strip()
            if lang_title:
                push[f'title_{lang}'] = lang_title
            if lang_body:
                push[f'body_{lang}'] = lang_body

    send_mode = request.POST.get('send_mode') or 'draft'
    scheduled_at = None
    if send_mode == 'now':
        scheduled_at = timezone.now()
    elif send_mode == 'schedule':
        raw = (request.POST.get('scheduled_at') or '').strip()
        parsed = None
        if raw:
            from django.utils.dateparse import parse_datetime
            parsed = parse_datetime(raw)
        if parsed is None:
            raise ValueError("Pick a valid schedule date and time.")
        if timezone.is_naive(parsed):
            # The composer labels this input as UTC — attach UTC, not the
            # project default timezone (Europe/Luxembourg).
            parsed = parsed.replace(tzinfo=dt_timezone.utc)
        if parsed <= timezone.now():
            raise ValueError("The scheduled time must be in the future.")
        scheduled_at = parsed

    return {
        'name': name,
        'channels': channels,
        'audience': audience,
        'segment_key': segment_key,
        'language': language,
        'email_content': email_content,
        'whatsapp': whatsapp,
        'push': push,
        'scheduled_at': scheduled_at,
    }


@login_required
@require_POST
def campaign_create(request):
    error = _check_admin_access(request)
    if error:
        return error

    try:
        kwargs = _parse_composer_form(request)
        campaign = campaign_service.create_campaign(
            created_by=request.user, **kwargs,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('campaign_new')

    if campaign.status == 'scheduled':
        if campaign.scheduled_at <= timezone.now():
            messages.success(
                request,
                f'Campaign "{campaign.name}" queued — sending begins within '
                f'~5 minutes.',
            )
        else:
            messages.success(
                request,
                f'Campaign "{campaign.name}" scheduled for '
                f'{campaign.scheduled_at:%Y-%m-%d %H:%M} UTC.',
            )
    else:
        messages.success(
            request, f'Campaign "{campaign.name}" saved as draft.',
        )
    return redirect('campaign_detail', campaign_id=campaign.pk)


@login_required
def campaign_detail(request, campaign_id):
    error = _check_admin_access(request)
    if error:
        return error

    campaign = get_object_or_404(Campaign, pk=campaign_id)
    links = (
        campaign.links.annotate(click_count=Count('clicks'))
        .order_by('-click_count')
    )
    recipients = (
        campaign.recipients.select_related('user', 'whatsapp_message')
        .order_by('-sent_at')[:25]
    )
    context = {
        'title': campaign.name,
        'campaign': campaign,
        'stats': campaign.stats,
        'links': links,
        'recipients': recipients,
        'newsletter': getattr(campaign, 'email_newsletter', None),
    }
    return render(request, 'admin/crush_lu/campaign_detail.html', context)


@login_required
@require_POST
def campaign_cancel(request, campaign_id):
    error = _check_admin_access(request)
    if error:
        return error

    campaign = get_object_or_404(Campaign, pk=campaign_id)
    if campaign.cancel():
        messages.success(request, f'Campaign "{campaign.name}" cancelled.')
    else:
        messages.warning(
            request,
            f'Campaign "{campaign.name}" is already '
            f'{campaign.get_status_display().lower()} — nothing to cancel.',
        )
    return redirect('campaign_detail', campaign_id=campaign.pk)


@login_required
@require_POST
def campaign_estimate(request):
    """HTMX partial: live per-channel recipient counts for the composer."""
    error = _check_admin_access(request)
    if error:
        return error

    channels = request.POST.getlist('channels')
    estimate = campaign_service.estimate_campaign(
        audience=request.POST.get('audience') or 'segment',
        segment_key=(request.POST.get('segment_key') or '').strip(),
        language=request.POST.get('language') or 'all',
        channels=channels,
    )
    return render(
        request,
        'admin/crush_lu/partials/_campaign_estimate.html',
        {'estimate': estimate, 'channels': channels},
    )


@login_required
@require_POST
def campaign_preview(request):
    """HTMX partial: per-channel content preview for the composer."""
    error = _check_admin_access(request)
    if error:
        return error

    channels = request.POST.getlist('channels')
    context = {'channels': channels}

    if Campaign.CHANNEL_EMAIL in channels:
        from django.template.loader import render_to_string
        email_html = render_to_string('crush_lu/emails/newsletter.html', {
            'user': request.user,
            'first_name': request.user.first_name or 'Alex',
            'body_html': request.POST.get('email_body_html') or '',
            'unsubscribe_url': '#',
            'home_url': 'https://crush.lu/',
            'about_url': 'https://crush.lu/en/about/',
            'events_url': 'https://crush.lu/en/events/',
            'settings_url': 'https://crush.lu/en/settings/',
            'social_links': {},
            'LANGUAGE_CODE': 'en',
        })
        context['email_subject'] = request.POST.get('email_subject') or ''
        context['email_html'] = email_html

    if Campaign.CHANNEL_WHATSAPP in channels:
        template_name = (request.POST.get('whatsapp_template_name') or '').strip()
        templates, template_error = _safe_template_list()
        matching = [t for t in templates if t['name'] == template_name]
        context['whatsapp_template_name'] = template_name
        context['whatsapp_variants'] = matching
        context['whatsapp_error'] = template_error
        context['whatsapp_parameters'] = {
            str(slot): request.POST.get(f'whatsapp_param_{slot}') or ''
            for slot in range(1, WHATSAPP_PARAM_SLOTS + 1)
            if request.POST.get(f'whatsapp_param_{slot}')
        }

    if Campaign.CHANNEL_PUSH in channels:
        context['push_title'] = request.POST.get('push_title') or ''
        context['push_body'] = request.POST.get('push_body') or ''
        context['push_url'] = request.POST.get('push_url') or '/'

    return render(
        request, 'admin/crush_lu/partials/_campaign_preview.html', context,
    )


@login_required
def campaign_status_partial(request, campaign_id):
    """HTMX polling partial: live status/stats while a campaign sends."""
    error = _check_admin_access(request)
    if error:
        return error

    campaign = get_object_or_404(Campaign, pk=campaign_id)
    return render(
        request,
        'admin/crush_lu/partials/_campaign_status.html',
        {'campaign': campaign, 'stats': campaign.stats},
    )


@login_required
def campaign_overview_api(request):
    """Chart JSON: campaign messages sent per week per channel (12 weeks)."""
    error = _check_admin_access(request)
    if error:
        return error

    start = timezone.now() - timedelta(weeks=12)

    email_rows = (
        NewsletterRecipient.objects.filter(
            status='sent',
            sent_at__gte=start,
            newsletter__campaign__isnull=False,
        )
        .annotate(period=TruncWeek('sent_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    )
    channel_rows = (
        CampaignRecipient.objects.filter(status='sent', sent_at__gte=start)
        .annotate(period=TruncWeek('sent_at'))
        .values('period', 'channel')
        .annotate(count=Count('id'))
        .order_by('period')
    )

    series = {'email': {}, 'whatsapp': {}, 'push': {}}
    for row in email_rows:
        series['email'][str(row['period'].date())] = row['count']
    for row in channel_rows:
        if row['channel'] in series:
            series[row['channel']][str(row['period'].date())] = row['count']

    labels = sorted({
        label for channel in series.values() for label in channel
    })
    datasets = [
        {
            'label': channel.title(),
            'data': [series[channel].get(label, 0) for label in labels],
        }
        for channel in ('email', 'whatsapp', 'push')
    ]
    return JsonResponse({
        'labels': labels,
        'datasets': datasets,
        'summary': {
            'total': sum(
                sum(channel.values()) for channel in series.values()
            ),
        },
    })


@login_required
def campaign_clicks_api(request, campaign_id):
    """Chart JSON: clicks per day for one campaign."""
    error = _check_admin_access(request)
    if error:
        return error

    campaign = get_object_or_404(Campaign, pk=campaign_id)
    rows = (
        CampaignClick.objects.filter(link__campaign=campaign)
        .annotate(period=TruncDay('clicked_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    )
    labels = [str(row['period'].date()) for row in rows]
    data = [row['count'] for row in rows]
    return JsonResponse({
        'labels': labels,
        'datasets': [{'label': 'Clicks', 'data': data}],
        'summary': {'total': sum(data)},
    })


@login_required
def reminders_funnel_api(request):
    """Chart JSON: reminder volume by type + verified-after-reminder conversion."""
    error = _check_admin_access(request)
    if error:
        return error

    counts = dict(
        ProfileReminder.objects.values_list('reminder_type')
        .annotate(n=Count('id'))
        .values_list('reminder_type', 'n')
    )
    converted = dict(
        ProfileReminder.objects.filter(
            user__crushprofile__verification_status='verified',
            user__crushprofile__approved_at__gt=F('sent_at'),
        )
        .values_list('reminder_type')
        .annotate(n=Count('user_id', distinct=True))
        .values_list('reminder_type', 'n')
    )
    labels = ['24h', '72h', '7d']
    return JsonResponse({
        'labels': labels,
        'datasets': [
            {'label': 'Sent', 'data': [counts.get(l, 0) for l in labels]},
            {
                'label': 'Verified after reminder',
                'data': [converted.get(l, 0) for l in labels],
            },
        ],
        'summary': {'total_sent': sum(counts.values())},
    })
