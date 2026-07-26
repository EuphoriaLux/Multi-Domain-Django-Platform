# "My Crush!" — staging verification checklist

Spec: `docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md`

**Status: open. Do this once Phase E lands and the whole flow is deployed to
staging — not before.** Phases B–D each shipped one slice of the flow, so
until Phase E there is no end-to-end path to walk.

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
- [ ] **`ADMIN_API_KEY` set on the Function App and matching Django's.** Also
      checked inside the function; a mismatch surfaces as a 401 the timer
      swallows.
- [ ] **`DJANGO_CRUSH_LEAD_REMINDERS_URL` is actually set** on the Function
      App. Unset logs an error and no-ops, so the 24h reminder would silently
      never fire — and nothing else in the system would notice.

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
      `az webapp config appsettings set -g <rg> -n <app> --slot staging --settings CRUSH_LEAD_REMINDERS_ENABLED=True --slot-settings CRUSH_LEAD_REMINDERS_ENABLED`
      Unpinned, the staging→production swap exchanges it — turning reminders on
      in production before sign-off, or off again after release.
- [ ] A real reminder push **arrives on a real device**, and its body does not
      contain the `requester_note` (it is deliberately kept out of the payload
      — push surfaces on a lock screen).
- [ ] A coach with **two devices, one with `notify_screening_reminders` off**,
      receives the reminder only on the opted-in device. Unit-tested against a
      captured send list; never against real subscriptions.
- [ ] **VAPID misconfiguration is distinguishable from opt-out.** Break the
      VAPID keys deliberately, run the sweep, confirm `reminder_sent_at` rolls
      back and the lead is still eligible on the next run. Both paths return a
      zero-total result; only a flag separates them.
- [ ] **Delivery failure rolls the stamp back** with the real push helper, not
      an injected one. This is the exact shape that fooled the first fix.
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
- [ ] Postgres row locking behaves as assumed throughout — the suite runs on
      SQLite, so `select_for_update` is effectively a no-op there.

## The flow end to end (needs Phase E)

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
      its note stays shut until a coach owns it.
- [ ] A **mutual crush** flags both coaches without either learning the other
      side's note.

## UI (Phase E scope, listed here so it is not lost)

- [ ] Coach surfaces in **DE and FR** — all Phase D strings are wrapped but
      the catalogues have not been extracted.
- [ ] **Dark mode and light mode** on the outreach task and the lead
      workspace. A white-on-white button already shipped once in this phase
      and was only caught by review.
- [ ] **Mobile widths** for the workspace, which is the densest coach surface
      in the app.

## Deliberately not built

Recorded so a future reader does not mistake these for oversights:

~~- **A constrained triage surface for unrouted leads.** Routing tier 4 catches
  any active coach, so a lead is unrouted only when the platform has zero
  active coaches.~~ **This was wrong — it belongs in the build, not here.**
  The rationale missed that `profile_requirement="none"` events let
  `event_register` proceed with `profile = None` (`views_events.py:916-920`),
  and `request_connection` gates the *requester* on attendance only. A
  profile-less attendee of an open event therefore declares normally,
  `declare_crush()` skips routing for them, and the lead is unrouted **with
  active coaches present** — landing in no inbox, no reminder sweep, and behind
  a connections page that defaults to `needs_review`. Tracked as row 5 / option
  3 of O12 in the spec. Kept visible rather than deleted because the flawed
  reachability argument was accepted twice in review before anyone read
  `profile_requirement`'s full choice list.
~~- **Reverting a `shared` lead via the legacy `approve` action.** The terminal
  guard covers `declined` only.~~ **No longer true — fixed before merge.** The
  guard now reads `status in ("declined", "shared")`, both before taking the
  lock and again on the locked re-read, so a stale workspace cannot walk a
  completed introduction back to `coach_approved` while `shared_at` stays
  populated. Struck rather than deleted so anyone who read the earlier version
  can see it was closed, not dropped.
