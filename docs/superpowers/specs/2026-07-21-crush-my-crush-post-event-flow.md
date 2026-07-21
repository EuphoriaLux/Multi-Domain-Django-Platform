# "My Crush!" — Post-Event Connection Flow

**Date:** 2026-07-21

**Status:** Draft — product decisions proposed with recommended defaults,
pending Tom's confirmation (§11). **The build is gated on a coach-capacity
estimate (§6, O-cap) that does not yet exist.**

**Scope of this PR:** Planning only. No models, migrations, routes, templates,
JavaScript, tasks, or production behavior are changed by this document.

**Related documents:**
[Crush.lu Event Identity Redesign](2026-07-21-crush-event-identity-redesign.md),
[Crush Connect Event Lobby & "People I've Met"](2026-07-17-crush-connect-event-lobby-design.md),
[Crush Connect — Product Overview](../../products/crush-connect.md)

> **This spec was split off** the Event Identity redesign because it is a
> different kind of change: not a profile refactor but a **coach-operations
> workflow** — every crush becomes a human call with an SLA. Its load-bearing
> assumption is coach *time*, so §6 makes capacity an explicit build gate
> rather than an assumption.

## 1. Executive summary

Today the post-event `EventConnection` is a self-serve utility: a verified
attendee picks a name from the event's attendee list, attaches an optional
note, and waits for coach-facilitated contact sharing. Mechanically sound
(`crush_lu/models/connections.py:84`), emotionally flat — and it undersells the
strongest signal the platform ever gets.

This spec reframes it as **"My Crush!"** — the free member's flagship
post-event action: a private crush declaration that becomes a **coach lead**
and triggers a personal coach call while the feeling is fresh. The coach does
what no app can — asks how they met, reads the situation, gives advice, and
facilitates the introduction personally. It is the most natural Premium/Connect
conversion moment that exists, without a paywall screen.

## 2. Where "My Crush!" sits among the three post-event mechanisms

| <br />             | "My Crush!" (this spec)                             | Event Lobby (2026-07-17 spec)                | Connect matching             |
| ------------------ | -------------------------------------------------- | -------------------------------------------- | ---------------------------- |
| Who                | Any verified attendee                              | Checked-in, onboarded, LuxID Connect members | Connect members              |
| When               | 48h post-event window                              | Live during event + 48h recap                | Anytime                      |
| Identity           | Named attendee list                                | Photo-only until mutual                      | Blurred until mutual Spark   |
| Curation           | **Coach calls the member, facilitates personally** | None (anonymous, self-serve)                 | Coach's Pick / Drop / Sparks |
| Result             | Contact info shared after mutual consent           | People I've Met entry                        | Arranged date                |
| Member-facing name | **My Crush!**                                      | Event Lobby / People I've Met                | Crush Connect                |

## 3. Product decisions

| #  | Decision                                                                                                                                                                                            | Status   |
| -- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| D1 | Reframe the legacy post-event connection as **"My Crush!"** — a private crush declaration that becomes a coach lead, triggering a personal coach call (§4). No `EventConnection` status is removed. | Proposed |
| D2 | Coach curation stays the brand through-line: "My Crush!" = coach-facilitated connection for free members (event-scoped); Connect = Coach's Pick (Premium, continuous). The crush call is the Premium conversion moment. | Proposed |

## 4. The reframe

* The attendees-page button changes from "Send connection request" to
  **"My Crush!"** ("Who caught your eye?").

* A crush is **private**. The system never notifies the other person.

* Every crush creates a **coach lead**, not an email: the member's coach gets a
  call task with an SLA (§6/O8) while the feeling is fresh.

* The confirmation dialog sets the expectation: *"Your Crush Coach will call
  you within 48 hours to talk about it."*

* On the call the coach asks how they met, what they felt, reads the situation,
  gives advice — then facilitates the introduction personally (contacting the
  crush's coach when the crush is assigned elsewhere).

* Contact details are shared only after coach-facilitated mutual consent.
  Declines stay silent — consistent with Connect Sparks philosophy.

### 4.1 Why this wins

1. **Emotional timing.** A crush declared within 48h of meeting someone is the
   hottest lead the platform ever gets. Today it triggers a system email; the
   reframe triggers a human.
2. **Premium taste, free tier.** Every event attendee already gets a
   permanently assigned coach. The crush call lets free members *experience*
   personal coaching once — a natural conversion moment without a paywall.
3. **On-brand.** "Curated, never swiped" applies to events too: a human
   introduction, not a self-serve contact exchange.
4. **Infrastructure is mostly built.** `EventConnection` already has the
   `pending → accepted → coach_reviewing → coach_approved → shared` status set,
   `declined`, `assigned_coach`, `coach_notes`, `coach_introduction`, dual
   consent flags, and a 300-char `requester_note`
   (`crush_lu/models/connections.py:84–150`). The coach action queue,
   screening-call booking, and call-completion tracking exist as patterns to
   reuse. **The genuinely net-new work is scoped in §7 — call-tracking
   fields, a flow discriminator, the routing selection policy, a
   gender-independent counter, directional duplicate handling with the
   legacy mutual branch neutralized, and gating every legacy
   recipient-facing surface (notifications, inbox, chat) — not the state
   machine.** The state machine survives; the *reveal semantics* around it
   do not.

## 5. Mechanics (proposed)

* **Declaration:** post-event named attendees list (unchanged gating: attended
  + connection window open). Confirmation dialog: irreversible, sets the
  coach-call expectation. The optional 300-char `requester_note` stays but is
  explicitly **coach-facing** ("Tell your coach what happened — only they will
  read this"), so the Event Identity spec's no-free-text rule (profile
  surfaces) is unaffected.

* **Lead routing:** `assigned_coach` if set **and still active**; else an
  **active** one of the event's coaches (`MeetupEvent.coaches` M2M,
  `crush_lu/models/events.py:239`, `related_name="assigned_events"` — already
  populated by event operations); else the coach-pool queue. Every tier
  filters `is_active=True`: `coach_required` (`crush_lu/decorators.py:27`)
  bars deactivated coaches from all coach views, and §7's per-lead
  authorization hides the lead from everyone else — so routing to a stale
  coach would strand the lead unworkable until the SLA breaches. Only the
  *selection policy* within `event.coaches` is net-new (§7, O11). Test a
  stale assignment and a deactivated event coach falling through (§13).

* **The recipient's coach gets a defined work item.** When the recipient has
  their own (active) assigned coach different from the routed coach, the §4
  promise "contacting the crush's coach" must be trackable, not manual: the
  lead records the recipient-side coach, who receives a **recipient-scoped
  co-coach task** — recipient identity, event, and requesting coach only,
  **never the crusher's `requester_note`** (that stays with the routed coach)
  and not the crusher's identity until the coaches agree the intro is
  happening. Recipient outreach gets its own tracking timestamp so the SLA
  covers both halves. If the recipient has no active coach, the routed coach
  performs the outreach directly. **The task carries its own constrained
  actions** — without them the flow deadlocks: the §7 consent actions live
  on the lead review view, which the privacy model forbids the co-coach
  from opening, so the co-coach would have no auditable way to record the
  recipient's answer. The co-coach task exposes exactly three writes —
  *record outreach made*, *record recipient consent*, *record recipient
  decline* — each an audited narrow write to the lead (actor + timestamp)
  that never renders the lead itself or the crusher's identity. Recording
  consent notifies the routed coach, whose `share` action (§7) then
  completes the introduction. Test the full different-coach path to
  `shared` (§13).

* **Lead record:** extend `EventConnection` with call-tracking fields
  (`coach_call_scheduled_at`, `coach_call_completed_at`, `call_outcome`) or a
  linked `CoachLead` model — implementation choice (§7). Appears in the coach
  action queue with a "call by" timestamp; 24h reminder if untouched.

* **Crush limits:** the per-event limit needs a **gender-independent
  counter** — net-new. The existing accounting
  (`EventConnection.cross_gender_connection_count`,
  `crush_lu/models/connections.py:182`, read against the event's
  `max_cross_gender_connections`) only counts requests where the genders
  differ or are missing, so same-gender crushes would bypass a cap built on
  it and generate unbounded coach leads — invalidating the §6 capacity model.
  Count **all** crush declarations per (member, event) against the limit;
  proposed default **1 crush per event** (O9). Whether the legacy cross-gender
  mechanic stays alongside or is superseded is an implementation decision.

* **The other side:** the crush recipient hears nothing until the coach reaches
  out personally: *"Someone you met at {event} would like to know you better —
  may I introduce you?"* Consent before any share. Mutual crush pairs surface
  to both coaches immediately (priority lead).

* **Gate every legacy recipient-facing surface.** Creating a `pending`
  `EventConnection` today immediately notifies the recipient
  (`notify_new_connection`, `crush_lu/views_connections.py:420`, `:601`),
  lists the row in their inbox with an Accept/Decline card
  (`received_pending` → `received_requests`, `views_connections.py:875`),
  and exposes it on `connection_detail`. The attendee page additionally
  shows an **aggregate hint** — `incoming_pending_count`
  (`views_connections.py:144–146`) renders "Someone here wants to connect
  with you" from the count of received `pending` rows — which would reveal
  that *a* crush exists even with all per-row surfaces hidden. The **sitewide
  badge counters leak too**: the context processor
  (`crush_lu/context_processors.py:96–115`) computes
  `pending_requests_count` from every received `pending` row and
  `connection_count` from `accepted`/`coach_reviewing`/`coach_approved`/
  `shared` rows in either direction, and `base.html`, `dashboard.html`, and
  the mobile nav render them — so a recipient's badge increments the moment
  a crush is declared, and again when the coach starts review. Left as-is, a
  one-sided crush is revealed within seconds. Crush leads must be excluded
  from recipient notifications, the recipient's `my_connections` inbox,
  attendee-page connection state, the `incoming_pending_count` aggregate,
  **both context-processor counters**, **the post-event recap email's
  waiting-connections count** (`send_event_recap`,
  `email_helpers.py:920–922`), and `connection_detail` **until the
  introduction completes (`shared`)** — not merely until the recipient's
  consent is recorded. The coach workflow records the two consents
  independently (§7), so there is an intermediate state where the recipient
  has consented but the requester has not; un-hiding at recipient-consent
  would let `connection_detail.html` render the crusher's display name,
  city, and languages while the outreach is still the anonymous "someone
  you met" — an identity disclosure before the completed introduction. §13
  tests that the recipient cannot discover the row (or any hint or badge
  change) from any existing page in **every** pre-`shared` state, including
  after their own consent is recorded.

* **Declines must be invisible to the crusher too.** `connection_detail`
  authorizes *either* party of a row (`views_connections.py:917–921`) and
  `my_connections` lists every outgoing connection — so flipping a crush
  lead to `declined` would reveal the outcome via a bookmarked detail URL or
  the sent-requests state. For crush rows, every requester-facing surface
  renders a **neutral state** ("your coach will contact you") regardless of
  whether the actual status is `pending`, `declined`, or mid-coach-workflow;
  only a completed introduction (`shared`) is ever distinguishable. Cover
  both the list and the direct detail endpoint (§13).

* **Reciprocal declarations must work — today they can't.** Both
  `request_connection` guards reject a new request when a row exists in
  *either* direction (`crush_lu/views_connections.py:295–302` and `:488–491`),
  so after A declares, B's independent reverse declaration is refused and the
  mutual-crush path is unreachable. Worse, the "Connection request already
  exists" warning on B's *first* attempt reveals that A already declared —
  a privacy leak that directly contradicts "the recipient hears nothing."
  Required change: block only same-direction duplicates
  (`unique_together ('requester','recipient','event')` already permits the
  reverse row), accept the reverse declaration silently, and never expose
  whether a reverse row exists. **And neutralize the legacy mutual branch
  before relaxing the guard:** today a detected mutual auto-accepts, sends
  celebratory notifications to both members, and for same-gender pairs even
  auto-marks the rows `shared` with contact consent
  (`views_connections.py:357–408`, `:548–598` — dead code behind the current
  guard, but it comes alive the moment the guard is relaxed). For crush
  leads the reciprocal transition does exactly one thing: creates the second
  lead and flags both coaches as a priority pair — no status change, no
  auto-consent, no member notification. Test both same- and cross-gender
  pairs explicitly (§13).

* **Status mapping (existing choices keep working, `connections.py:87–94`;
  crush rows additionally carry the §7 flow discriminator):** `pending` =
  new lead; `coach_reviewing` = call done / intro being arranged;
  `coach_approved` = both sides consented; `shared` = contact shared;
  `declined` = silent decline.

* **Upsell:** during the call the coach can naturally introduce Crush Connect
  ("this is what I do for Premium members every week"). No automated paywall
  inside the crush flow itself.

## 6. Coach capacity — the build gate

The entire value of this reframe is that a human calls the member. That makes
**coach time the binding constraint**. A lead costs coach time on **both
sides**, not one call: the routed coach calls the requester, and then the
recipient must be reached for consent — by the recipient's own coach (co-coach
task + handoff, §5) or by the routed coach directly. The estimate models both
halves separately:

```
hours per lead   = requester call (~20–30 min)
                 + recipient outreach (~10–15 min)
                 + co-coach handoff overhead when coaches differ (~10 min)
weekly coach-hours = events/week × attendees/event × declaration rate
                   × hours per lead
```

### Phase A measurement (production, 2026-07-22)

Measured via the production Django shell (same access pattern as the Event
Identity spec §2):

* **11 past events** Feb–Jun 2026 ≈ 2.4/month; attendance 11–41 declining
  (recent events ~11–14); 1 event scheduled in the next 60 days.
* **Declaration rate 0.36/attendee** (79 `EventConnection`s / 222 attended)
  under the existing `max_cross_gender_connections=1` — i.e. the proposed
  limit-1 behaviour is already in force, and ~1 in 3 attendees uses it. One
  event hit 10/11 (91%).
* **9 active coaches** (11 total); 463 members hold an assigned coach.
* Corroborating facts: 41% of all connections ever are `declined`
  (requester-side silence matters, §5); **0 mutual pairs have ever
  existed** (the legacy auto-share branch has never fired in production);
  `PremiumMembership` has 0 rows (the conversion story starts from zero).

| Scenario | Leads/week | Coach-hours/week (0.75–1.25 h/lead, two-sided) | Per active coach |
| --- | --- | --- | --- |
| Today (2.4 ev/mo × ~15 att × 0.36) | 3–4 | 2.5–5 h | ~20–35 min |
| Growth (weekly event, 30 att) | ~11 | 8–14 h | ~1–1.5 h |
| Worst case (every attendee, limit 1) | ~30 | 22–38 h | ~2.5–4 h |

**Verdict: absorbable at current scale with margin** — O8 (48h SLA, 24h
reminder) and O9 (limit 1) are confirmed by measurement, not assumption.
The growth scenario stays manageable; the worst case is the ceiling the
limit exists to prevent. Re-run the numbers at build time if event cadence
or attendance has shifted materially.

The result *sets*, rather than assumes, two decisions:

* **O8 (SLA):** a 48h call SLA is only credible if the model shows coaches can
  absorb the peak. If not, lengthen the SLA or lower the limit.
* **O9 (per-event crush limit):** 1/event is the proposed default precisely
  because it bounds this number.

**Acceptance:** the SLA and crush limit are set from this written estimate, not
asserted. If the estimate shows the load is not absorbable at any acceptable
SLA, this feature does not ship as specced — it becomes a smaller pilot (e.g.
one flagship event) first.

## 7. Model & migration plan

* No `EventConnection` status is added or removed. Existing statuses (§5) carry
  the lead lifecycle.

* **Call-tracking** — either add fields to `EventConnection`
  (`coach_call_scheduled_at`, `coach_call_completed_at`, `call_outcome`
  choices) or introduce a linked `CoachLead` model. Recommendation: add fields
  to `EventConnection` — it already *is* the lead, and a second model
  duplicates routing/consent state. Revisit only if leads must exist without a
  target (they don't here).

* **Routing tier** — `EventConnection.assign_coach()`
  (`crush_lu/models/connections.py:220`) today assigns the requester's approved
  `ProfileSubmission` coach, else **any active coach**. The event→coach
  association **already exists** — `MeetupEvent.coaches`
  (`crush_lu/models/events.py:239`) is actively populated by event
  operations, so the middle tier must reuse it, not introduce a parallel
  source. Net-new is only the **selection policy** when an event has several
  coaches (e.g. least-loaded by open crush leads, else first by id) and the
  final coach-pool fallback ordering.

* **Gender-independent crush counter** — a per-(member, event) count of all
  declarations, enforced at declaration time under concurrency (§5). The
  existing `cross_gender_connection_count` cannot serve as the capacity
  bound (it skips same-gender pairs).

* **Directional duplicate guard** — replace the either-direction existence
  check in both `request_connection` guards
  (`crush_lu/views_connections.py:295–302`, `:488–491`) with a
  same-direction-only check, so reciprocal declarations create the second row
  (the model's `unique_together` already allows it) and no response ever
  discloses a reverse row — with the legacy mutual auto-accept/auto-share
  branch neutralized for crush rows first (§5). **The mutual-priority flag
  must survive a concurrent race:** simultaneous A→B and B→A submissions
  insert different unique keys, so each transaction can pass its reverse-row
  check before the other commits — both rows land and neither is flagged.
  Either serialize on a canonical pair key (lock ordered
  `(min(user_a,user_b), max(…), event)` before the check) **or** make
  mutual-priority *derived/reconciled idempotently* after commit (the
  `annotate_is_mutual` pattern already computes it) so the flag appears
  regardless of interleaving. Test simultaneous reciprocal declarations
  (§13).

* **Flow discriminator** — a durable field on `EventConnection` (e.g.
  `flow` = `legacy` | `crush`, or `declared_as_crush` boolean). Without it,
  a crush lead is indistinguishable from every pre-launch row still
  `pending`: the coach queue would generate crush-call tasks (and SLA
  reminders) for old generic requests, while filtering on the new nullable
  call fields alone would miss fresh rows until first write. Queue, SLA and
  reminder machinery key off the discriminator only; historical `pending`
  rows never become crush-call tasks (§13). The additive migration stays
  backfill-free — legacy rows simply keep `flow=legacy`.

* **Messaging locked for crush leads** — `connection_detail` currently
  serves `ConnectionMessage` chat on accepted/coach-reviewing rows
  (`views_connections.py:51`, `:973`), which would let a pair exchange
  contact details before dual consent, contradicting the coach-facilitated
  introduction and the no-chat non-goal (§12). Crush leads expose no message
  creation, polling, or notification in any pre-`shared` status — enforced
  server-side, not merely hidden in the template.

* **Coach-side consent recording and the `shared` transition.** In the
  legacy flow the two consent flags are set only through **member**
  `connection_detail`/response posts, and the coach's `approve` action sets
  nothing but `coach_approved` — so with member surfaces suppressed for
  crush rows (§5), a lead would have **no path from the coach calls to a
  completed introduction**. Net-new coach actions on the review view:
  record the requester's and the recipient's **call-obtained consent**
  individually (each an audited write: actor coach + timestamp), and a
  final **share** action that performs the contact disclosure. `share` is
  valid only when both consents are recorded — reuse the
  `can_share_contacts` invariant (`crush_lu/models/connections.py:212–218`)
  rather than bypassing it. The member-facing consent controls stay disabled
  for crush rows; consent is given verbally to the coach, recorded by the
  coach. When the coaches differ, the recipient's consent arrives through
  the co-coach task's constrained actions (§5) — the review-view
  recipient-consent action covers the same-coach and no-recipient-coach
  cases. Test that one recorded consent never releases either side's
  contact details (§13).

* **Coach workflow: reciprocal crush leads stay independent.** The existing
  `coach_connection_review` treats a reverse row as part of one legacy
  connection: it loads it (`views_coach.py:3748–3753`) and `start_review`
  (`:3773–3778`), `approve` (`:3813–3833`) and `claim` (`:3848–3850`) all
  overwrite the reverse row's `assigned_coach` and status — approve even
  copies `coach_notes`/`coach_introduction` onto it. With reciprocal crush
  leads routed to *different* coaches (§5), the first coach acting would
  hijack the other coach's lead and promote it to `coach_approved`, opening
  consent surfaces before that coach's outreach. Every reverse-row coupling
  in the coach workflow is bypassed for `flow=crush`; mutual pairs are
  *flagged* to both coaches, never merged. Test independently assigned
  reciprocal leads (§13).

* **Coach authorization is server-side per lead.** `coach_connections`
  starts from **every** `EventConnection` (`views_coach.py:3590–3596` — the
  code even notes "or all for now") and `coach_connection_review` checks
  ownership only on POST (`:3758–3764`), so today any coach can open any
  lead and read the supposedly coach-only `requester_note`. For
  `flow=crush`: the queue queryset is filtered to the routed coach, and
  object-level authorization applies to **GET and POST** on the review view.
  A non-routed coach gets no queue row and no detail response (§13).

* **Member-overview surfaces redact crush rows.** The queue and review view
  are not the only coach-facing disclosure path: any active coach can open
  `coach_member_overview` for an arbitrary member
  (`views_coach.py:2799–2837`), which loads that member's connections in
  both directions and renders the incoming requester's name
  (`coach_member_overview.html`). An unrelated coach — or the recipient's
  co-coach *before* the coaches have agreed to introduce — would learn the
  crusher's identity there. `flow=crush` rows are filtered or redacted on
  `coach_member_overview` and every equivalent member-detail surface
  (`coach_verification_channel`, `crush_connect/coach_member_detail`, …):
  visible in full only to the routed coach; the recipient-side co-coach
  sees only their recipient-scoped task (§5). Authorization tests cover an
  unrelated coach and a pre-agreement co-coach (§13).

* **The 24h reminder needs real scheduler wiring, not just a field.**
  Follow the platform's existing pattern: an Azure Function timer calling a
  language-neutral admin endpoint (`ADMIN_API_KEY`-authenticated, like the
  SLA-sweep / KPI timers on `crush-hybrid-maintenance`) that runs a
  management-command sweep over crush leads with `call by` overdue and no
  reminder recorded. Requires: the endpoint + command, a feature gate, a
  `DJANGO_*_URL` env var on the function app (ops note: unset = the timer
  silently no-ops — a known failure mode of this pattern), and an
  idempotency record (e.g. `reminder_sent_at`) so repeated timer delivery
  never double-reminds. The sweep (and the queue) select **live actionable
  statuses only** — a member block silently flips an in-flight
  `EventConnection` to `declined` (and a coach-recorded decline has the
  same shape) without setting any call/reminder field, so an
  overdue-and-unreminded filter alone would remind a coach about a
  cancelled pair. A decline or block also cancels the recipient-side
  co-coach task. Integration-test repeated delivery and a block/decline
  landing before the 24h deadline (§13).

* Migration is additive (nullable call-tracking fields + the discriminator
  with a `legacy` default); no data backfill.

## 8. Surface changes

| Surface                                | Today                                   | After                                                                 |
| -------------------------------------- | --------------------------------------- | --------------------------------------------------------------------- |
| `event_attendees.html` (crush button)  | "Send connection request"               | **"My Crush!"** ("Who caught your eye?") + confirmation dialog        |
| `request_connection.html`              | interests free text; generic request    | "My Crush!" declaration (irreversible, sets coach-call expectation) + Event Identity chips (from the Event Identity spec) |
| `connection_detail.html`               | bio + interests                         | My Crush status / call expectation + Event Identity chips             |
| Coach action queue                     | connection requests                     | crush leads with a "call by" timestamp; 24h reminder if untouched     |
| Coach dropdown (naming, O10)           | "My Crush" (`base.html:365` desktop, `base.html:896` mobile) | rename to "My Dating Profile" in **both** locations       |

All chip rendering on these surfaces reuses the Event Identity taxonomy; no
`bio`/`interests` free text renders (that guarantee is shared with the Event
Identity spec §7).

## 9. Relationship to the Event Lobby and Connect

1. **One pair, one flow.** A member must never see both "My Crush!" and the
   lobby recap for the same person at the same event. The redirect criterion
   is the **read-time recap roster** — `eligible_participations(event)`
   (`crush_lu/services/event_lobby.py:221`), which rechecks approval, LuxID,
   onboarding, coach exclusion and photo consent when rendering — **not**
   mere `EventLobbyParticipation` row existence, because rows outlive
   eligibility. Only when *both* members are in the current roster does the
   crush button redirect to the recap ("You'll find them in your event
   recap — confirm you met."); if either side has since dropped out of
   eligibility, the recap can no longer show them, so My Crush stays
   available as the only remaining flow. **The recap must also still be
   open:** `eligible_participations` filters members, not phases — and
   `connection_window_hours` is per-event configurable and can exceed the
   lobby's fixed 48h recap, so both rows can be eligible while the recap is
   already closed. **And eligibility alone is not visibility:** the real
   recap roster (`get_recap_roster`,
   `crush_lu/services/event_lobby.py:770`) further subtracts blocked pairs
   and pending/approved encounter removals (`hidden_encounter_user_ids`,
   `:731`) — a pair can pass `eligible_participations` yet be mutually
   invisible in the recap. Nor do participation rows imply the feature is
   on: rows outlive a `CRUSH_EVENT_LOBBY_ENABLED` flag-off (`:67`), after
   which lobby views 404. The redirect predicate is therefore: *the lobby
   flag is enabled (`lobby_feature_enabled()`), the recap window is open,
   and the target is actually present in the requester's `get_recap_roster`
   for the event*. When the predicate fails for a **phase/feature** reason —
   closed recap, flag off — My Crush applies even for an eligible pair. But
   when it fails for a **pair-level** reason — the counterpart is hidden by
   a pending/approved encounter removal or a block — **neither flow is
   offered**: falling back to My Crush would let the other party route a
   coach at someone who had the encounter removed, defeating the removal
   policy. Removal pairs mirror block semantics on the crush surfaces: the
   target is absent from the requester's attendee list, and a direct POST is
   rejected without disclosing why. **The check is enforced in the
   write endpoints, not just the button:** both `request_connection`
   (`views_connections.py:281`) and `request_connection_inline` (`:443`)
   accept direct POSTs and currently never consult
   `eligible_participations` — a stale form or hand-crafted request could
   create a crush for a lobby-eligible pair despite the invariant. Both
   endpoints perform the same read-time roster check and redirect instead
   of creating the row; test a direct POST for an eligible pair (§13). This
   couples the crush flow to the lobby eligibility service — a new
   integration surface that must be tested (O7).
2. **Cross-world bridge.** A Connect member may still declare "My Crush!" on a
   **non-Connect** attendee; the lead goes to their assigned Premium coach.
3. **Different questions, no merge.** "People I've Met" (lobby) is a *record of
   encounters*; "My Crush!" is an *intent signal that triggers human action*.
   They must not merge.
4. The lobby itself is unchanged; its 2026-07-18 amendment (attendees page and
   connection endpoints closed during the live phase; 48h shared window)
   already prevents live-phase overlap.

### 9.1 Long-term convergence (deferred)

Once the lobby has real usage (recap open rate, confirmation rate, encounters
per event) and "My Crush!" has lead/call metrics (calls completed, intros
accepted, Premium conversions), revisit whether LuxID-verified attendees move
entirely to recap mechanics and the named-attendee crush list is retired for
that segment. Out of scope here.

## 10. Delivery

* **Phase A — capacity gate:** the §6 estimate — **measured 2026-07-22**,
  pending Tom's review. Sets O8 and O9; re-verify at build time if event
  cadence or attendance has shifted.

* **Phase B — lead model:** §7 call-tracking fields, routing tier, coach action
  queue integration, tests.

* **Phase C — member UI:** attendees-page copy, crush declaration flow with
  coach-call expectation, one-pair-one-flow redirect (§9.1), upsell copy.

* **Phase D — coach UI & notifications:** lead queue with "call by" timestamp,
  24h reminder, mutual-crush priority flagging, recipient-outreach copy.

* **Phase E — launch readiness:** translations (EN/DE/FR), accessibility,
  dark/light, mobile widths. **Update `docs/products/crush-connect.md` §9 copy
  glossary** to record the "My Crush!" (member feature) vs "My Dating Profile"
  (coach nav) naming — it is currently absent there.

This spec is independent of the Event Identity redesign phases and can ship
before or after them.

## 11. Open decision points (for Tom)

| #     | Question                                                        | Recommended default                                                                                                                             |
| ----- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| O-cap | Coach-capacity estimate (§6) before build?                     | **Measured 2026-07-22** (§6): 3–4 leads/week today, 2.5–5 two-sided coach-hours across 9 active coaches — absorbable with margin. O8/O9 defaults confirmed by data; re-run at build time if cadence shifts. |
| O7    | Confirm the one-pair-one-flow redirect (§9) for Connect pairs. | **Yes**, redirect — gated on the read-time recap roster (`eligible_participations`), with My Crush as the fallback when either side is no longer eligible; test the coupling to the lobby eligibility service. |
| O8    | Coach-call SLA for crush leads.                                | **Call within 48h, reminder at 24h** — *conditional on the O-cap model showing it is absorbable*; otherwise lengthen.                            |
| O9    | Crush limit per event.                                         | **1 per event for free AND 1 for Connect members** — do not make Connect unlimited; scarcity protects signal quality and bounds coach load. Enforced by a **gender-independent** counter (§5/§7) — the legacy cross-gender counter cannot bound coach load. |
| O10   | Resolve "My Crush" naming collision with the coach navbar dropdown. | **Rename the coach dropdown to "My Dating Profile"** in both nav locations (`base.html:365` desktop, `base.html:896` mobile); member feature keeps "My Crush!". Record in `docs/products/crush-connect.md` §9. |
| O11   | Lead routing when the crusher has no assigned coach.           | **Reuse `MeetupEvent.coaches`** (`events.py:239`) as the middle tier — the association already exists; only the selection policy among an event's coaches is net-new (§7). Then the coach-pool queue. Until built, `assign_coach()` falls back to any active coach. |

## 12. Non-goals

* No change to Event Lobby mechanics, eligibility, or anonymity rules.

* No change to `EventConnection` statuses or the connection-window computation.

* No chat or messaging anywhere (lobby spec §3 stands).

* No retirement of the legacy named-attendee connection flow (§9.1 deferred).

* No profile/taxonomy changes — those live in the
  [Event Identity spec](2026-07-21-crush-event-identity-redesign.md).

## 13. Test plan (implementation PRs)

* **Declaration** creates exactly one coach lead with a "call by" timestamp.

* **Routing** follows assigned → event coach (`event.coaches`) → pool; verify
  the selection policy among multiple event coaches and the pool fallback.

* **Reciprocal declarations:** after A declares on B, B's independent
  declaration on A succeeds, creates the second directional row, and B's
  response is byte-identical whether or not A's row exists (no leak); a
  same-direction duplicate is still rejected.

* **Reciprocal never auto-shares:** for both same-gender and cross-gender
  pairs, the reverse declaration leaves both rows `pending` with consent
  flags false, sends no mutual notifications, and flags both coaches as a
  priority pair — the legacy auto-accept/auto-share branch never fires for
  crush rows.

* **Recipient invisibility:** after a declaration, the recipient's
  `my_connections` inbox, notification feed, attendee page, and
  `connection_detail` show nothing; `notify_new_connection` is not sent for
  crush leads; `pending_requests_count` and `connection_count` (context
  processor) are unchanged for the recipient in **every** pre-`shared`
  status — `pending`, `coach_reviewing`, `coach_approved`, **and after the
  recipient's own consent is recorded while the requester's is not**.

* **Recap email excluded:** the recurring post-event recap
  (`send_event_recap`, `crush_lu/email_helpers.py:884`, batch command
  `send_event_recaps`) counts the recipient's received `pending` rows for
  the event (`:920–922`) and emails "people are waiting to hear back" with
  an attendee-page CTA — one more channel that would announce a private
  crush. `flow=crush` rows are excluded from that count; a recipient whose
  only received rows are crush leads gets no waiting-connections section
  (and no email, if that was its only trigger).

* **Member-overview redaction:** an active coach who is neither the routed
  coach nor the recipient-side co-coach sees no crush row (and no requester
  name) on `coach_member_overview` or equivalent member-detail surfaces;
  the co-coach sees the recipient-scoped task but not the crusher's
  identity pre-agreement.

* **Write-endpoint roster check:** a direct POST to `request_connection` or
  `request_connection_inline` for a currently lobby-eligible pair creates
  no row and redirects to the recap.

* **Flow discriminator:** historical pre-launch `pending` connections never
  appear in the crush queue and never receive SLA/reminder tasks.

* **Messaging locked:** `ConnectionMessage` creation and polling are
  rejected server-side for crush leads in every pre-`shared` status.

* **Redirect fallback:** a pair with lobby-participation rows where one side
  has lost read-time eligibility gets the My Crush flow, not a dead-end
  recap redirect; likewise an eligible pair whose recap window has already
  closed (e.g. `connection_window_hours` > 48) and any pair after
  `CRUSH_EVENT_LOBBY_ENABLED` is switched off — never a redirect into a
  closed or 404 recap.

* **Removal pairs get neither flow:** for a pair hidden by a
  pending/approved encounter removal (`hidden_encounter_user_ids`), the
  counterpart is absent from the requester's attendee list, a direct
  declaration POST is rejected without disclosing the reason, and no recap
  redirect occurs — the removal policy is never defeated by a crush
  declaration.

* **Requester decline suppression:** after a coach records a decline, the
  crusher's `my_connections` list and a direct `connection_detail` fetch
  both render the neutral state — byte-identical to an untouched pending
  lead.

* **Reciprocal leads stay independent:** with A's and B's leads routed to
  different coaches, `start_review`/`approve`/`claim` on one lead leaves the
  other lead's `assigned_coach`, status, and notes untouched.

* **Coach authorization:** a coach who is not the routed coach sees no crush
  queue row and gets no detail response on GET; the routed coach sees
  exactly their own leads.

* **Reminder idempotency:** delivering the timer sweep twice for the same
  overdue lead produces exactly one reminder; a lead with
  `coach_call_completed_at` set is never swept; a lead flipped to
  `declined` before the 24h deadline (member block or coach decline) is
  never swept and its co-coach task is cancelled.

* **Aggregate hint suppressed:** a recipient whose only received rows are
  crush leads sees no "Someone here wants to connect with you" alert —
  `incoming_pending_count` is 0.

* **Stale-coach routing:** a deactivated `assigned_coach` and a deactivated
  event coach each fall through to the next tier; no lead is ever routed to
  an inactive coach.

* **Co-coach work item:** for a pair assigned to different coaches, the
  recipient's coach receives the recipient-scoped task (without
  `requester_note` or premature crusher identity) and the routed coach keeps
  the lead; recipient-outreach tracking is SLA-visible. The **full
  different-coach path completes**: co-coach records outreach + recipient
  consent via the constrained task actions (never opening the lead), the
  routed coach records requester consent and shares — the lead reaches
  `shared` with both writes audited.

* **Reciprocal race:** simultaneous A→B and B→A declarations (concurrent
  transactions) both commit and end flagged as exactly one mutual-priority
  pair — via pair-key serialization or idempotent reconciliation, whichever
  §7 approach is implemented.

* **Coach-side consent:** recording one consent does not release any contact
  details; the share action succeeds only with both consents recorded and is
  fully audited (actor + timestamps).

* **Crush limit is gender-independent:** a same-gender declaration counts
  against the per-event limit exactly like a cross-gender one.

* **Recipient** is never notified by the system at any point before coach
  outreach.

* **Mutual crush** pairs flag both coaches as a priority lead.

* **Per-event crush limit** enforced under concurrent submissions.

* **Statuses** map as specified in §5 (`pending`/`coach_reviewing`/
  `coach_approved`/`shared`/`declined`).

* **One-pair-one-flow:** a crush declaration between two lobby participants
  redirects to the recap; mixed pairs (Connect + non-Connect) keep the My Crush
  flow; block rules intact.

* **Coach-facing note:** the 300-char `requester_note` renders only to coaches,
  never on profile/attendee surfaces.

* **i18n:** every new string translated EN/DE/FR.

## 14. Acceptance criteria

* The per-event crush limit and SLA are set from the written §6 capacity
  estimate, not assumed.

* Every "My Crush!" declaration produces a coach lead visible in the coach
  action queue within seconds, with a "call by" timestamp no later than the
  agreed SLA after declaration.

* The crush recipient receives no system notification at any point before coach
  outreach — including indirectly: no response to any of the recipient's own
  actions (e.g. their own declaration attempt) and no existing surface (inbox,
  notification feed, attendee page, connection detail, chat) may reveal that a
  crush on them exists.

* Contact details are shared only after coach-facilitated mutual consent;
  declines never reach the crusher.

* No member is offered two post-event flows for the same pair.

* The coach navbar naming collision is resolved and recorded in the Connect
  copy glossary.
