# Crush.lu Coach Panel (`/crush-admin/`) — Redesign Audit

> Phase 0 deliverable of the coach-panel redesign. This is the review that drives
> the Phase 1–3 implementation. Audience: **superuser** — nothing is removed from
> reach; the goal is organisation and findability.

## 1. Why

`CrushLuAdminSite` (`crush_lu/admin/site.py`) registers **~103 models across 17
sidebar groups**. All of them render **twice** — in the left `nav_sidebar` *and*
as 17 collapsible tables in the index body — with no prioritisation, so a
December-only Advent Calendar group carries the same visual weight as daily
profile review. The landing page also stacks Action Center + 4 stat cards + 11
quick-links + a 3-tab "Today's Focus" **on top of** that full model dump.

## 2. Group inventory & usage tiers

Tiers below become a declarative `TIER` field on the `custom_order` map in Phase 1.

| Group | ~Models | Tier | Rationale |
|---|---|---|---|
| Users & Profiles | 17 | **PINNED** | Daily profile review / coach assignment — the core job |
| Crush Connect | 7 | **PINNED** | The live matchmaking product (memberships, drops, sparks) |
| Events & Meetups | 8 | **PINNED** | Event ops, registrations, invitations |
| Connections | 3 | **PINNED** | Post-event mutual connections & messages |
| Notifications | 12 | **PINNED** | Push, newsletters, campaigns, email prefs, reminders |
| Special Journey | 10 | MORE | VIP journeys — created occasionally, not daily |
| Growth & Referrals | 2 | MORE | Referral tracking — weekly glance |
| Quiz Night | 6 | MORE | Only during live quiz events |
| Activity Voting | 4 | MORE | Only during live events |
| Advent Calendar | 5 | MORE | Seasonal (December only) |
| Wallet & Passes | 2 | MORE | Apple/Google wallet — rarely touched |
| Site Settings | 1 | MORE | Global config — set-and-forget |
| Matching | 2 | SUPERUSER | Trait/score engine internals |
| Analytics | 1 | SUPERUSER | Weekly KPI snapshots (internal) |
| Changelog | 2 | SUPERUSER | Patch notes |
| Technical & Debug | 3 | SUPERUSER | PWA installs, OAuth state, GDPR consent audit |
| Other | 0* | SUPERUSER | Auto catch-all — must stay empty for known models |

`PINNED` = expanded by default. `MORE` = collapsed by default. `SUPERUSER` =
collapsed **and** hidden from non-superuser coaches (existing
`SUPERUSER_ONLY_GROUPS` behaviour, preserved).

\* "Other" is a safety net; Phase 1 verification asserts no known model lands there.

## 3. Bespoke tool dashboards (not model admins)

These are the real high-value daily destinations, currently buried among the 11
flat quick-links. Phase 2 promotes them into a curated "Tools" grid.

| Tool | URL name | View |
|---|---|---|
| Analytics Dashboard | `crush_admin_dashboard` | `admin_views.crush_admin_dashboard` |
| Campaign Dashboard | `campaign_dashboard` | `admin/campaign_dashboard.py` |
| User Segments | `user_segments_dashboard` | `admin/user_segments.py` |
| Profile Reminders | `profile_reminders_panel` | `admin/profile_reminders.py` |
| Email Templates | `email_template_manager` | `admin_views` |
| Poll Analytics | `poll_analytics_dashboard` | `admin/poll_analytics.py` |
| Account Merge | `merge_accounts_confirm` | `admin_views.merge_accounts_confirm` |

## 4. Profile proxy models — decision

`Users & Profiles` carries **9 proxy models** that are all just
`CrushProfileAdmin` / `ProfileSubmissionAdmin` subclasses with a `get_queryset`
filter + `has_add_permission = False` (`crush_lu/admin/profiles.py`):

| Proxy | Filters | Equivalent changelist query |
|---|---|---|
| ApprovedProfile | `verification_status="verified"` | `crushprofile?verification_status__exact=verified` |
| AwaitingReviewProfile | `verification_status="pending"` | `…=pending` |
| IncompleteProfile | `verification_status="incomplete"` | `…=incomplete` |
| PendingReviewProfile | `profilesubmission__status="pending"` | via submission status |
| RevisionNeededProfile | `profilesubmission__status="revision"` | via submission status |
| RecontactCoachProfile | `profilesubmission__status="recontact_coach"` | via submission status |
| RejectedProfile | `profilesubmission__status="rejected"` | via submission status |
| CompletedSubmission | `status in [approved, rejected]` | `profilesubmission?status…` |
| InProcessSubmission | `status in [pending, revision, recontact_coach]` | `profilesubmission?status…` |

**Decision: keep them registered, stop listing them as 9 flat top-level rows.**
They provide genuinely useful curated querysets and are targeted by existing
deep-links (index Action Center → `crush_lu_profilesubmission_changelist?...`), so
**do not unregister** them. Instead, in Phase 1 they render as a **collapsed
"Profile segments" sub-list** nested under the two real models (`CrushProfile`,
`ProfileSubmission`). This removes ~9 rows from the always-visible menu while
keeping every view one expand away. Implemented purely in `get_app_list` ordering
+ the `nav_sidebar` override — **no model/migration changes**.

## 5. Index page audit

Current `templates/admin/crush_lu/index.html` (330 lines) renders, top to bottom:
Action Center → 4 stat cards → **11 quick-links** → 3-tab Today's Focus → **all 17
model groups as tables** → Recent Actions sidebar. The bottom half duplicates the
left nav.

**Decision:** the tiered `nav_sidebar` becomes the canonical model browser; the
index drops the 17-group dump and becomes a focused command center:
Action Center → trimmed stats → Today's Focus → curated **Tools grid** (§3).

## 6. Constraints carried into implementation

- Preserve the `get_app_list` `app_label is not None` guard (keeps `app_index.html`
  working).
- Keep the `"Other"` catch-all and `SUPERUSER_ONLY_GROUPS` gating.
- Reuse `admin-custom.css` `:root` tokens; brand hexes are synced across 3 files
  (`admin-custom.css`, `tailwind-src/crush_lu/tailwind-input.css`, `base_site.html`).
- Alpine CSP build: compose with `mixin` + `Alpine.data`, no inline arg expressions.
- Keep the proxy admins registered; only their *placement* changes.

## 7. Phase order

1. **Phase 1** — declarative tiers on `custom_order`, nested profile segments,
   `nav_sidebar.html` override (collapsible tiers + search).
2. **Phase 2** — index hub rewrite (drop model dump, add Tools grid).
3. **Phase 3** — visual pass on `admin-custom.css` + full verification.
