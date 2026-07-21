# Campaign & Remarketing Dashboard (Crush.lu Coach Panel)

Status: **implemented**. This spec supersedes the original 5-tab per-channel
draft: instead of managing each channel separately, campaigns are a **unified
entity** — compose once, pick a segment, pick channels (Email / WhatsApp /
Web Push), send now or schedule, and read combined per-channel results in one
record.

---

## 1. Architecture

```
Coach Panel UI (/crush-admin/campaigns/)
    └── Campaign (crush_lu/models/campaigns.py)
          ├── email  ──► Newsletter + NewsletterRecipient   (existing engine)
          ├── whatsapp ─► hub.whatsapp_service + WhatsAppMessage (Meta Cloud API)
          └── push  ──► push_notifications (Web Push/VAPID)
        per-recipient state: NewsletterRecipient (email),
                             CampaignRecipient (whatsapp/push)
        clicks: CampaignLink + CampaignClick  (/c/<token>/ redirect + UTM)

Scheduling (production has NO async worker — Django tasks run inline):
    CampaignDispatch Azure Function timer (every 5 min, hybrid-maintenance app)
        └── POST /api/admin/campaigns/dispatch/   (Bearer ADMIN_API_KEY)
              └── services.campaigns.dispatch_campaigns()
                    = same logic as `manage.py dispatch_campaigns`
```

Key modules:

| Concern | File |
|---|---|
| Models (Campaign, CampaignRecipient, CampaignLink, CampaignClick) | `crush_lu/models/campaigns.py` |
| Channel adapters + dispatcher + tracking service | `crush_lu/services/campaigns.py` |
| WhatsApp send service (shared with hub CRM) | `hub/whatsapp_service.py` |
| Dashboard/composer/detail views + chart JSON APIs | `crush_lu/admin/campaign_dashboard.py` |
| Click redirect | `crush_lu/views_campaign_click.py` (`/c/<token>/`) |
| Dispatch endpoint | `crush_lu/api_admin_campaigns.py` |
| Management command | `crush_lu/management/commands/dispatch_campaigns.py` |
| Timer trigger | `azure-functions/hybrid-maintenance/function_app.py` (`CampaignDispatch`) |
| Templates | `crush_lu/templates/admin/crush_lu/campaign_*.html` + `partials/_campaign_*.html` |
| Alpine CSP components | `crush_lu/static/crush_lu/js/alpine-components.js` (`campaignDashboardTabs`, `campaignComposer`, `campaign*Chart`) |

## 2. Data model

- **Campaign** — name, unique `slug` (= `utm_campaign`), `channels` JSON list,
  targeting mirroring Newsletter semantics (`audience`, `segment_key`,
  `language`), `audience_snapshot`, WhatsApp content (template name +
  parameters JSON with `{first_name}`/`{last_name}`/`{email}` merge tokens),
  push content (`push_title`/`push_body` translated en/de/fr via
  modeltranslation, `push_url`), lifecycle
  `draft → scheduled → sending → sent | partial | failed` (+ `cancelled`),
  `scheduled_at`, `dispatch_heartbeat_at` (overlap guard), `created_by`.
  `Campaign.stats` aggregates all channels + clicks.
- **Email leg** reuses `Newsletter` via a nullable `Newsletter.campaign`
  OneToOne — audience resolution, consent exclusions, i18n content, Graph
  rate limiting and `NewsletterRecipient` resumability come from the existing
  engine. Standalone newsletters are untouched.
- **CampaignRecipient** — per-(campaign, channel, user) state for WhatsApp
  and push. `WhatsAppMessage.user` is the *sending admin* (recipient is a
  phone string), so WhatsApp state cannot live on that model; each recipient
  row links to its `WhatsAppMessage`, and the Meta status webhook keeps
  delivered/read flowing into campaign stats with no `hub` changes.
- **CampaignLink / CampaignClick** — one link row per unique destination per
  channel; clicks store only link + attributed user + timestamp (**no IP, no
  user agent** — GDPR data minimization). Attribution rides in a signed `?r=`
  parameter and silently degrades to an anonymous click.

## 3. Consent gates (per channel, layered on the shared audience)

| Channel | Gate |
|---|---|
| Email | `EmailPreference.email_newsletter` + not `unsubscribed_all` (existing newsletter rules) |
| WhatsApp | explicit `EmailPreference.whatsapp_opt_in` (missing row = NOT opted in) + verified phone + not `not_on_whatsapp` + not `unsubscribed_all` |
| Push | an `enabled` `PushSubscription` exists |
| All | `UserDataConsent.crushlu_banned` always excluded; campaign `language` filter applies |

Unsubscribe links in campaign emails are never rewritten to tracked URLs.

## 4. Dispatch & scheduling

Production runs Django tasks on `ImmediateBackend` with no `db_worker`, so
sends are **never** run inline in a web request or a daemon thread. Instead:

- "Send now" sets `status='scheduled', scheduled_at=now` — the next timer
  tick picks it up (UI says "within ~5 minutes").
- Each tick (`dispatch_campaigns()`): promote due campaigns → claim each
  `sending` campaign via `dispatch_heartbeat_at` (stale after 15 min) → run
  every enabled channel's bounded batch → finalize when all channels report
  nothing left.
- Bounds per tick: **25 email** (exactly one Graph batch — no 62s pause
  inside a run), **30 WhatsApp** (~1s spacing), **150 push**, plus an ~80s
  wall-clock budget — the endpoint stays far below gunicorn's 120s timeout.
- Failed recipients are terminal for dispatch (no automatic retries of paid
  WhatsApp templates or bouncing addresses); an unlimited manual
  `send_newsletter` run still retries email failures.
- `send_newsletter(limit=N)` no longer finalizes a newsletter while eligible
  recipients remain (this also fixed the pre-existing `--limit` bug).

Enablement is two-layered: the Function App's `HYBRID_MAINTENANCE_ENABLED`
and Django's `CAMPAIGN_DISPATCH_ENABLED` (default **off**).

### Runbook

```bash
# Manual tick (Azure SSH shell or dev)
python manage.py dispatch_campaigns            # one bounded tick
python manage.py dispatch_campaigns --dry-run  # eligible counts, no sends
python manage.py dispatch_campaigns --campaign-id 3 --limit-email 5

# Endpoint (what the timer calls)
curl -X POST https://crush.lu/api/admin/campaigns/dispatch/ \
     -H "Authorization: Bearer $ADMIN_API_KEY"
# → 202 {"status": "ok", promoted, campaigns:[...]} or 200 {"skipped": true}
```

Azure setup: add `DJANGO_CAMPAIGN_DISPATCH_URL` to the
`crush-hybrid-maintenance` Function App settings and set
`CAMPAIGN_DISPATCH_ENABLED=true` on the App Service. Deployment is covered by
the existing `deploy-hybrid-maintenance-function.yml` workflow.

## 5. UI

`/crush-admin/campaigns/` — function-based views gated by the shared
coach-or-superuser check (`_check_admin_access`), registered in
`azureproject/urls_crush.py` before the `crush-admin/` site. Templates extend
`admin/base_site.html` and style with the panel's **theme-aware CSS
variables** (the coach panel has no Tailwind build — the original spec was
wrong about that). Interactivity is the **Alpine.js CSP build** (components
registered in `alpine-components.js`, no inline expressions), HTMX partials
for the composer's live estimate/preview and the detail page's 10s status
polling, and the **vendored Chart.js** fed by `{labels, datasets, summary}`
JSON endpoints.

Tabs: **Overview** (KPI cards + messages-per-week chart) · **Campaigns**
(unified table → detail) · **WhatsApp** (delivery funnel, outbound log,
inbound inbox) · **Reminders** (24h/72h/7d volumes + verified-after-reminder
conversion) · **Segments** (all `get_segment_definitions()` segments with
live counts and a one-click **Start campaign** shortcut that preselects the
segment in the composer).

Composer wizard: audience → channels & content (per-language email/push
variants optional, EN falls back) → preview + live per-channel estimate →
draft / send now / schedule (UTC).

## 6. Tests

- `crush_lu/tests/test_campaigns.py` — consent gates, resumability,
  bounded-run finalization regression, dispatcher lifecycle, create/estimate.
- `hub/tests/test_whatsapp_service.py` — extracted Meta send, template
  fetch cache, campaign WhatsApp leg incl. merge tokens + terminal failures.
- `crush_lu/tests/test_campaign_tracking.py` — UTM building, HTML rewriting,
  redirect + attribution, standalone newsletters untouched.
- `crush_lu/tests/test_campaign_dashboard.py` — access control, composer
  flow, cancellation, chart payload shapes.
- `crush_lu/tests/test_api_admin_campaigns.py` — Bearer auth, feature gate,
  end-to-end tick through the endpoint.

## 7. Out of scope (future)

- APNS/FCM native push broadcast (the adapter registry is the extension
  point; v1 push is Web Push only).
- SMS/Telegram/social channels; cross-domain campaigns (all targeting is
  Crush.lu's segment/consent data).
- Open-pixel tracking — deliberately omitted (unreliable + weaker GDPR
  posture); click + UTM tracking covers engagement.
- Click-row retention pruning (12-month sweep) — documented intent, not yet
  implemented.
