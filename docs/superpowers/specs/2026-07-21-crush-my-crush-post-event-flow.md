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
   reuse. **The genuinely net-new work is call-tracking fields, the
   lead-routing selection policy, a gender-independent crush counter, and
   directional duplicate handling (§7) — not the state machine.**

## 5. Mechanics (proposed)

* **Declaration:** post-event named attendees list (unchanged gating: attended
  + connection window open). Confirmation dialog: irreversible, sets the
  coach-call expectation. The optional 300-char `requester_note` stays but is
  explicitly **coach-facing** ("Tell your coach what happened — only they will
  read this"), so the Event Identity spec's no-free-text rule (profile
  surfaces) is unaffected.

* **Lead routing:** `assigned_coach` if set; else one of the **event's
  coaches** (`MeetupEvent.coaches` M2M, `crush_lu/models/events.py:239`,
  `related_name="assigned_events"` — already populated by event operations);
  else the coach-pool queue. Only the *selection policy* within
  `event.coaches` is net-new (§7, O11).

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
  whether a reverse row exists. Test explicitly (§13).

* **Status mapping (existing choices keep working,
  `connections.py:87–94`):** `pending` = new lead; `coach_reviewing` = call
  done / intro being arranged; `coach_approved` = both sides consented;
  `shared` = contact shared; `declined` = silent decline.

* **Upsell:** during the call the coach can naturally introduce Crush Connect
  ("this is what I do for Premium members every week"). No automated paywall
  inside the crush flow itself.

## 6. Coach capacity — the build gate

The entire value of this reframe is that a human calls the member. That makes
**coach time the binding constraint**, and it is currently unmodelled. Before
any build starts (Phase A), produce a written back-of-envelope estimate:

```
weekly coach-hours needed
  = events/week
  × attendees/event
  × crush declaration rate
  × (call length + intro-arrangement overhead)
```

Measure the inputs the same way §2 of the Event Identity spec was measured
(production Django shell, via Azure SSH). Then compare against **actual
available coach-hours/week** across the active coach roster.

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
  discloses a reverse row (§5).

* Migration is additive (nullable call-tracking fields); no data backfill.

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
   lobby recap for the same person at the same event. If both members hold
   lobby participation for the event (same eligibility service as lobby spec
   §5.1), the recap is their path and the attendees-page crush button redirects
   there ("You'll find them in your event recap — confirm you met."). This
   couples the crush flow to the lobby eligibility service — a new integration
   surface that must be tested (O7).
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

* **Phase A — capacity gate:** the §6 estimate, reviewed by Tom. Blocks
  everything else. Sets O8 and O9.

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
| O-cap | Coach-capacity estimate (§6) before build?                     | **Required.** Produce the written estimate in Phase A; it sets O8 and O9. Do not build to an assumed capacity.                                   |
| O7    | Confirm the one-pair-one-flow redirect (§9) for Connect pairs. | **Yes**, redirect — worth the complexity to avoid double-flow confusion; test the new coupling to the lobby eligibility service.                 |
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
  actions (e.g. their own declaration attempt) may reveal that a crush on them
  exists.

* Contact details are shared only after coach-facilitated mutual consent;
  declines never reach the crusher.

* No member is offered two post-event flows for the same pair.

* The coach navbar naming collision is resolved and recorded in the Connect
  copy glossary.
