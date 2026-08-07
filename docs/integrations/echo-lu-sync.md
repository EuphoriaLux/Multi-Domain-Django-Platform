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

**The sweep bounds itself.** The Function allows 110s and one event can spend
most of a minute against a struggling echo.lu, so a pass stops at
`ECHO_LU_SWEEP_BUDGET_SECONDS` (default 90) and reports what it left. The next
hour resumes with the remainder — the selection is by state, not by cursor, so
nothing is skipped. Override per-run with `--max-seconds`.

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

**Deleting a MeetupEvent row deletes its sync record too** (cascade), which
loses the experience id and orphans the listing on echo.lu. Withdraw first,
then delete.

### Orphaned listings

If echo.lu answers a create with 2xx but no id, a listing now exists that we
hold no handle on. The row goes to **Orphaned**, which blocks every automatic
create for that event — including `--force`, because a second create is the
one thing this state exists to prevent. It is the only status that needs a
human:

```bash
python manage.py sync_events_to_echo --audit    # find the listing
```

Then either adopt it — set `experience_id` on the event's `EchoExperienceSync`
row in the admin and change the status back to Pending — or delete it in the
organiser back office and clear the row.

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
