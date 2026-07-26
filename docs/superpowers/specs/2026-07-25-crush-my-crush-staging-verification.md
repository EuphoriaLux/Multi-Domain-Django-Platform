# "My Crush!" — staging verification checklist

Spec: `docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md`

**Status: open, and now unblocked.** Phase E's code has landed (O12 pool
section, O13 backfill command, O14 claim-then-send, translation extraction), so
the end-to-end path exists and this checklist can be walked. Two of its items
below have ops steps that must happen *before* the walk, not during it: run
`manage.py backfill_crush_recipient_coaches` after deploy, and set the four
Function App / Django settings under "Infrastructure".

## Why this file exists

Everything below is covered by unit tests, and those tests pass. They are
still not proof. Each item here is a place where the test asserts against a
**substitute** for the real thing — an injected notifier instead of Web Push,
a mocked clock instead of elapsed time, a hand-mutated row instead of two
concurrent requests. The logic is verified; the wiring is not.

The three review rounds on #683 landed in exactly this gap twice: a failure
test whose fake notifier *raised* passed while the real helper *returned* its
errors, and a race test that closed the row before the request so it never
reached the code it was written for. A green suite did not catch either.

So: none of these are known bugs. They are the claims a test cannot make.

## Infrastructure — the reminder sweep

The sweep (`crush_lu/services/crush_leads.py`) is driven by the
`CrushLeadReminders` timer on the `crush-hybrid-maintenance` Function App,
hourly at :45, via `POST /api/admin/crush-lead-reminders/`.

- [ ] **`HYBRID_MAINTENANCE_ENABLED="true"` on the Function App.** Checked
      *before* everything else in `_call_admin_endpoint()`
      (`azure-functions/hybrid-maintenance/function_app.py:64-68`) — if it is
      absent or not the literal string `true`, the function returns without
      reading the URL or calling Django at all. A freshly created or
      deliberately quiesced staging Function App fails here with every other
      setting on this page correct.
- [ ] **`DJANGO_CRUSH_LEAD_REMINDERS_URL` is actually set** on the Function
      App. Unset logs an error and no-ops, so the 24h reminder would silently
      never fire — and nothing else in the system would notice.
- [ ] **`ADMIN_API_KEY` set on the Function App and matching Django's.**
      *Missing* and *wrong* fail differently, and only one is quiet. Missing
      hits the same silent early return as the two above. **Mismatched does
      not**: Django answers 401, `raise_for_status()` raises, and
      `_call_admin_endpoint()` logs the error and re-raises
      (`function_app.py:86,101-103`), so Azure marks the invocation **failed**.
      If the key is wrong you have a failed Function invocation to look at —
      check there first rather than assuming another silent skip.

      Listed in the order `_call_admin_endpoint()` checks them — each is an
      early return, so with several unset only the first is logged.
      These three plus the flag below are **all** required. Only the flag is
      observable from the Django side; the first three fail inside the Function
      App, where the app cannot tell a misconfigured timer from one that never
      ran. Check the Function's own logs, not just Django's.
- [ ] **`CRUSH_LEAD_REMINDERS_ENABLED=True` on the Django app.** The feature
      gate defaults to off, so the endpoint answers
      `200 {"skipped": true}` and no reminder is sent until it is set. Unlike
      the URL var above this one *is* visible — the response says which flag
      stopped it — but both have to be right before a single reminder goes out.
      Note it is independent of `HYBRID_COACH_SYSTEM_ENABLED`: the other
      maintenance timers being live does not turn this on.
- [ ] **`CRUSH_LEAD_REMINDERS_ENABLED` is pinned slot-sticky.** It is listed in
      `infra/resources.bicep` next to `HYBRID_COACH_SYSTEM_ENABLED`, but that
      file carries a standing warning not to deploy it as-is (its
      `appSettingNames` list is out of sync with the live resource), so pin it
      with the CLI instead:
      ```
      az webapp config appsettings set -g <rg> -n <app> --slot staging \
        --settings CRUSH_LEAD_REMINDERS_ENABLED=True \
        --slot-settings CRUSH_LEAD_REMINDERS_ENABLED
      ```
      Unpinned, the staging→production swap exchanges it — turning reminders on
      in production before sign-off, or off again after release.
- [ ] A real reminder push **arrives on a real device**, and its body does not
      contain the `requester_note` (it is deliberately kept out of the payload
      — push surfaces on a lock screen).
- [ ] A coach with **two devices, one with `notify_screening_reminders` off**,
      receives the reminder only on the opted-in device. Unit-tested against a
      captured send list; never against real subscriptions.
- [ ] **VAPID misconfiguration is distinguishable from opt-out.** Break the
      VAPID keys deliberately, run the sweep, confirm the claim is released
      (`reminder_sent_at` back to null) and the lead is still eligible on the
      next run. Both paths return a
      zero-total result; only a flag separates them.
- [ ] **Delivery failure releases the claim** with the real push helper, not an
      injected one. This is the exact shape that fooled the first fix. Since
      O14 the sweep no longer *rolls back* — it commits the stamp as a claim,
      sends outside the transaction, and clears the stamp with a second guarded
      `UPDATE` on failure. So check both halves on one broken endpoint: the
      lead is eligible again on the next sweep, **and** the subscription's
      `failure_count` went up and stayed up. Before O14 the second half was the
      bug: the rollback discarded the health write with the stamp, so a dead
      endpoint never reached its five-failure auto-delete.
- [ ] **A push that *raises* also releases the claim.** Distinct from the
      above: the helper returns its errors, but a transport-level failure can
      still propagate. Committing the claim first means an exception no longer
      undoes it, so this path depends entirely on an explicit handler. Unit
      tests cover it against a raising fake; confirm once against a real
      unreachable endpoint.
- [ ] **The timer's 60s budget holds** under a realistic backlog. The
      wall-clock budget (`REMINDER_TIME_BUDGET`, 45s) is tested with a fake
      clock; it has never met a slow endpoint. Check the Function does not
      time out and that `truncated` shows up in logs rather than a silent
      partial sweep.
- [ ] **Two overlapping timer deliveries send exactly one reminder** per lead.
      `skip_locked` is the mechanism; SQLite in tests does not exercise it, and
      staging is Postgres.

## Concurrency — verified only against simulated races

Every race below is unit-tested by hand-constructing the interleaving (a
stale instance held across a committed write, or a `.update()` between scan
and lock). None has been observed with two real requests.

- [ ] Two coaches acting on the same lead at once leave a **consistent
      `system_actions` trail** — no entry overwritten. Appending to a JSON
      field is a read-modify-write; the locks are correct in principle.
- [ ] The co-coach recording an answer while the routed coach **declines**
      resolves one way only, with a message matching what actually happened
      ("this lead is closed", not "already recorded").
- [ ] `record_outreach` on a lead closing underneath it neither stamps the
      field nor reports success.
- [ ] **A coach acting during a slow push does not get reminded about the work
      they just did.** O14 releases the row lock before the send, so the coach
      action handlers — which lock the same row — no longer queue behind the
      reminder transaction. An unlocked re-read just before the send narrows
      that window but cannot close it; closing it means holding the lock across
      the network call, which is the bug O14 removed. Schedule a call against a
      lead whose push is deliberately slow (a hung endpoint) and confirm the
      reminder is cancelled and the claim handed back. **This is the one race
      here that is expected to be reachable in production**, not merely
      theoretical — it is bounded by push duration, and the loser is a coach
      seeing one stale notification, not corrupted data.
- [ ] Postgres row locking behaves as assumed throughout — the suite runs on
      SQLite, so `select_for_update` is effectively a no-op there.

## The flow end to end

- [ ] **Different coaches on each half:** declare → routed coach calls the
      crusher → co-coach reaches the recipient → consent → introduction. The
      privacy line holds throughout: the co-coach never sees the crusher's
      identity or note.
- [ ] **Same coach on both halves**, and **recipient's coach deactivated
      mid-flow** — both now route through `crush_record_recipient_consent` on
      the routed coach's page. This was a hard deadlock until #685; walk it
      end to end rather than trusting the unit test.
- [ ] A **declined** recipient ends the lead and the crusher is never told
      they were refused — check every surface, including notifications.
- [ ] An **unrouted pool lead** can be opened and claimed but not worked, and
      its note stays shut until a coach owns it. It now also appears in the
      **coach inbox** under an amber "Unclaimed" badge, with the lead's own
      "call by" clock — check it shows for *every* active coach, that claiming
      it removes it from the others' inboxes, and that it does not sort above
      a coach's own call at the same urgency. It gets **no push notification**:
      that half of O12/3 was deliberately not built (§10.1), so a coach who
      never opens their inbox still learns nothing about it.
- [ ] A **mutual crush** flags both coaches without either learning the other
      side's note.

## UI (Phase E scope, listed here so it is not lost)

- [ ] Coach surfaces in **DE and FR**. The code half is done: the labels are
      wrapped and `makemessages` has run, so the Phase D/E strings are in both
      catalogues — **with empty `msgstr`s**. Until a native speaker fills them
      in, every coach surface still renders English in DE and FR, and that is
      what to check for here. Two things the extraction run itself does not
      settle:
      - `RECIPIENT_RESPONSE_CHOICES` is now wrapped, but nothing calls
        `get_recipient_response_display()` yet — so its two labels are in the
        catalogue ahead of any surface that shows them. Not a gap; just do not
        go looking for them on screen.
      - `STATUS_CHOICES` and `FLOW_CHOICES` next to it are **still unwrapped**
        and were left that way deliberately — they predate this phase and are
        admin-only. If a coach-facing surface ever renders
        `get_status_display()`, it will be English in both languages and no
        catalogue will fix it.
      - **390 entries are marked `#, fuzzy`, and that is deliberate.** Each
        carries a candidate translation `msgmerge` recovered from a msgid that
        was reworded at some point since the last extraction (2026-06-13),
        with a `#| msgid "…"` breadcrumb showing the old text. A fuzzy entry
        is excluded from the compiled `.mo`, so **none of them renders** — the
        string shows in English until a translator confirms it. Confirming one
        is a keystroke; retyping it is not.

        An earlier version of this branch blanked all 390
        (`msgattrib --clear-fuzzy --empty`) to avoid a translator accepting a
        bad guess — one match really is nonsense ("Recipient consented to the
        introduction" ← "Write the introduction"). That was the wrong trade:
        it destroyed ~261 genuinely recoverable translations per language to
        suppress a much smaller number of obvious mismatches, and the
        breadcrumb makes the bad ones self-evident anyway. **Do not clear them
        wholesale on a future run.**
      - **Some strings were already rendering in English on `main` before any
        of this.** The catalogues had gone stale: `"My Crush"` was translated
        while every template says `"My Crush!"`, so the lookup already missed.
        The extraction did not break those — it made the catalogue honest
        about them. Expect the DE/FR pass to be larger than "the new Phase D/E
        strings" for that reason.
- [ ] **Dark mode and light mode** on the outreach task and the lead
      workspace. A white-on-white button already shipped once in this phase
      and was only caught by review.
- [ ] **Mobile widths** for the workspace, which is the densest coach surface
      in the app.

## Deliberately not built

Recorded so a future reader does not mistake these for oversights:

~~- **A constrained triage surface for unrouted leads.** Routing tier 4 catches
  any active coach, so a lead is unrouted only when the platform has zero
  active coaches.~~ **This was wrong — it belonged in the build, and has since
  been built** as the coach inbox's `crush_pool` section (§10.1). The reasoning
  it was wrong is kept below, because the shape of the mistake is worth more
  than the conclusion.
  The rationale missed that `profile_requirement="none"` events let
  `event_register` proceed with `profile = None` (`views_events.py:916-920`),
  and `request_connection` gates the *requester* on attendance only. A
  profile-less attendee of an open event therefore declares normally,
  `declare_crush()` skips routing for them, and the lead is unrouted **with
  active coaches present**. It stays visible and claimable on the connections
  page's **Pending** tab, but it is absent from the SLA-tracked inbox
  (`crush_leads_for_coach()` filters `assigned_coach = me`) and from the
  reminder sweep (`reminder_candidates()` filters
  `assigned_coach__isnull=False`) — so nothing gives it a "call by" clock or
  chases it, and the default `needs_review` tab does not show it. Tracked as
  row 5 / option 3 of O12 in the spec, scoped there as wiring an existing pool
  row into the inbox and sweep rather than building discovery from scratch.
  Kept visible rather than deleted because the flawed
  reachability argument was accepted twice in review before anyone read
  `profile_requirement`'s full choice list.
~~- **Reverting a `shared` lead via the legacy `approve` action.** The terminal
  guard covers `declined` only.~~ **No longer true — fixed before merge.** The
  guard now reads `status in ("declined", "shared")`, both before taking the
  lock and again on the locked re-read, so a stale workspace cannot walk a
  completed introduction back to `coach_approved` while `shared_at` stays
  populated. Struck rather than deleted so anyone who read the earlier version
  can see it was closed, not dropped.
