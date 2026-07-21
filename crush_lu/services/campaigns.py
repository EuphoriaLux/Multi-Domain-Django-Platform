"""
Multi-channel campaign service: audience resolution, channel adapters, and the
bounded-batch dispatcher behind the Coach Panel campaign dashboard.

Design constraints (see docs/specs/campaign-dashboard.md):

- Production has no async task worker (Django tasks run on ImmediateBackend),
  so campaign sends are driven by an Azure Function timer POSTing
  ``/api/admin/campaigns/dispatch/`` every few minutes. Each tick must finish
  well inside gunicorn's 120s request timeout, hence the per-channel tick
  limits and the wall-clock budget below.
- Every channel is resumable: the email leg persists per-recipient state in
  ``NewsletterRecipient`` (via the campaign-linked Newsletter), the other
  channels in ``CampaignRecipient``. A tick processes a bounded slice and the
  next tick continues where it stopped.
- Adding a channel means adding one adapter to ``CHANNEL_ADAPTERS``.
"""
import logging
import re
import secrets
import time as time_module
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.core.signing import Signer
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone, translation

from crush_lu import newsletter_service
from crush_lu.models import (
    Campaign,
    CampaignLink,
    CampaignRecipient,
    EmailPreference,
)
from crush_lu.utils.i18n import get_user_preferred_language

logger = logging.getLogger(__name__)

# Per-tick send limits, sized so one dispatch tick stays far below gunicorn's
# 120s timeout (startup.sh):
# - Email: exactly one Graph API batch (send_newsletter pauses 62s only
#   *between* 25-email batches, so a 25-email run never sleeps).
# - WhatsApp: sends are spaced ~1s apart (pair rate-limit hygiene).
# - Push: webpush calls are fast but still network I/O.
EMAIL_LIMIT_PER_TICK = 25
WHATSAPP_LIMIT_PER_TICK = 30
PUSH_LIMIT_PER_TICK = 150

# Stop starting new channel batches once a tick has run this long.
DISPATCH_TIME_BUDGET_SECONDS = 80

# A campaign claimed by a tick (dispatch_heartbeat_at set) is skipped by other
# ticks until the heartbeat goes stale — covers overlapping timer invocations
# without holding row locks across network calls.
HEARTBEAT_STALE_MINUTES = 15

WHATSAPP_SEND_SPACING_SECONDS = 1.0


@dataclass
class BatchResult:
    """Outcome of one bounded send batch for one channel."""

    sent: int = 0
    failed: int = 0
    skipped: int = 0
    remaining: int = 0
    # True when the batch was cut short (tick budget) rather than exhausted,
    # so `remaining` may undercount; the campaign must stay in 'sending'.
    interrupted: bool = False

    @property
    def processed(self):
        return self.sent + self.failed + self.skipped

    @property
    def complete(self):
        return self.remaining == 0 and not self.interrupted


def resolve_campaign_audience(campaign):
    """Base audience for the non-email channels of a campaign.

    Mirrors the newsletter audience semantics exactly (same resolver), then
    applies the exclusions shared by every channel: banned/deleted users and
    the campaign's language restriction. Channel-specific consent gates are
    layered on top by each adapter.
    """
    users = newsletter_service.resolve_audience(
        campaign.audience, campaign.segment_key
    )
    users = newsletter_service.exclude_banned_users(users)
    users = newsletter_service.apply_language_filter(users, campaign.language)
    return users


def _exclude_processed(users, campaign, channel):
    """Resumability: drop users already handled for this campaign+channel.

    All outcomes are terminal for dispatch — failed sends are not retried
    automatically (WhatsApp template sends are paid; email failures are
    excluded for the same convergence reason in send_newsletter). 'pending'
    is a pre-send claim (WhatsApp): if a worker died mid-send the outcome is
    unknown, so at-most-once wins over retrying a possibly-delivered paid
    message.
    """
    if campaign.pk is None:
        # Transient campaign (estimate-only call) — nothing processed yet.
        return users
    processed_ids = CampaignRecipient.objects.filter(
        campaign=campaign,
        channel=channel,
        status__in=['pending', 'sent', 'failed', 'skipped'],
    ).values_list('user_id', flat=True)
    return users.exclude(id__in=processed_ids)


def _is_cancelled(campaign):
    """Fresh-from-DB check so an in-flight batch notices a cancellation.

    Cheap single-row read; called between per-recipient sends (which are
    slow network calls anyway) so a cancel stops contact within one message
    of the click instead of at the end of the batch.
    """
    return Campaign.objects.filter(
        pk=campaign.pk, status='cancelled',
    ).exists()


def _substitute_merge_tokens(text, user):
    """Replace supported {token}s in WhatsApp parameter values."""
    return (
        str(text)
        .replace('{first_name}', user.first_name or '')
        .replace('{last_name}', user.last_name or '')
        .replace('{email}', user.email or '')
    )


# --- Click + UTM tracking ---------------------------------------------------

CLICK_SIGNER_SALT = 'campaign-click'

# The public host the redirect must live on: campaign links land in emails,
# WhatsApp messages and push payloads, all of which need absolute URLs.
CLICK_REDIRECT_BASE = 'https://crush.lu'

_HREF_RE = re.compile(r'''href=(["'])(https?://[^"']+)\1''', re.IGNORECASE)


def click_signer():
    return Signer(salt=CLICK_SIGNER_SALT)


def _merge_utm_params(url, campaign, channel):
    """Add utm_source/medium/campaign to a URL, keeping existing params.

    Works on the raw (key, value) pair list — never a dict — so repeated
    query keys (``?category=a&category=b``) survive the round trip.
    """
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    present = {key for key, _ in pairs}
    for key, value in (
        ('utm_source', 'crush.lu'),
        ('utm_medium', channel),
        ('utm_campaign', campaign.slug),
    ):
        if key not in present:
            pairs.append((key, value))
    return urlunsplit((
        parts.scheme, parts.netloc, parts.path,
        urlencode(pairs), parts.fragment,
    ))


def build_tracked_url(url, campaign, channel, user=None):
    """Turn a destination URL into a click-tracked redirect URL.

    Creates (or reuses) the campaign's ``CampaignLink`` for this destination
    and returns ``https://crush.lu/c/<token>/``, carrying the recipient in a
    signed ``?r=`` parameter when a user is given. The redirect resolves to
    the destination with UTM parameters merged in.
    """
    link, _ = CampaignLink.objects.get_or_create(
        campaign=campaign,
        channel=channel,
        original_url=url,
        defaults={
            'token': secrets.token_urlsafe(9),
            'tracked_url': _merge_utm_params(url, campaign, channel),
        },
    )
    path = reverse(
        'campaign_click_redirect',
        urlconf='azureproject.urls_crush',
        kwargs={'token': link.token},
    )
    redirect_url = f"{CLICK_REDIRECT_BASE}{path}"
    if user is not None:
        # Bind the attribution to this specific link so a recipient can't
        # lift their ?r= value onto another campaign's URL and pollute its
        # click stats (verified against the resolved link in the redirect).
        signed = click_signer().sign(f"{user.pk}:{link.token}")
        redirect_url = f"{redirect_url}?r={quote(signed)}"
    return redirect_url


def rewrite_html_links(html, campaign, channel, user=None):
    """Rewrite absolute http(s) hrefs in an email body to tracked URLs.

    Skips unsubscribe links (they must remain direct and one-click) and URLs
    that are already tracked. ``mailto:`` and same-page anchors never match.
    """
    def _replace(match):
        quote_char, url = match.group(1), match.group(2)
        # hrefs are HTML-escaped; work on the real URL and re-escape after.
        real_url = url.replace('&amp;', '&')
        if 'unsubscribe' in real_url:
            return match.group(0)
        if real_url.startswith(f"{CLICK_REDIRECT_BASE}/c/"):
            return match.group(0)
        tracked = build_tracked_url(real_url, campaign, channel, user)
        return f"href={quote_char}{tracked.replace('&', '&amp;')}{quote_char}"

    return _HREF_RE.sub(_replace, html)


class EmailAdapter:
    """Email leg — thin wrapper over the proven newsletter engine."""

    key = Campaign.CHANNEL_EMAIL

    def eligible_users(self, campaign):
        newsletter = getattr(campaign, 'email_newsletter', None)
        if newsletter is None:
            return User.objects.none()
        return newsletter_service.get_newsletter_recipients(newsletter)

    def send_batch(self, campaign, limit, deadline=None, stdout=None):
        newsletter = getattr(campaign, 'email_newsletter', None)
        if newsletter is None:
            # Broken invariant (linked newsletter deleted?) — defer rather
            # than report complete, or the campaign would finalize as 'sent'
            # without a single email. Stays visible as a stuck 'sending'
            # campaign the operator can cancel.
            logger.error(
                "Campaign #%s has the email channel enabled but no linked "
                "newsletter — deferring the email leg", campaign.pk,
            )
            return BatchResult(interrupted=True, remaining=1)
        if newsletter.status not in ('draft', 'sending'):
            # Already finalized (e.g. sent manually) — nothing left to do.
            return BatchResult(remaining=0)

        # Consistency net: the campaign record is the single source of truth
        # for targeting; realign the newsletter if they ever diverge.
        if (
            newsletter.audience != campaign.audience
            or newsletter.segment_key != campaign.segment_key
            or newsletter.language != campaign.language
        ):
            logger.warning(
                "Campaign #%s: newsletter targeting diverged from the "
                "campaign — realigning before send", campaign.pk,
            )
            newsletter.audience = campaign.audience
            newsletter.segment_key = campaign.segment_key
            newsletter.language = campaign.language
            newsletter.save(update_fields=[
                'audience', 'segment_key', 'language', 'updated_at',
            ])

        result = newsletter_service.send_newsletter(
            newsletter,
            limit=limit,
            stdout=stdout,
            link_rewriter=lambda html, user: rewrite_html_links(
                html, campaign, self.key, user,
            ),
            # Abort between recipients on cancellation OR when the tick's
            # wall-clock budget is spent — a slow Graph backend (30s/request)
            # could otherwise push 25 sends far past the gunicorn timeout.
            should_abort=lambda: (
                _is_cancelled(campaign)
                or (
                    deadline is not None
                    and time_module.monotonic() > deadline
                )
            ),
        )
        return BatchResult(
            sent=result['sent'],
            failed=result['failed'],
            skipped=result['skipped'],
            remaining=result.get('remaining', 0),
            interrupted=result.get('aborted', False),
        )


class WhatsAppAdapter:
    """WhatsApp template sends via the hub's Meta Cloud API service."""

    key = Campaign.CHANNEL_WHATSAPP

    @staticmethod
    def _resolve_sender(campaign):
        """Sender identity for WhatsAppMessage rows (its user is NOT nullable).

        Falls back to an active superuser when the creating coach's account
        was deleted before a scheduled campaign dispatched — the campaign and
        recipient consent remain valid without them.
        """
        if campaign.created_by is not None:
            return campaign.created_by
        return (
            User.objects.filter(is_superuser=True, is_active=True)
            .order_by('pk')
            .first()
        )

    def eligible_users(self, campaign):
        users = resolve_campaign_audience(campaign)
        # Explicit opt-in only: a missing EmailPreference row means NOT opted
        # in (whatsapp_opt_in defaults to False — GDPR).
        opted_in_ids = EmailPreference.objects.filter(
            whatsapp_opt_in=True,
            unsubscribed_all=False,
        ).values_list('user_id', flat=True)
        users = users.filter(id__in=opted_in_ids)
        # Sendable number: verified, present, and not flagged off-WhatsApp
        # (queryset form of services.whatsapp.can_send_whatsapp).
        users = users.filter(
            crushprofile__phone_verified=True,
            crushprofile__not_on_whatsapp=False,
        ).exclude(crushprofile__phone_number='')
        return _exclude_processed(users, campaign, self.key)

    def send_batch(self, campaign, limit, deadline=None, stdout=None):
        from hub.whatsapp_service import send_whatsapp_template

        users = self.eligible_users(campaign)
        user_ids = list(users.values_list('id', flat=True)[:limit])

        result = BatchResult()
        sender = self._resolve_sender(campaign)
        if user_ids and sender is None:
            logger.error(
                "Campaign #%s has no usable WhatsApp sender (creator deleted, "
                "no active superuser) — leaving batch for a later tick",
                campaign.pk,
            )
            result.interrupted = True
            result.remaining = users.count()
            return result

        for i, user_id in enumerate(user_ids):
            if i > 0:
                time_module.sleep(WHATSAPP_SEND_SPACING_SECONDS)
            # Checked AFTER the pacing sleep so a cancel (or the deadline)
            # landing during it stops before the next paid Meta call.
            if deadline is not None and time_module.monotonic() > deadline:
                result.interrupted = True
                break
            if _is_cancelled(campaign):
                result.interrupted = True
                break

            user = User.objects.filter(id=user_id).first()
            if user is None:
                continue
            profile = getattr(user, 'crushprofile', None)
            lang = get_user_preferred_language(user=user, default='en')
            parameters = {}
            for key, value in (campaign.whatsapp_parameters or {}).items():
                value = _substitute_merge_tokens(value, user)
                if value.startswith(('http://', 'https://')):
                    value = build_tracked_url(value, campaign, self.key, user)
                parameters[key] = value

            # Durable pre-send claim: if the worker dies after Meta accepts
            # but before the outcome lands, this row (excluded from later
            # eligibility) prevents a second paid send for the same user.
            claim, created = CampaignRecipient.objects.get_or_create(
                campaign=campaign,
                channel=self.key,
                user=user,
                defaults={
                    'status': 'pending',
                    'error_message': 'claimed for send',
                },
            )
            if not created and claim.status != 'pending':
                continue  # processed by a concurrent tick

            try:
                message = send_whatsapp_template(
                    sender=sender,
                    recipient=profile.phone_number,
                    template_name=campaign.whatsapp_template_name,
                    language=lang,
                    parameters=parameters,
                )
            except Exception as exc:  # noqa: BLE001 — record and continue
                logger.exception(
                    "Campaign #%s WhatsApp send crashed for user %s",
                    campaign.pk, user_id,
                )
                self._record(campaign, user, 'failed', error=str(exc)[:500])
                result.failed += 1
                continue

            if message.status == message.Status.SENT:
                self._record(campaign, user, 'sent', message=message)
                result.sent += 1
            else:
                error = ''
                if message.status_history:
                    error = message.status_history[-1].get('error_message', '')
                self._record(
                    campaign, user, 'failed', message=message, error=error
                )
                result.failed += 1

        result.remaining = self.eligible_users(campaign).count()
        return result

    def _record(self, campaign, user, status, message=None, error=''):
        CampaignRecipient.objects.update_or_create(
            campaign=campaign,
            channel=self.key,
            user=user,
            defaults={
                'status': status,
                'sent_at': timezone.now() if status == 'sent' else None,
                'error_message': error,
                'whatsapp_message': message,
            },
        )


class PushAdapter:
    """Web push broadcast — fans out per-user via push_notifications."""

    key = Campaign.CHANNEL_PUSH

    def eligible_users(self, campaign):
        users = resolve_campaign_audience(campaign)
        users = users.filter(push_subscriptions__enabled=True).distinct()
        return _exclude_processed(users, campaign, self.key)

    def send_batch(self, campaign, limit, deadline=None, stdout=None):
        from crush_lu.push_notifications import send_push_notification

        users = self.eligible_users(campaign)
        user_ids = list(users.values_list('id', flat=True)[:limit])

        result = BatchResult()
        if user_ids and not (
            getattr(settings, 'VAPID_PRIVATE_KEY', '')
            and getattr(settings, 'VAPID_PUBLIC_KEY', '')
        ):
            # Config failure, not an empty audience: send_push_notification
            # would zero out every user and this batch would record them all
            # as skipped, letting the campaign finalize as 'sent' without a
            # single push attempted. Defer instead so a config fix resumes it.
            logger.error(
                "Campaign #%s push batch deferred: VAPID keys are not "
                "configured", campaign.pk,
            )
            result.interrupted = True
            result.remaining = users.count()
            return result
        for user_id in user_ids:
            if deadline is not None and time_module.monotonic() > deadline:
                result.interrupted = True
                break
            if _is_cancelled(campaign):
                result.interrupted = True
                break

            user = User.objects.filter(id=user_id).first()
            if user is None:
                continue
            lang = get_user_preferred_language(user=user, default='en')

            # Durable pre-send claim (same pattern as WhatsApp): a worker
            # dying after delivery but before the receipt lands must not
            # cause a duplicate push on the next tick.
            claim, created = CampaignRecipient.objects.get_or_create(
                campaign=campaign,
                channel=self.key,
                user=user,
                defaults={
                    'status': 'pending',
                    'error_message': 'claimed for send',
                },
            )
            if not created and claim.status != 'pending':
                continue  # processed by a concurrent tick

            try:
                # Read the modeltranslation variants for the user's language.
                with translation.override(lang):
                    title = campaign.push_title
                    body = campaign.push_body
                outcome = send_push_notification(
                    user,
                    title,
                    body,
                    url=self.build_push_url(campaign, user),
                    tag=f'campaign-{campaign.slug}',
                )
            except Exception as exc:  # noqa: BLE001 — record and continue
                logger.exception(
                    "Campaign #%s push send crashed for user %s",
                    campaign.pk, user_id,
                )
                self._record(campaign, user, 'failed', error=str(exc)[:500])
                result.failed += 1
                continue

            if outcome.get('success', 0) > 0:
                self._record(campaign, user, 'sent')
                result.sent += 1
            elif outcome.get('total', 0) == 0:
                self._record(
                    campaign, user, 'skipped',
                    error='No active push subscriptions',
                )
                result.skipped += 1
            else:
                self._record(
                    campaign, user, 'failed',
                    error='All push subscriptions failed',
                )
                result.failed += 1

        result.remaining = self.eligible_users(campaign).count()
        return result

    def build_push_url(self, campaign, user):
        return build_tracked_url(
            campaign.push_url or '/', campaign, self.key, user,
        )

    def _record(self, campaign, user, status, error=''):
        CampaignRecipient.objects.update_or_create(
            campaign=campaign,
            channel=self.key,
            user=user,
            defaults={
                'status': status,
                'sent_at': timezone.now() if status == 'sent' else None,
                'error_message': error,
            },
        )


CHANNEL_ADAPTERS = {
    adapter.key: adapter
    for adapter in (EmailAdapter(), WhatsAppAdapter(), PushAdapter())
}

DEFAULT_TICK_LIMITS = {
    Campaign.CHANNEL_EMAIL: EMAIL_LIMIT_PER_TICK,
    Campaign.CHANNEL_WHATSAPP: WHATSAPP_LIMIT_PER_TICK,
    Campaign.CHANNEL_PUSH: PUSH_LIMIT_PER_TICK,
}


def estimate_campaign(audience, segment_key='', language='all', channels=None):
    """Per-channel eligible counts for the composer's live estimate.

    Uses transient (unsaved) Campaign/Newsletter instances so the exact
    production exclusion logic runs without touching the database.
    """
    from crush_lu.models import Newsletter

    channels = [c for c in (channels or []) if c in CHANNEL_ADAPTERS]
    campaign = Campaign(
        audience=audience,
        segment_key=segment_key,
        language=language,
        channels=channels,
    )

    estimate = {}
    union_ids = set()
    for channel in channels:
        if channel == Campaign.CHANNEL_EMAIL:
            newsletter = Newsletter(
                audience=audience, segment_key=segment_key, language=language,
            )
            ids = set(
                newsletter_service.get_newsletter_recipients(newsletter)
                .values_list('id', flat=True)
            )
        else:
            ids = set(
                CHANNEL_ADAPTERS[channel]
                .eligible_users(campaign)
                .values_list('id', flat=True)
            )
        estimate[channel] = len(ids)
        union_ids |= ids

    estimate['reach'] = len(union_ids)
    return estimate


def create_campaign(*, name, channels, audience, segment_key='',
                    language='all', email_content=None, whatsapp=None,
                    push=None, created_by=None, scheduled_at=None):
    """Create a campaign (and its email-leg Newsletter when email is enabled).

    ``email_content`` is a dict of Newsletter field values — plain
    (``subject``, ``body_html``) and/or modeltranslation-suffixed
    (``subject_de``, ``body_html_fr``) keys are both accepted.
    ``scheduled_at`` set => status 'scheduled'; None => stays 'draft'.
    """
    from crush_lu.models import Newsletter

    channels = list(dict.fromkeys(channels))  # de-dupe, keep order
    unknown = [c for c in channels if c not in CHANNEL_ADAPTERS]
    if unknown:
        raise ValueError(f"Unknown campaign channels: {unknown}")
    if not channels:
        raise ValueError("A campaign needs at least one channel")

    campaign = Campaign(
        name=name,
        channels=channels,
        audience=audience,
        segment_key=segment_key,
        language=language,
        whatsapp_template_name=(whatsapp or {}).get('template_name', ''),
        whatsapp_parameters=(whatsapp or {}).get('parameters', {}),
        push_url=(push or {}).get('url', '/'),
        created_by=created_by,
        status='scheduled' if scheduled_at else 'draft',
        scheduled_at=scheduled_at,
    )
    for field, value in (push or {}).items():
        if field.startswith(('title', 'body')):
            setattr(campaign, f'push_{field}', value)
    campaign.save()

    if Campaign.CHANNEL_EMAIL in channels:
        newsletter = Newsletter(
            campaign=campaign,
            audience=audience,
            segment_key=segment_key,
            language=language,
            created_by=created_by,
            status='draft',
        )
        for field, value in (email_content or {}).items():
            setattr(newsletter, field, value)
        newsletter.save()

    snapshot = estimate_campaign(
        audience, segment_key, language, channels,
    )
    snapshot['captured_at'] = timezone.now().isoformat()
    campaign.audience_snapshot = snapshot
    campaign.save(update_fields=['audience_snapshot', 'updated_at'])
    return campaign


def _reconcile_unresolved_claims(campaign):
    """Convert stale pre-send claims into failures before finalizing.

    A 'pending' CampaignRecipient at finalization time means a worker died
    between claiming and recording the outcome — the recipient may or may
    not have been contacted. Counting these as failed keeps the campaign
    from finalizing as a clean 'sent' over unknown outcomes. (The email leg
    does the same sweep inside send_newsletter.)
    """
    return CampaignRecipient.objects.filter(
        campaign=campaign, status='pending',
    ).update(
        status='failed',
        error_message='Unresolved send claim — outcome unknown '
                      '(worker interrupted mid-send)',
    )


def _finalize_status(campaign):
    """Terminal status from persisted per-recipient stats."""
    totals = campaign.stats['totals']
    if totals['failed'] == 0:
        return 'sent'
    if totals['sent'] > 0:
        return 'partial'
    return 'failed'


def dispatch_campaigns(now=None, limits=None, time_budget=None, stdout=None,
                       campaign_id=None):
    """Run one bounded dispatch tick. Safe to invoke concurrently.

    1. Promote due scheduled campaigns to 'sending'.
    2. For each 'sending' campaign without a fresh heartbeat (oldest first),
       run every enabled channel's bounded batch until the per-channel tick
       limits or the wall-clock budget are spent.
    3. Finalize campaigns whose channels all report nothing left to send.

    ``campaign_id`` restricts the whole tick (promotion AND sending) to one
    campaign — used by ``dispatch_campaigns --campaign-id`` so an operator
    focusing on one campaign never launches unrelated ones.

    Returns a summary dict for the admin API endpoint / management command.
    """
    now = now or timezone.now()
    budget = dict(DEFAULT_TICK_LIMITS)
    if limits:
        budget.update(limits)
    deadline = time_module.monotonic() + (
        time_budget if time_budget is not None else DISPATCH_TIME_BUDGET_SECONDS
    )

    def log(msg):
        if stdout:
            stdout.write(msg)
        logger.info(msg)

    due = Campaign.objects.filter(status='scheduled', scheduled_at__lte=now)
    if campaign_id is not None:
        due = due.filter(pk=campaign_id)
    promoted = due.update(status='sending', started_at=now)
    if promoted:
        log(f"Promoted {promoted} scheduled campaign(s) to sending")

    stale_cutoff = now - timezone.timedelta(minutes=HEARTBEAT_STALE_MINUTES)
    not_claimed = Q(dispatch_heartbeat_at__isnull=True) | Q(
        dispatch_heartbeat_at__lt=stale_cutoff
    )
    candidate_qs = (
        Campaign.objects.filter(status='sending')
        .filter(not_claimed)
        .order_by('started_at', 'created_at')
    )
    if campaign_id is not None:
        candidate_qs = candidate_qs.filter(pk=campaign_id)
    candidates = list(candidate_qs)

    summary = {'promoted': promoted, 'campaigns': []}
    for campaign in candidates:
        if time_module.monotonic() > deadline:
            log("Tick budget exhausted; remaining campaigns wait for next tick")
            break
        if all(budget[c] <= 0 for c in campaign.channels if c in budget):
            continue

        # Atomic claim — loses gracefully to a concurrent tick.
        claimed = (
            Campaign.objects.filter(pk=campaign.pk, status='sending')
            .filter(not_claimed)
            .update(dispatch_heartbeat_at=now)
        )
        if not claimed:
            continue

        entry = {'id': campaign.pk, 'name': campaign.name, 'channels': {}}
        all_complete = True
        try:
            for channel in campaign.channels:
                if _is_cancelled(campaign):
                    log(f"Campaign #{campaign.pk} cancelled mid-tick — stopping")
                    all_complete = False
                    break
                adapter = CHANNEL_ADAPTERS.get(channel)
                if adapter is None:
                    log(f"Campaign #{campaign.pk}: unknown channel '{channel}' skipped")
                    continue
                if budget.get(channel, 0) <= 0 or time_module.monotonic() > deadline:
                    all_complete = False
                    continue

                result = adapter.send_batch(
                    campaign,
                    limit=budget[channel],
                    deadline=deadline,
                    stdout=stdout,
                )
                budget[channel] -= result.processed
                entry['channels'][channel] = {
                    'sent': result.sent,
                    'failed': result.failed,
                    'skipped': result.skipped,
                    'remaining': result.remaining,
                }
                if not result.complete:
                    all_complete = False
        except Exception:
            # A crashed batch means unknown channel state — never finalize
            # from incomplete counters; clear the claim (finally) so the next
            # tick resumes, and let the error propagate to the caller.
            all_complete = False
            raise
        finally:
            if all_complete:
                reconciled = _reconcile_unresolved_claims(campaign)
                if reconciled:
                    log(
                        f"Campaign #{campaign.pk}: {reconciled} unresolved "
                        f"send claim(s) marked failed"
                    )
                final_status = _finalize_status(campaign)
                # Guarded update: only finalize while still 'sending', so a
                # cancellation that landed mid-batch is never overwritten.
                finalized = Campaign.objects.filter(
                    pk=campaign.pk, status='sending',
                ).update(
                    status=final_status,
                    completed_at=timezone.now(),
                    dispatch_heartbeat_at=None,
                )
                if finalized:
                    log(f"Campaign #{campaign.pk} finalized: {final_status}")
                else:
                    Campaign.objects.filter(pk=campaign.pk).update(
                        dispatch_heartbeat_at=None,
                    )
            else:
                Campaign.objects.filter(pk=campaign.pk).update(
                    dispatch_heartbeat_at=None,
                )
            campaign.refresh_from_db()

        entry['status'] = campaign.status
        summary['campaigns'].append(entry)

    return summary
