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

- [ ] **`DJANGO_CRUSH_LEAD_REMINDERS_URL` is actually set** on the Function
      App. Unset logs an error and no-ops, so the 24h reminder would silently
      never fire — and nothing else in the system would notice. This is the
      single highest-value check on the page.
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

- **A constrained triage surface for unrouted leads.** Routing tier 4 catches
  any active coach, so a lead is unrouted only when the platform has zero
  active coaches. If staging ever produces an unrouted lead with active
  coaches present, that reachability argument is wrong — reopen it.
- **Reverting a `shared` lead via the legacy `approve` action.** The terminal
  guard covers `declined` only. No review round raised it; noted in case
  staging shows it matters.
