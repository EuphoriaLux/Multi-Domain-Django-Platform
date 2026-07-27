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
the scanner. It assigns **only while the member has no coach yet** — once
`assigned_coach_id` is set the receiver bails, so the first assignment sticks.

That asymmetry decides what is repairable on the night:

- **No coach assigned** (the event had none attached) → **repairable.** Attach a
  coach, re-save the attended registration, and the receiver runs.
- **Wrong or inactive coach assigned** → **not** repairable that way. The
  receiver bails on the existing `assigned_coach_id`, so re-saving does nothing
  and the profile has to be corrected directly.

- [x] **Confirm the event has at least one coach attached.** ✅ **Done
      2026-07-27** — production event **12** has four: Tom Swayer, Wesley,
      Matilde, Natascha. So nobody lands coachless for want of an assignment.
      An event with *no* coaches assigns **nobody**, silently, and **re-scanning
      does not repair it** — the QR endpoint returns early on a repeat scan
      (`views_checkin.py:91-110`), so the signal never runs again from the door.

      **It is recoverable, though, and this is worth knowing on the night.**
      `assign_coach_on_first_attendance` is an unconditional `post_save`
      receiver guarded only on `status == "attended"` and "no coach yet"
      (`signals.py:2416-2441`) — not a once-ever hook. So after attaching a
      coach to the event, an operator can **re-save the attended registration**
      (admin, or `reg.save()`) and the assignment runs then. The mistake costs a
      repair step, not the member's coach.
      ```
      python manage.py shell -c "from crush_lu.models import MeetupEvent; e=MeetupEvent.objects.get(id=12); print(e.title, list(e.coaches.all()))"
      ```
- [ ] **Confirm the coach it selects is `is_active`.** Attachment alone is not
      enough. `assign_coach_on_first_attendance` calls the **unfiltered**
      `instance.event.coaches.first()` (`signals.py:2435`) and permanently
      assigns whatever it returns — including a **deactivated** coach. Later
      My Crush routing then explicitly *skips* an inactive permanent coach
      (`models/connections.py:703-705`), so the member ends up holding an
      unusable assignment while their leads route elsewhere, and the ticked box
      above would have hidden it. **This is the non-repairable case** — once a
      coach is set the receiver bails, so a re-save will not correct it and the
      profiles have to be fixed by hand. Check the selected row *before* the
      doors open, not just the count:
      ```
      python manage.py shell -c "from crush_lu.models import MeetupEvent; c=MeetupEvent.objects.get(id=12).coaches.first(); print(c, 'is_active=', c.is_active if c else None)"
      ```
- [ ] **Decide whether one coach taking every attendee is what you want.**
      Four coaches on the event does **not** spread the load — there is no
      round-robin. `signals.py` calls `event.coaches.first()`, `CrushCoach` has
      no `Meta.ordering`, so Django's `first()` falls back to `order_by("pk")`.
      Every newly-coached attendee from event 12 therefore gets **the same
      single coach**: whichever of the four has the **lowest `CrushCoach.pk`**
      — the oldest coach record, not the first name in the admin widget and not
      the person scanning.

      That person then becomes the routed coach for those members' later
      My Crush! leads **only where no higher tier claims them**.
      `assign_coach()` (`models/connections.py:685-710`) resolves in order:
      (1) the requester's **approved `ProfileSubmission` coach**, if active;
      (2) `CrushProfile.assigned_coach`, if active; (3) an event coach. So a
      member who already has an approved submission under a different active
      coach routes to *that* coach, not to this one, and an inactive permanent
      coach is skipped. The concentration is real for members whose only coach
      link is this event, but it is **not** "every lead from event 12" —
      staffing decisions should not assume the stronger version. Confirm who
      the event tier resolves to:
      ```
      python manage.py shell -c "from crush_lu.models import MeetupEvent; print(MeetupEvent.objects.get(id=12).coaches.first())"
      ```
      If that is not the intended owner, the lever before Wednesday is which
      coaches are attached, not the check-in flow.
- [ ] **Walk a real QR check-in on staging end to end** — scan → `attended` →
      coach assigned → attendee appears where the post-event flow expects them.
      Not covered by this session's verification at all.

### 1.1b Event 12's profile requirement — changed mid-preparation

**Current setting: `completed`** ("Vollständiges Profil / Einstiegs-
veranstaltung"). It was `none` ("Kein Profil erforderlich") when this file was
first written on 2026-07-27 and was changed the same day. See
`2026-07-27-profile-requirement-audit.md` for the full option matrix.

`completed` is the right choice: it requires a profile, so the profile-less path
below **cannot arise for anyone registering from now on**, while still admitting
the member who has built their profile and verified their phone but is not yet
verified — the LuxID-less attendee who gets verified in person at the door.

- [ ] **But the change does not revalidate registrations already taken.**
      `profile_requirement` is enforced in `event_register` only, at the moment
      of registering. Anyone who registered while the event was `none` keeps
      their confirmed registration; the QR check-in endpoint never re-checks
      `profile_requirement`, and the attendance signal simply skips assignment
      when its fresh profile lookup returns nothing. **So the guarantee above
      applies to new registrations only — the existing cohort must be audited
      separately**, or profile-less attendees still arrive coachless on the day
      and their declarations still land in the pool:
      ```
      python manage.py shell -c "from crush_lu.models import EventRegistration; qs=EventRegistration.objects.filter(event_id=12, user__crushprofile__isnull=True); print(qs.count(), [r.user.email for r in qs])"
      ```
      Zero means the setting change fully closed it. Non-zero is the list to
      chase before Wednesday — each of those people needs a profile created
      *before* they are scanned.

The rest of this section documents the mechanism, which still applies to **any**
event left on `none` and to any pre-change registrations found above:

- **An attendee still profile-less _at check-in_ gets no coach**, even though
  the event has four. **The timing is what matters, not registration.**
  `event_register` proceeds with `profile = None`
  (`views_events.py:916-920`), but that value decides nothing later: the
  assignment signal runs its **own fresh `CrushProfile` query** when the
  registration flips to `attended` (`signals.py:2431-2435`) and bails at
  `if profile is None` only if the profile is *still* missing at that moment.

  So someone who registers without a profile and **creates one before being
  scanned does get the event coach normally**. Only someone still without a
  profile when they are scanned follows the path below — do not classify
  attendees, or their later leads, from how they registered.
- **Their "My Crush!" declaration lands unrouted, in the pool.**
  `declare_crush()` deliberately skips `assign_coach()` when the requester has
  no `CrushProfile`, so the lead has no owner. It surfaces in **every** active
  coach's inbox under the amber "Unclaimed" badge with its own `call_by` clock,
  and its note stays sealed until someone claims it.

  This is exactly the path the O12 build exists for, and exactly the path the
  earlier "unrouted leads are unreachable, leave as-is" decision would have left
  uncovered — that reasoning missed `profile_requirement="none"`. Verified
  working in the browser on 2026-07-27.

- [ ] **Brief the coaches that "Unclaimed" rows are real work**, not noise —
      needed only if the registration audit above returns a non-empty cohort,
      but cheap enough to do regardless.
      They get **no push notification** (§10.1 — deliberately not built), so a
      coach who never opens their inbox will not learn the lead exists, and the
      member is still promised a call within 48h.

### 1.2 Deploy-time ops steps that fail *silently*

Both are one-shot actions that nothing else will remind you about, and neither
raises an error when skipped.

- [ ] **Run `manage.py backfill_crush_recipient_coaches` after the swap.**
      Idempotent, supports `--dry-run`. Scope the expectation correctly: this
      only matters for pre-Phase-D leads whose recipient resolves to a
      **separate active co-coach**. A null `recipient_coach` is *not* itself a
      stall — where the recipient has no distinct active coach the command
      deliberately leaves the row null
      (`backfill_crush_recipient_coaches.py:148-163`) and the routed coach is
      given the recipient-consent controls instead
      (`views_coach.py:4572-4584`). Skipping the run strands only the
      genuine co-coach candidates, and those stall with no visible symptom.
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
      `DJANGO_CRUSH_LEAD_REMINDERS_URL`, then `ADMIN_API_KEY`.

      **All three fail the same silent way when *missing*.** Each is its own
      early `return` that logs and exits without raising
      (`function_app.py:66-74`), so Azure records the invocation as
      **successful** while Django is never called — including for a missing
      `ADMIN_API_KEY`. They are checked in the order listed and each returns, so
      with several unset **only the first is logged**: fixing one can simply
      reveal the next.

      Failures, by contrast, are **loud but not self-diagnosing**. A timeout, an
      unreachable host, a bad URL, a Django 500 and a wrong key all re-raise
      (`function_app.py:98-103`), so Azure marks the invocation **failed** in
      every one of those cases. A failed invocation therefore means "the call
      was attempted and did not succeed" — **not** "the key is wrong". Read the
      logged status before touching `ADMIN_API_KEY`: a **401** is the evidence
      for a wrong key; anything else points at the URL, the endpoint or the
      network.

      The asymmetry to remember: a *successful* invocation proves nothing on its
      own (it is also what all three missing-value skips look like), and a
      *failed* one names a problem without naming which. Read the Function's own
      logs, not Django's, and confirm it actually reached Django.

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
- [ ] **The committed `tailwind.css` IS what production serves — fix the
      pipeline, and never gitignore it.** An earlier draft of this file claimed
      production was unaffected by CSS drift because the deploy workflow rebuilds
      it. **That is wrong**, and the correction matters more than the original
      point:

      `deploy-azure-app-service-optimized.yml` runs `npm ci && npm run
      build:css:all` in the **`build` job** (`:57-61`) and then **uploads
      nothing** — the workflow contains no `upload-artifact`/`download-artifact`
      at all. The `deploy` job (`:64-`) takes a **fresh `actions/checkout`** and
      runs `collectstatic` over the **tracked** file, so the zip ships whatever
      is committed to git. The CSS build job is decorative; its output is
      discarded with the runner.

      Two consequences:
      - The drift found on this branch (last built 2026-07-23, templates changed
        2026-07-24 in #681, 5 utility classes missing) was **live on
        production**, not just a local-dev annoyance. The rebuild in this PR is a
        production fix.
      - **Gitignoring the file would ship production with no CSS.** The option
        floated earlier is actively harmful and must not be taken.

      **FIXED** — the `build` job now uploads all four platform bundles as a
      `built-css` artifact and `deploy` downloads them after checkout and before
      `collectstatic`, with `if-no-files-found: error` and an explicit
      non-empty check so a silent regression fails the deploy instead of
      shipping stale CSS again.

      Untracking `tailwind.css` becomes *technically* safe once that lands, but
      it is still not recommended: the file is useful for local dev without a
      build step, and keeping it tracked means the artifact and the repo can be
      diffed against each other. Revisit deliberately, not as a side effect.
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
