# Pre-event readiness — event Wednesday 2026-07-29

Written 2026-07-27 (Monday), **two days out**. Companion to
`2026-07-25-crush-my-crush-staging-verification.md`, which lists *how* to verify
"My Crush!"; this file lists *what is still owed* before the event and what has
already been proven.

`main` is **301 commits and 13 migrations** ahead of the last recorded
production SHA (`e2a9fd71`): migrations `0186`, `0189`–`0200`. That includes
Event Identity (#670–#679), My Crush Phases B–E (#680–#683, #687, #690), the
funnel/GDPR work (#658) and the campaign dashboard (#656). **None of it is on
production.** Everything below assumes one slot swap carries the lot — see the
slot-swap runbook in the memory hub.

---

## 1. Blocking — must be done before Wednesday

### 1.1 Check-in, and the coach it assigns

**This is the highest-risk untested path and it is event-day-critical.**
Marking a registration `attended` is what assigns a member's *permanent* coach,
and the coach chosen is `event.coaches.first()` — **not** the person operating
the scanner. It fires once per registration.

- [ ] **Confirm the Wednesday event has at least one coach attached.** An event
      with no coaches assigns **nobody**, silently. Every attendee then lands
      coachless, and because the assignment only fires once, re-running check-in
      later does not repair it. This is a one-line check and the single cheapest
      thing on this list:
      ```
      python manage.py shell -c "from crush_lu.models import MeetupEvent; e=MeetupEvent.objects.get(id=<ID>); print(e.title, list(e.coaches.all()))"
      ```
- [ ] **Confirm the coach it would pick is the one you actually want.**
      `.first()` is ordering-dependent, not "the event owner".
- [ ] **Walk a real QR check-in on staging end to end** — scan → `attended` →
      coach assigned → attendee appears where the post-event flow expects them.
      Not covered by this session's verification at all.

### 1.2 Deploy-time ops steps that fail *silently*

Both are one-shot actions that nothing else will remind you about, and neither
raises an error when skipped.

- [ ] **Run `manage.py backfill_crush_recipient_coaches` after the swap.**
      Idempotent, supports `--dry-run`. Without it, leads declared before Phase D
      have no `recipient_coach`, so the recipient-side outreach task exists for
      nobody and those introductions stall with no visible symptom.
- [ ] **Pin `CRUSH_LEAD_REMINDERS_ENABLED` slot-sticky.** Unpinned, the
      staging→production swap *exchanges* it. `infra/resources.bicep` carries a
      standing warning not to deploy as-is, so use the CLI:
      ```
      az webapp config appsettings set -g <rg> -n <app> --slot staging \
        --settings CRUSH_LEAD_REMINDERS_ENABLED=True \
        --slot-settings CRUSH_LEAD_REMINDERS_ENABLED
      ```
- [ ] **Set the three Function App settings** on `crush-hybrid-maintenance`, in
      the order `_call_admin_endpoint()` checks them:
      `HYBRID_MAINTENANCE_ENABLED="true"` (literal string), then
      `DJANGO_CRUSH_LEAD_REMINDERS_URL`, then `ADMIN_API_KEY`. The first two fail
      as **silent early returns**; a *mismatched* `ADMIN_API_KEY` instead fails
      loudly (Django 401 → failed Azure invocation), so check the Function's own
      logs, not Django's.

### 1.3 The reminder sweep, on staging

Every item here is a claim the unit tests make against a substitute (an injected
notifier, a fake clock, a hand-mutated row). None can be checked locally.
Detail and rationale in `2026-07-25-crush-my-crush-staging-verification.md`.

- [ ] A real reminder push arrives on a real device, and the body does **not**
      contain `requester_note` (push surfaces on a lock screen).
- [ ] **VAPID misconfiguration vs. opt-out are distinguishable.** Break the keys
      with a *malformed* value, run the sweep, confirm the claim is released and
      **no device is deleted or has `failure_count` raised** — a global config
      fault must not be charged to individual endpoints, or five sweeps delete
      every coach's devices.
- [ ] **Per-device key faults are the opposite case.** `POST` a garbage `p256dh`
      (`"%%%"`) to `/api/coach/push/subscribe/` → expect **400, no row**; and a
      pre-existing garbage row must be **deleted** after five sweeps. The
      boundary between this and the VAPID case was wrong in *both* directions
      during review — check them as a pair.
- [ ] A hung endpoint's `failure_count` actually climbs (timeout takes the
      catch-all branch, not `WebPushException`) and deletes on the fifth failure.
- [ ] Two overlapping timer deliveries send **exactly one** reminder per lead.
      `skip_locked` is the mechanism and SQLite never exercises it.
- [ ] The timer's 60s budget holds under a realistic backlog; `truncated` shows
      in logs rather than a silent partial sweep.

---

## 2. Should do — improvements, not blockers

- [ ] **DE/FR coach surfaces still render English.** The strings are extracted
      and in both catalogues with empty `msgstr`s, plus **390 `#, fuzzy`
      entries** that are deliberately excluded from the compiled `.mo`. Confirming
      a fuzzy entry is a keystroke; retyping it is not. **Do not clear them
      wholesale** — an earlier run destroyed ~261 recoverable translations per
      language to suppress a handful of obvious mismatches.
- [ ] **Reword the pool-lead sort claim** in the staging-verification checklist.
      It asks that an unclaimed lead "does not sort above a coach's own call at
      the same urgency". The sort key is `(priority, submitted_at)` and pool
      leads carry the *same* priority number, so at equal SLA the **older** row
      wins regardless of ownership — reproduced in the browser on 2026-07-27.
      The behaviour is correct (nearest deadline first within a bucket); the
      sentence is stricter than the code and will fail sign-off on working code.
- [ ] **Decide how the built `tailwind.css` stays in sync.** It is tracked but
      nothing rebuilds it on commit, so it drifted: as of this branch it was last
      built 2026-07-23 while templates changed 2026-07-24 (#681), leaving 5
      utility classes missing from the committed file. Production is unaffected
      (the deploy workflow runs `build:css:all` + `collectstatic`), so the choice
      is gitignore it or add a pre-commit rebuild.
- [ ] **Reminder sweep for pool leads is deliberately not built** (§10.1). A
      coach who never opens their inbox learns nothing about an unclaimed lead.
      Open ops question, not an oversight.

---

## 3. Verified 2026-07-27 — no action needed

Walked in a browser against local **Postgres** (not SQLite, so row locking is
real), driving the real service layer rather than hand-built rows.

- **Full flow, different coaches on each half:** declare → routed coach
  schedules + completes the call → records requester consent → co-coach reaches
  the recipient on the constrained surface → consent → approve → introduction →
  `shared`, timeline correct.
- **The privacy line holds.** The co-coach surface names only the recipient and
  the requesting coach; a scan of the full 191KB HTML response for the crusher's
  name, email and note text returned **zero hits**. A co-coach GETting
  `coach_connection_review` directly gets a **404**.
- **Terminal guard holds.** POSTing the legacy `approve`, `crush_start_review`
  and `crush_share` at a completed lead left it `shared` with `shared_at` intact.
- **Pool leads (O12).** Shown to every active coach with an amber "Unclaimed"
  badge and their own `call_by` clock; the note stays sealed until claimed;
  claiming unseals it and removes the row from other coaches' inboxes.
- **SLA lifecycle.** Completing a call removes the lead from the inbox — the
  "queue is read-only, leads never leave" gap is closed.
- **Mutual crush** flags and priority-boosts both sides.
- **Same-coach path** relabels the recipient half "your call too" instead of
  deadlocking.
- **UI:** no horizontal overflow at 375px with the full control set; light and
  dark both pass contrast; DE/FR render (in English) without breaking.
- **Tests:** 77 Phase E + 207 leads/connections/coach, all passing.

---

## 4. Fixed in this PR

- **`.btn-crush-solid` was missing from the canonical base button rule.** It is
  one of the four canonical variants in `crush_lu/STYLE.md`, and its own comment
  claims it is the "solid-purple sibling of `btn-crush-primary` — same
  shape/size/motion". It was not: absent from the base `@apply` list it rendered
  with `border-radius: 0`, `display: inline-block`, `cursor: default`, no focus
  ring and **no disabled styling at all**, so it sat square-cornered next to its
  pill-shaped outline siblings. The visible consequence was
  `coach_connection_review.html:371` "Make the introduction" — the final step of
  the flow — rendering **pixel-identical enabled and disabled** in both themes,
  so a coach clicks it and gets nothing. Added to the base rule and to the
  unlayered `/* Disabled State */` block, matching how `.btn-crush-primary` is
  handled.
