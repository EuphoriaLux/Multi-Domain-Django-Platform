# echo.lu event sync

Publishes public Crush.lu events to [echo.lu](https://www.echo.lu), Luxembourg's
national events portal, through its partner API.

- API reference: <https://api.echo.lu/> (sandbox: <https://test-api.echo.lu/>)
- Support: the echo.lu organiser back office issues the API key and answers
  vocabulary questions.

## What gets published

Only events that are **published, public, and not yet finished**:

| Condition | Sent to echo.lu |
| --- | --- |
| `is_published=False` | no |
| `is_cancelled=True` | no — cancelled on echo.lu if it was listed |
| `is_private_invitation=True` | **never** |
| `end_time` in the past | no — unpublished on echo.lu if it was listed |
| otherwise | yes |

The private-invitation exclusion is the one that matters most: those events are
invitation-only by design, and echo.lu is a public, indexed, national listing.

## Turning it on

1. **Issue an API key** in the echo.lu organiser back office. The key is tied to
   your organisation — there is no separate organisation id to configure.
2. **Set the environment variables** (Azure App Service application settings):

   ```
   ECHO_LU_API_KEY=<key>
   ECHO_LU_API_BASE_URL=https://api.echo.lu/v1     # sandbox: https://test-api.echo.lu/v1
   ECHO_LU_SYNC_ENABLED=true
   ECHO_LU_CONTACT_EMAIL=hello@crush.lu
   ECHO_LU_CONTACT_PHONE=+352...
   ```

   Two more have sensible defaults and only need setting if echo.lu turns out
   to be slower than expected: `ECHO_LU_SIGNAL_TIMEOUT_SECONDS` (5) caps what
   an event save spends on the sync, and `ECHO_LU_SWEEP_BUDGET_SECONDS` (90)
   caps one hourly pass. See *How it stays in sync* for why both exist.

   `ECHO_LU_SYNC_ENABLED` defaults to **false** everywhere. Nothing is written
   to echo.lu until it is explicitly turned on, so a restored production
   database on staging cannot mutate live listings just by inheriting the key.

3. **Pick the taxonomy slugs.** echo.lu validates categories, audiences, formats
   and environments against its own vocabularies and rejects the *entire*
   experience on one unknown value, so these ship empty and have to be read off
   the API:

   ```bash
   python manage.py echo_taxonomy                  # print every vocabulary
   python manage.py echo_taxonomy --kind categories
   ```

   Then set what applies, comma-separated:

   ```
   ECHO_LU_DEFAULT_CATEGORIES=...
   ECHO_LU_DEFAULT_AUDIENCES=...
   ECHO_LU_CATEGORY_MAP={"speed_dating": ["..."], "quiz_night": ["..."]}
   ```

   `ECHO_LU_CATEGORY_MAP` layers per-event-type categories on top of the
   defaults. Verify what you configured actually exists:

   ```bash
   python manage.py echo_taxonomy --check
   ```

4. **Dry-run before the first real sync**, which needs no key and writes nothing:

   ```bash
   python manage.py sync_events_to_echo --dry-run
   python manage.py sync_events_to_echo --event-id 42 --dry-run --show-payload
   ```

5. **Sync one event first**, look at it on echo.lu, then let the sweep take over:

   ```bash
   python manage.py sync_events_to_echo --event-id 42
   ```

## How it stays in sync

Three paths, each covering what the others cannot:

- **On save** — a `post_save` receiver on `MeetupEvent` enqueues a background
  task (`sync_event_to_echo_task`). No change detection: the service hashes the
  payload it would send and compares it with the one echo.lu last accepted, so
  an unrelated save costs a hash and no HTTP request.
- **Admin bulk actions** — publish/unpublish/cancel use `queryset.update()`,
  which emits no signals, so they enqueue the sync explicitly.
- **Hourly sweep** — the `EchoLuSync` Azure Function timer POSTs
  `/api/admin/echo-sync/`, which runs `sync_events_to_echo`. This is the only
  thing that catches an event simply *ending* (nothing saves the row when it
  does), and it is the retry path for writes that failed during an echo.lu
  outage.

Everything is idempotent. In the steady state the sweep makes zero API calls.

**"Background" is optimistic on the save path.** `DJANGO_TASKS_BACKEND` is
unset in production, so `TASKS` falls back to `ImmediateBackend` and `.enqueue()`
runs the sync inline in the request that saved the event. That path therefore
uses a deliberately impatient client — `ECHO_LU_SIGNAL_TIMEOUT_SECONDS`
(default 5) with retries off — so an unreachable echo.lu costs an admin save a
few seconds rather than a minute. Anything it drops is picked up by the sweep.

**The sweep bounds itself.** The Function allows 110s, so a pass stops at
`ECHO_LU_SWEEP_BUDGET_SECONDS` (default 90) and reports what it left. The next
hour resumes with the remainder — the selection is by state, not by cursor, so
nothing is skipped. Override per-run with `--max-seconds`.

The budget reserves one call's worth of headroom rather than just checking
whether it has run out: an event started a second before the deadline still
gets a full timeout to finish, and that overshoot is exactly what the budget
exists to prevent. The sweep client also runs with retries off — the sweep
*is* the retry, an hour later — which keeps a single event's worst case to one
timeout instead of four attempts plus backoff.

**Admin actions are bounded too**, by `ECHO_LU_ADMIN_BUDGET_SECONDS` (30).
Both the bulk publish/unpublish/cancel actions and the manual sync action run
inline in the request for the same `ImmediateBackend` reason, and the admin
page size is not a bound — a slow echo.lu across two dozen selected events
would otherwise lose the response to gunicorn's timeout for work the database
had already committed. Anything not reached is named in a message and left to
the sweep.

**Failures reach the timer.** If any event fails, the command exits non-zero
and the endpoint answers 500, so the Function's failure count moves. A revoked
key or a long outage shows up there instead of staying green on a job that
quietly synced nothing.

To check the two sides agree — including listings echo.lu holds that we have no
id for, which are invisible from our side by definition:

```bash
python manage.py sync_events_to_echo --audit
```

## Take-downs

`withdraw` rather than `delete`, deliberately:

- A **cancelled** event is *cancelled* on echo.lu — the portal shows a
  cancellation notice, which is what someone who already saw the listing needs.
- Everything else (unpublished, gone private, finished) is *unpublished*: it
  leaves the public site but the experience stays addressable, so re-publishing
  updates the same listing instead of creating a second one.

Cancelling only applies while the event is *otherwise still public*. A
cancellation notice is a published thing — it keeps the title, venue and date
on a national portal — so an event that was cancelled **and** unpublished or
made invitation-only is unpublished instead. Privacy is the stronger
instruction; nobody is told why.

That holds whichever order the two changes arrive in. A cancelled listing is
recorded as **Cancelled**, not Withdrawn, precisely because it is still
showing: if the event is unpublished or made invitation-only in a *later*
save, the sweep sees a listing that is still public and takes it down. Marking
it Withdrawn would have claimed the work was already done and left the notice
up indefinitely.

The experience id is kept in both cases. `delete_event_listing()` exists for
genuine mistakes — an event that should never have been published at all.

**Un-cancelling is not automatic.** The API has `cancel` but no matching
"un-cancel" action, so restoring an event that was already cancelled on echo.lu
sends a `PUT` that updates the content while the listing may stay marked
cancelled on their side. Check the listing in the organiser back office after
restoring a cancelled event, and if it is still showing as cancelled, withdraw
and recreate it:

```bash
python manage.py sync_events_to_echo --event-id 42        # after clearing is_cancelled
# if echo.lu still shows it cancelled, from a Django shell:
#   from crush_lu.services.echo_lu import delete_event_listing, sync_event
#   delete_event_listing(event); sync_event(event)
```

Manual take-down without unpublishing on crush.lu:

```bash
python manage.py sync_events_to_echo --event-id 42 --withdraw
python manage.py sync_events_to_echo --withdraw --all-listed   # everything
```

Or the **🚫 Remove selected events from echo.lu** admin action.

Both of those are *explicit* take-downs: they can hit an event that still
qualifies for a listing, so the sync row is parked in **Suppressed** rather
than Withdrawn. The difference is what the hourly sweep does next — a
Withdrawn event re-publishes by itself once it qualifies again, a Suppressed
one stays down, because the event's own fields still say "publish me" and
nothing else would stop the next pass putting it straight back. To undo one,
use the **🌍 Sync selected events to echo.lu** admin action or
`--event-id N --force`.

An explicit take-down always *unpublishes*, even for a cancelled event — the
cancellation notice is skipped here on purpose. Somebody asking to remove a
listing means remove it, and leaving a public notice up would then be frozen
in place by the Suppressed state, with no sweep to correct it.

Suppression also survives the event later becoming ineligible. Withdrawing
again would rewrite the row to Withdrawn, and a Withdrawn event re-publishes
itself once it qualifies — so an unrelated unpublish/republish cycle would
quietly undo a removal somebody asked for.

**Deleting a MeetupEvent takes its listing down.** The sync record cascades
with the event, and the experience id is the only handle we have — once the
row is gone the listing is still public and nothing left in the database can
name it. A `pre_delete` receiver captures the id while it is still readable,
and the take-down runs on commit.

*On commit*, for two reasons. A delete that rolls back withdraws nothing —
doing the call at `pre_delete` meant an aborted delete still pulled the
listing, leaving an event that exists and a listing that does not. And no HTTP
happens inside the delete's transaction, which Django's collector wraps around
the whole cascade.

A bulk delete arrives as N take-downs back to back, so they share
`ECHO_LU_ADMIN_BUDGET_SECONDS` rather than multiplying one timeout by N.

The take-down is best-effort by necessity: the delete proceeds either way,
because refusing it would be the worse failure (a coach unable to remove an
event they need gone). If echo.lu is unreachable, the budget runs out, or the
sync switch is off, the experience id is written to the log at ERROR/WARNING —
the event is gone, so nothing can retry from the database and that log line is
the last copy. Recover from it, or find the listing with `--audit`, which
still reports it as untracked.

### Orphaned listings

A create can leave a listing we hold no handle on, in two ways:

- echo.lu answers 2xx but returns no id;
- the create fails in a way that does not say whether it committed first — a
  5xx, a timeout, a dropped socket. A rejection (4xx) is different: echo.lu
  read the payload and refused it, so nothing exists and the sync retries
  normally.

Either way the row goes to **Orphaned**, which blocks every automatic create
for that event — including `--force`, because a second create is the one thing
this state exists to prevent. It is the only status that needs a human, and
the hourly sweep reports it as `blocked` and exits non-zero until it is
resolved, so it will not sit unnoticed.

Find out which happened:

```bash
python manage.py sync_events_to_echo --audit
```

Then resolve it. Neither of these writes to echo.lu, so both work with the
sync switch off:

```bash
# The listing is there — reattach its id and resume syncing against it.
python manage.py sync_events_to_echo --event-id 42 --adopt exp_abc123

# The listing was deleted in the back office — clear the block so the next
# sync creates a fresh one.
python manage.py sync_events_to_echo --event-id 42 --forget
```

Adopting sets the row to **Pending** with no payload fingerprint, so the next
sync sends a full update rather than trusting a hash against a listing whose
content nobody has confirmed.

## Field mapping

| echo.lu | Crush.lu |
| --- | --- |
| `title` / `description` | `title_en` / `description_en`, falling back through the other languages |
| `subtitle` | `get_event_type_display()` |
| `dates[0].from` / `.to` | `date_time` / `end_time`, RFC 3339 in UTC |
| `dates[0].purchaseLink` | the event detail page |
| `venues` | `location` (venue name) |
| `location.address` | `address` parsed into street/number/postcode/town, plus `canton` |
| `location.address.latitude/longitude` | `latitude` / `longitude`, as strings, omitted when unset |
| `pictures[0]` | `image`, made absolute |
| `tickets` | `registration_fee` in EUR; free events get an explicit €0 ticket |
| `contact` | the `ECHO_LU_CONTACT_*` settings; `website` is the event page |
| `languages` | `languages` |
| `categories` / `audiences` / `formats` / `environments` / `tags` | the `ECHO_LU_DEFAULT_*` settings |

Two deliberate omissions:

- **`dates[].duration`** — the unit is undocumented and `from`/`to` already pin
  the span exactly, so a duration in the wrong unit would contradict them on the
  public listing.
- **Blank contact fields** — echo.lu treats an empty string as a supplied value
  and renders a blank contact line for it.

Addresses are parsed best-effort and never guessed: a component that cannot be
identified with confidence is left blank, and the full first line always
survives in `street`, so the listing is never less informative than what we
hold.

## Troubleshooting

Sync state per event lives on `EchoExperienceSync` and is shown in the admin —
a status column on the event list, and the full detail (experience id, last
sync, last error) on the event's change form.

| Symptom | Cause |
| --- | --- |
| Every sync fails with a 4xx | Almost always an unknown taxonomy slug. Run `echo_taxonomy --check`. |
| `sync_events_to_echo` errors "sync is disabled" | `ECHO_LU_SYNC_ENABLED` or `ECHO_LU_API_KEY` is unset. Deliberate: a silent no-op on a scheduled job is indistinguishable from "nothing to do". |
| Bare 401 | The key is wrong for the environment — production and sandbox keys are not interchangeable. |
| "accepted the experience but returned no id" | echo.lu created a listing we cannot address. Run `sync_events_to_echo --audit` to find it, then delete it in the back office before re-running — otherwise the next sync creates a duplicate. |
| Event never appears, no error | Check eligibility — private, unpublished, cancelled and finished events are all skipped by design. |

Nothing here raises into a user-facing path: the background task logs API
errors and moves on, so an echo.lu outage cannot make an event edit fail. The
failure is recorded on the sync row and retried by the next hourly sweep.

## Code

| File | Role |
| --- | --- |
| `crush_lu/services/echo_lu.py` | API client, payload mapping, eligibility, orchestration |
| `crush_lu/models/echo_lu.py` | `EchoExperienceSync` — experience id and sync health |
| `crush_lu/management/commands/sync_events_to_echo.py` | the sweep / manual sync |
| `crush_lu/management/commands/echo_taxonomy.py` | vocabulary discovery and slug check |
| `crush_lu/api_admin_events.py` | `POST /api/admin/echo-sync/` |
| `azure-functions/hybrid-maintenance/function_app.py` | `EchoLuSync` hourly timer |
| `crush_lu/tests/test_echo_lu_sync.py` | tests |
