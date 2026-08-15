# echo.lu event sync

Publishes public Crush.lu events to [echo.lu](https://www.echo.lu), Luxembourg's
national events portal, through its partner API.

- API reference: <https://api.echo.lu/> — Echo API v1.1.0. There is no sandbox.
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

### The venue is published, and that is a decision, not an oversight

`event_detail.html` shows `location` and `address` **only to signed-in users** —
anonymous visitors get the canton and *"Sign up to reveal the exact location"*.
The echo.lu payload sends both fields, so **publishing an event puts its exact
venue and street address on a public, indexed, national portal**, visible to
people who have not signed up and to search engines.

Decided 2026-08-08 (Tom): **that is fine.** The reveal-on-signup gate is a
signup nudge rather than a confidentiality requirement, and national discovery
is worth more than the nudge.

Written down because it is invisible from the code — nothing enforces it either
way; it is simply a property of sending `location`/`address` at all — and
because it is easy to reverse later. If the trade stops being worth it, three
options, cheapest first:

1. **A placeholder venue.** Register one "Crush.lu — Luxembourg City" venue and
   point every experience at it, with a canton-level address. `venues` is
   required so it cannot just be dropped; this satisfies it, keeps the gate,
   and `purchaseLink` still sends readers to the event page for the real
   detail. Costs one generic row in a registry shared with other organisers.
2. **A per-event opt-in.** A `publish_venue_publicly` flag on `MeetupEvent`,
   default off. Most control; needs a migration and an admin field.
3. **Skip venue-gated events entirely.** Simplest — and since today that is
   every event, it amounts to leaving the integration off.

## Turning it on

> **There is no sandbox.** An earlier version of this page sent you to
> `test-api.echo.lu` with a separate key. That environment is not real: the
> published docs name exactly one base URL, never mention a test environment,
> and that hostname serves a byte-identical documentation page from the same
> address as `api.echo.lu`. **Every write lands on the live national portal.**
>
> That inverts the obvious rollout. Staging is the *dangerous* place to try
> this — its database is `pythonapp_staging`, full of test events, and
> publishing those to echo.lu is exactly the failure this integration is meant
> to avoid. The safe first run is **one real production event, by id**. Its
> blast radius is that one listing, and `DeleteExperience` is the undo.

**Order matters and it is not the obvious order.** The switch goes on late, and
the hourly timer later still — the sweep reconciles the whole upcoming calendar
on its first tick.

1. **Issue an API key** in the echo.lu organiser back office. The key is tied to
   your organisation — there is no separate organisation id to configure.
2. **Set the credentials on the production slot, switch still off:**

   ```
   ECHO_LU_API_KEY=<key>
   ECHO_LU_CONTACT_EMAIL=<a mailbox somebody actually reads>
   ECHO_LU_CONTACT_PHONE=+352...
   ```

   `ECHO_LU_API_BASE_URL` needs no setting; it defaults to
   `https://api.echo.lu/v1`, the only base URL there is.

   `ECHO_LU_SYNC_ENABLED` defaults to **false**, and the key alone is inert —
   that is what lets the next steps run against the real API while nothing can
   be published. Note that `ECHO_LU_API_KEY` and `ECHO_LU_SYNC_ENABLED` are
   **slot-sticky** (they are in `slotConfigNames`), so each slot keeps its own
   and a swap does not carry them.

   **Contact is all-or-nothing.** `name`, `email` and `phone` are all required
   *within* the contact block while the block itself is optional, so a partial
   one is worse than none — it turns an optional field into a guaranteed
   rejection. Without a phone number the contact block is omitted entirely and
   the listing carries no organiser contact.

3. **Check the taxonomy ids.** All four facets are **required** — an experience
   without them is rejected with `Missing categories` and so on, so "leave it
   blank" is not an option:

   ```bash
   python manage.py echo_taxonomy            # 190 categories, 10 audiences,
   python manage.py echo_taxonomy --check    # 16 formats, 3 environments
   ```

   The shipped defaults are real ids read off the live API
   (`nightlife` / `adults` / `networking` / `indoors`, languages `en,fr`),
   chosen for a dating meetup rather than editorially decided. Override per
   environment once somebody has read how the listings look:

   ```
   ECHO_LU_DEFAULT_CATEGORIES=nightlife
   ECHO_LU_CATEGORY_MAP={"speed_dating": ["nightlife"], "mixer": ["nightlife-afterwork"]}
   ```

4. **Dry-run and read the payload**, which writes nothing:

   ```bash
   python manage.py sync_events_to_echo --event-id 42 --dry-run --show-payload
   ```

   Read it rather than skim it — echo.lu renders whatever it accepts verbatim.
   `location.address` comes from the event's street/number/postcode/town
   fields; if it looks thin, that event still holds only the legacy free text
   and needs `manage.py backfill_event_addresses`.

5. **Turn it on:** `ECHO_LU_SYNC_ENABLED=true` on the production slot.

6. **Sync exactly one event and go and look at it:**

   ```bash
   python manage.py sync_events_to_echo --event-id 42
   ```

   This is the real test, because there is nowhere else to run one. The
   experience is created with `status=pending`, which submits it for
   validation — **echo.lu moderates listings**, so it will not appear publicly
   the moment the command returns. Check the organiser back office. Set
   `ECHO_LU_CREATE_STATUS=draft` first if you would rather it park there
   without being submitted at all.

7. **Only then wire up the hourly sweep**, by setting `DJANGO_ECHO_SYNC_URL` on
   the `crush-hybrid-maintenance` Function App — re-running `provision.sh` /
   `provision.ps1` does it, or set it directly:

   ```bash
   az functionapp config appsettings set -g django-app-rg      -n crush-hybrid-maintenance      --settings DJANGO_ECHO_SYNC_URL=https://crush.lu/api/admin/echo-sync/
   ```

   Last, for two reasons. The sweep's first tick reconciles the *entire*
   upcoming calendar, so everything above needs to have been checked by then.
   And the endpoint answers 500 while the switch is off — deliberately, so a
   dormant sweep cannot look green — which the `EchoLuSync` timer records as a
   Failed invocation every hour until step 5 is done.

### Venues are linked by hand, never created

`venues` on an experience is a list of **ids** from echo.lu's own venue
registry — a shared national table of 5,000+ rows that every organiser reads
and writes. Two facts about it shape everything else:

* **There is no text search.** ListVenues filters only by category and commune,
  and a page of 100 costs 3-4 seconds. Finding one venue means paging the whole
  registry: minutes. That is fine once, in a shell; it is impossible inside an
  admin action that allows five seconds.
* **Registering a venue means claiming it.** CreateVenue requires a
  description, a category, a website and a contact. For premises we merely
  book, the only values we hold are Crush.lu's own — so an automatic
  registration would publish somebody else's venue into a public national
  registry with our contact details on it, for other organisers to attach their
  events to.

So the sync **never registers a venue and never searches at request time.** It
reads a mapping made once, by a person:

```bash
# find the id (slow, exhaustive, read-only)
python manage.py echo_venue --search "MAIN Experience"

# record it — the key is name + postcode, exactly as the event produces them
python manage.py echo_venue --link "MAIN Experience" --postcode 1450     --venue-id <id>

python manage.py echo_venue --list
```

If the venue is not in the registry, create it in the echo.lu organiser back
office first — with its own description, category and contact, not ours — then
link the id it is given.

Until a venue is linked, syncing an event there fails with a message naming it
and the command to run. That failure is retryable (`FAILED`, not `ORPHANED`):
nothing was sent, so the next sweep succeeds by itself once the link exists.

## Turning it on

> **There is no sandbox.** An earlier version of this page sent you to
> `test-api.echo.lu` with a separate key. That environment is not real: the
> published docs name exactly one base URL, never mention a test environment,
> and that hostname serves a byte-identical documentation page from the same
> address as `api.echo.lu`. **Every write lands on the live national portal.**
>
> That inverts the obvious rollout. Staging is the *dangerous* place to try
> this — its database is `pythonapp_staging`, full of test events, and
> publishing those to echo.lu is exactly the failure this integration is meant
> to avoid. The safe first run is **one real production event, by id**. Its
> blast radius is that one listing, and `DeleteExperience` is the undo.

**Order matters and it is not the obvious order.** The switch goes on late, and
the hourly timer later still — the sweep reconciles the whole upcoming calendar
on its first tick.

1. **Issue an API key** in the echo.lu organiser back office. The key is tied to
   your organisation — there is no separate organisation id to configure.
2. **Set the credentials on the production slot, switch still off:**

   ```
   ECHO_LU_API_KEY=<key>
   ECHO_LU_CONTACT_EMAIL=<a mailbox somebody actually reads>
   ECHO_LU_CONTACT_PHONE=+352...
   ```

   `ECHO_LU_API_BASE_URL` needs no setting; it defaults to
   `https://api.echo.lu/v1`, the only base URL there is.

   `ECHO_LU_SYNC_ENABLED` defaults to **false**, and the key alone is inert —
   that is what lets the next steps run against the real API while nothing can
   be published. Note that `ECHO_LU_API_KEY` and `ECHO_LU_SYNC_ENABLED` are
   **slot-sticky** (they are in `slotConfigNames`), so each slot keeps its own
   and a swap does not carry them.

   **Contact is all-or-nothing.** `name`, `email` and `phone` are all required
   *within* the contact block while the block itself is optional, so a partial
   one is worse than none — it turns an optional field into a guaranteed
   rejection. Without a phone number the contact block is omitted entirely and
   the listing carries no organiser contact.

3. **Check the taxonomy ids.** All four facets are **required** — an experience
   without them is rejected with `Missing categories` and so on, so "leave it
   blank" is not an option:

   ```bash
   python manage.py echo_taxonomy            # 190 categories, 10 audiences,
   python manage.py echo_taxonomy --check    # 16 formats, 3 environments
   ```

   The shipped defaults are real ids read off the live API
   (`nightlife` / `adults` / `networking` / `indoors`, languages `en,fr`),
   chosen for a dating meetup rather than editorially decided. Override per
   environment once somebody has read how the listings look:

   ```
   ECHO_LU_DEFAULT_CATEGORIES=nightlife
   ECHO_LU_CATEGORY_MAP={"speed_dating": ["nightlife"], "mixer": ["nightlife-afterwork"]}
   ```

4. **Dry-run and read the payload**, which writes nothing:

   ```bash
   python manage.py sync_events_to_echo --event-id 42 --dry-run --show-payload
   ```

   Read it rather than skim it — echo.lu renders whatever it accepts verbatim.
   `location.address` comes from the event's street/number/postcode/town
   fields; if it looks thin, that event still holds only the legacy free text
   and needs `manage.py backfill_event_addresses`.

5. **Turn it on:** `ECHO_LU_SYNC_ENABLED=true` on the production slot.

6. **Sync exactly one event and go and look at it:**

   ```bash
   python manage.py sync_events_to_echo --event-id 42
   ```

   This is the real test, because there is nowhere else to run one. The
   experience is created with `status=pending`, which submits it for
   validation — **echo.lu moderates listings**, so it will not appear publicly
   the moment the command returns. Check the organiser back office. Set
   `ECHO_LU_CREATE_STATUS=draft` first if you would rather it park there
   without being submitted at all.

7. **Only then wire up the hourly sweep**, by setting `DJANGO_ECHO_SYNC_URL` on
   the `crush-hybrid-maintenance` Function App — re-running `provision.sh` /
   `provision.ps1` does it, or set it directly:

   ```bash
   az functionapp config appsettings set -g django-app-rg      -n crush-hybrid-maintenance      --settings DJANGO_ECHO_SYNC_URL=https://crush.lu/api/admin/echo-sync/
   ```

   Last, for two reasons. The sweep's first tick reconciles the *entire*
   upcoming calendar, so everything above needs to have been checked by then.
   And the endpoint answers 500 while the switch is off — deliberately, so a
   dormant sweep cannot look green — which the `EchoLuSync` timer records as a
   Failed invocation every hour until step 5 is done.

### Venues

`venues` on an experience is a list of **ids** from echo.lu's own venue
registry — a shared national table of 5,000+ rows, not free text. There is no
search endpoint (ListVenues filters only by category and commune), so the sync
resolves a venue once and caches it in `EchoVenue`:

1. the local cache, keyed on the normalised venue name plus postcode;
2. echo.lu's registry, matched on **normalised equality of the name** — never a
   substring, because attaching our event to somebody else's venue is worse
   than a duplicate and invisible from our side;
3. failing both, `CreateVenue`, and the new id is cached.

Registering into a registry shared with every other organiser is a real side
effect, so `EchoVenue.created_by_us` records which rows we put there.

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

**Confirm in the back office before using `--forget`.** `--audit` asks echo.lu
for its listings in one request and does not page, and whether
`GET /experiences` paginates has never been verified against the real API. If
it does, listings past the first page are reported as `tracked but not returned
by echo.lu (deleted there?)` when they are in fact live — and `--forget` on one
of those clears a valid id, so the next sync creates the duplicate this whole
section exists to avoid. Treat that line as a prompt to go and look, not as an
answer. (Neither `--adopt` nor `--forget` accepts `--dry-run`: they change
stored state, so the combination is refused rather than silently writing.)

Adopting sets the row to **Pending** with no payload fingerprint, so the next
sync sends a full update rather than trusting a hash against a listing whose
content nobody has confirmed.

**One case where `--audit` will find nothing: an orphaned _venue_.** If
`CreateVenue` answers 2xx without an id, the event's row is marked **Orphaned**
as well — that is the state that blocks automatic retries, which is what has to
happen here, because retrying would register a *second* venue into a registry
shared with every other organiser in the country. But the orphan is a venue,
not an experience, and `--audit` only walks experiences, so the usual
`--adopt`/`--forget` path has nothing to work with. The error text saved on the
row says so; the recovery is:

1. find the venue in the echo.lu organiser back office and note its id;
2. create the mapping by hand — an `EchoVenue` row whose `key` is what
   `services.echo_lu.venue_key(location, postcode)` returns for that venue;
3. then `--event-id N --forget` to clear the event's block, since no experience
   was ever created for it.

## Field mapping

| echo.lu | Crush.lu |
| --- | --- |
| `title` / `description` | `title_en` / `description_en`, falling back through the other languages |
| `subtitle` | `get_event_type_display()` |
| `dates[0].from` / `.to` | `date_time` / `end_time`, RFC 3339 in UTC |
| `dates[0].purchaseLink` | the event detail page — the *per-occurrence* link |
| `purchaseLink` (top level) | the event detail page — the **experience-wide** link, and the one that backs the "Commander des billets" button. Not a duplicate of the date-level field: `Ticket` has no link of its own, so this is the only place an experience-wide purchase URL can live. Sending only the date-level one leaves the listing with a price, a buy button and nowhere to buy (seen live 2026-08-15). |
| `venues` | `location` (venue name) |
| `location.address` | `address_street` / `address_number` / `address_postcode` / `address_town`; `commune` gets the town too |
| `location.address.latitude/longitude` | `latitude` / `longitude`, as strings, omitted when unset |
| `pictures[0]` | `image`, made absolute |
| `tickets` | `registration_fee` in EUR; free events get an explicit €0 ticket |
| `contact` | the `ECHO_LU_CONTACT_*` settings; `website` is the event page |
| `languages` | `languages` |
| `categories` / `audiences` / `formats` / `environments` / `tags` | the `ECHO_LU_DEFAULT_*` settings |
| `videos` | the `ECHO_LU_VIDEO_*` settings — one promo video on every listing |

### `videos` is an embed, not an upload

Unlike `pictures`, which echo.lu fetches and **re-hosts**, `videos` is a
reference the portal renders: `{"type": youtube|vimeo|other, "url": …}`. None
of the behaviour below is in the published schema; all of it was probed against
the live API on 2026-08-15, because the alternative was shipping blind again.

* ⚠️ **A self-hosted `.mp4` is accepted and then ignored.** `type: "other"` with
  a direct file URL answers 201, stores, and reads back intact — and the
  listing renders nothing for it. An experience carrying both our
  `cdn.crush.lu` mp4 and a YouTube entry previewed with a real
  `youtube.com/embed/…` iframe for the YouTube one and **no `<video>` element at
  all** for ours. The API reports success either way, so this is invisible from
  the sync side. **Point `ECHO_LU_VIDEO_URL` at YouTube or Vimeo.**
  The mp4 is still hosted at
  `https://cdn.crush.lu/crush-lu-media/marketing/crushlu-spot.mp4` (served with
  `Accept-Ranges: bytes`, so it streams progressively) — it is simply for our
  own surfaces, not for echo.lu.
* **A `PUT` replaces the whole list**, despite the API documenting the field as
  "videos to **add**". Probed on `PUT` specifically, because that is the verb
  `update_experience` uses — a result taken from `PATCH` would not have covered
  the update path. This is what makes it safe for the hourly sweep to re-send
  the same entry every pass; nothing accumulates.
* **`videos: []` is accepted on both verbs** — 200 on `PUT`, where it clears a
  stored video, and 201 on `POST`. So the key travels on every payload: it is
  how emptying `ECHO_LU_VIDEO_URL` *retracts* a video instead of stranding it,
  and it means a deployment with no video configured — every slot on the day
  this shipped — creates events without the experience being refused.
* **`cover` is silently dropped** — sent on create, absent on read back, the
  same accept-then-discard behaviour as `address.commune`. There is therefore no
  poster-image setting, and a `type: "other"` embed has no thumbnail to offer.
* **An unrecognised `type` is a `400 Malformed videos data` that refuses the
  ENTIRE experience.** A typo in one app setting would stop every event
  syncing, so `build_video_payload()` checks the value locally and sends no
  video rather than a payload echo.lu would reject outright.

⚠️ **Acceptance is not rendering**, and this field proved it. `commune` was the
precedent — taken, stored, ignored. `videos` does the same for `type: "other"`,
and a stored entry that reads back perfectly is not evidence of anything.
Check the organiser preview (`…/experiences/<id>/preview`, wizard step 4) for
an actual player before believing a video is published.

Note the back-office **editor** is not a reliable mirror either: section 2.4
showed three empty slots for an experience that demonstrably held two stored
videos. The preview is the instrument, not the form.

**`commune` is required, and gets the town.** It once carried the event's
`canton`, which is a region rather than a commune. Omitting it looked like the
safe correction — echo.lu's commune filter is a controlled vocabulary where an
unrecognised value 404s the venue search — but the field is mandatory: on
2026-08-10 every event was rejected with `location.address: Missing or malformed
address`, including three that were already live, and the whole integration
stopped publishing until the town was sent.

⚠️ **Known limitation.** A Luxembourg commune takes its name from its principal
town, so this is right wherever the two coincide (Luxembourg, Differdange,
Esch-sur-Alzette). It is wrong where they do not: **Rodange is in the commune of
Pétange**, Belval is in Sanem, and a quarter like Ville-Haute is not a commune at
all. Booking a venue in one of those will send a town where echo.lu expects a
commune, and may be rejected or filed under the wrong municipality. Fixing it
properly needs a postcode→commune table, or a `commune` field of its own on the
event. Until then, check the listing after publishing an event outside the
towns above.

**Blank contact and address fields** are dropped rather than sent — echo.lu
treats an empty string as a supplied value and renders a blank line for it.

`dates[].duration` **is** sent, as `duration_minutes`. It was held back while
its unit was undocumented — a wrong unit would have contradicted `from`/`to` on
a public listing — but the API documents it as an integer count of minutes.

The address is typed one component per field in the admin, so nothing is parsed
at publish time. Events created before that split fall back to parsing their
legacy free text, best-effort and never guessed: a component that cannot be
identified with confidence is left blank rather than invented. Find them with:

```bash
python manage.py backfill_event_addresses --audit
```

## Troubleshooting

Sync state per event lives on `EchoExperienceSync` and is shown in the admin —
a status column on the event list, and the full detail (experience id, last
sync, last error) on the event's change form.

| Symptom | Cause |
| --- | --- |
| Every sync fails with a 4xx | Almost always an unknown taxonomy slug. Run `echo_taxonomy --check`. |
| `sync_events_to_echo` errors "sync is disabled" | `ECHO_LU_SYNC_ENABLED` or `ECHO_LU_API_KEY` is unset. Deliberate: a silent no-op on a scheduled job is indistinguishable from "nothing to do". |
| Bare 401 | The key is wrong or revoked. There is only one environment, so there is no "wrong environment" to be in. |
| "accepted the experience but returned no id" | echo.lu created a listing we cannot address. The row is **Orphaned** and stays blocked until a person resolves it — re-running does nothing. Run `--audit`, then `--adopt` the id or `--forget` it; see *Orphaned listings*. |
| The sweep reports events deferred every run | echo.lu is slow, or `ECHO_LU_SWEEP_BUDGET_SECONDS` is set at or below `ECHO_LU_TIMEOUT_SECONDS` — the pass reserves one timeout of headroom, so a budget below that defers everything. |
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
