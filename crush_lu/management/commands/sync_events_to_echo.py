"""
Publish Crush.lu events to echo.lu, Luxembourg's national events portal.

Reconciles both directions in one pass: events that qualify for a public
listing are created or updated, and events that no longer qualify (just
unpublished, just cancelled, gone private, or finished) are withdrawn.
Idempotent — an event whose payload is unchanged since the last accepted
write costs no API call, so this is safe to run on a schedule.

Usage:
    # Standard run (hourly timer)
    python manage.py sync_events_to_echo

    # See what would happen, without a key and without writing anything
    python manage.py sync_events_to_echo --dry-run

    # One event, ignoring the unchanged-payload shortcut
    python manage.py sync_events_to_echo --event-id 42 --force

    # Take an event off echo.lu without unpublishing it on crush.lu
    python manage.py sync_events_to_echo --event-id 42 --withdraw

    # Compare echo.lu's listings against the ones we track (read-only)
    python manage.py sync_events_to_echo --audit

    # Resolve a blocked event after --audit says what happened to its listing
    python manage.py sync_events_to_echo --event-id 42 --adopt exp_abc123
    python manage.py sync_events_to_echo --event-id 42 --forget

Requires ECHO_LU_API_KEY and ECHO_LU_SYNC_ENABLED=true. Without them the
command reports what it would do and exits without touching echo.lu.

Exits non-zero when the sweep itself is unhealthy — the key refused, the
account rate-limited, echo.lu erroring or not answering — because the Azure
Function timer reads a clean exit as a healthy invocation, and a sweep that
syncs nothing for a week must not look the same as one with nothing to do.

A failure that belongs to one event — echo.lu rejecting its payload, a venue
nobody has linked, a listing blocked on an untracked create — does not fail
the run. It is recorded on that event's sync row and named in one WARNING per
sweep, because it fails identically every hour until a person fixes that
event, and 500ing the endpoint on it hid every other exception. How many
events share a failure never changes its scope. With --event-id or --withdraw
any failure is fatal, since those are an operator asking for one specific
outcome.
"""

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from crush_lu.models import MeetupEvent
from crush_lu.services import echo_lu

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync published Crush.lu events to echo.lu experiences."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the planned actions without calling echo.lu",
        )
        parser.add_argument(
            "--event-id",
            type=int,
            help="Restrict to a single event id",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Resend even when the payload is unchanged since the last sync",
        )
        parser.add_argument(
            "--withdraw",
            action="store_true",
            help=(
                "Take the selected events off echo.lu (requires --event-id or "
                "--all-listed); the events stay published on crush.lu"
            ),
        )
        parser.add_argument(
            "--all-listed",
            action="store_true",
            help="With --withdraw, target every event that currently has a listing",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Stop after this many events (useful for a first cautious run)",
        )
        parser.add_argument(
            "--max-seconds",
            type=float,
            default=None,
            help=(
                "Wall-clock ceiling for the pass; whatever is left over is "
                "picked up by the next run (default: "
                "ECHO_LU_SWEEP_BUDGET_SECONDS)"
            ),
        )
        parser.add_argument(
            "--show-payload",
            action="store_true",
            help="Print the JSON that would be sent (implies verbose output)",
        )
        parser.add_argument(
            "--audit",
            action="store_true",
            help=(
                "List the experiences echo.lu holds for our key and flag any we "
                "do not track; syncs nothing"
            ),
        )
        parser.add_argument(
            "--adopt",
            metavar="EXPERIENCE_ID",
            help=(
                "Attach an existing echo.lu experience id to --event-id and "
                "clear the blocked state, so syncing resumes against that "
                "listing instead of creating a second one. This is how an "
                "orphan found by --audit is recovered."
            ),
        )
        parser.add_argument(
            "--forget",
            action="store_true",
            help=(
                "Clear --event-id's blocked state without adopting an id, for "
                "when the untracked listing was deleted in the back office. "
                "The next sync creates a fresh listing."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        event_id = options["event_id"]
        force = options["force"]
        withdraw = options["withdraw"]
        all_listed = options["all_listed"]
        limit = options["limit"]
        show_payload = options["show_payload"]

        if withdraw and not (event_id or all_listed):
            raise CommandError(
                "--withdraw needs --event-id or --all-listed so it cannot take "
                "the whole calendar down by accident"
            )

        if options["adopt"] or options["forget"]:
            if dry_run:
                # These are the only options here that write to the database,
                # and they are dispatched before any of the dry-run handling
                # below — so `--dry-run --forget` would clear an orphan block
                # while the setup guide says a dry run writes nothing. The
                # blocked state is the one thing standing between an untracked
                # listing and a second one beside it, so a flag that reads as
                # "show me what would happen" must not be what lifts it.
                raise CommandError(
                    "--adopt and --forget change the stored sync state, so "
                    "they cannot be combined with --dry-run. Run --audit "
                    "first to see what echo.lu holds; that writes nothing."
                )
            return self._resolve_orphan(event_id, options["adopt"], options["forget"])

        if options["audit"]:
            return self._audit()

        enabled = echo_lu.is_sync_enabled()
        if not enabled and not dry_run:
            # Fail loudly rather than reporting "0 synced" — a silent no-op on a
            # scheduled job looks identical to "nothing to do" and hides a
            # missing key for weeks.
            raise CommandError(
                "echo.lu sync is disabled. Set ECHO_LU_SYNC_ENABLED=true and "
                "ECHO_LU_API_KEY in the environment, or pass --dry-run to "
                "preview without calling the API."
            )

        events = self._select_events(event_id, withdraw, all_listed)
        if limit:
            events = events[:limit]

        # No in-call retries on the sweep: the sweep *is* the retry, and it
        # runs again in an hour. Keeping them would make one event's worst
        # case ~95s (four attempts plus retry sleeps), which no budget below
        # the Function's 110s can accommodate even once.
        timeout = getattr(settings, "ECHO_LU_TIMEOUT_SECONDS", 20)
        client = None if dry_run else echo_lu.EchoLuClient(max_retries=0)
        counts = {}
        failures = []
        blocked = []

        # The EchoLuSync Function gives this endpoint 110s. One event can burn
        # most of a minute on its own against a struggling echo.lu (timeout
        # plus retry sleeps), so an unbounded loop over the calendar is a
        # gamble on being killed part-way rather than stopping cleanly. Stop on
        # our own terms instead: the sweep is idempotent and selects by state,
        # so the next hour resumes with exactly what is left.
        budget = options["max_seconds"]
        if budget is None:
            budget = getattr(settings, "ECHO_LU_SWEEP_BUDGET_SECONDS", 90)
        # Reserve one worst-case call. Checking only that the budget has not
        # already run out lets an event start at 89s of 90 and then spend a
        # whole timeout more, which is how a bounded sweep still overruns the
        # Function. With retries off that worst case is one timeout. The one
        # possible second call — the draft-or-deleted GET after a take-down
        # answered "no published experience found" — needs no reservation of
        # its own: it is cut to what is left of the budget (client.deadline,
        # below) and skipped when nothing is, leaving the row PENDING for the
        # next sweep. Reserving for it too made any budget of two timeouts or
        # less start nothing, every hour, for good.
        deadline = (
            time.monotonic() + budget - timeout if budget and not dry_run else None
        )
        if client is not None and budget:
            # Where the budget really ends, for the one optional follow-up
            # call (the draft-or-deleted GET): it is cut short or skipped
            # rather than run past this.
            client.deadline = time.monotonic() + budget
        deferred = 0

        for index, event in enumerate(events):
            if deadline is not None and time.monotonic() >= deadline:
                deferred = len(events) - index
                break

            if show_payload and echo_lu.should_publish(event):
                import json

                # Cached venue ids, so the preview shows the `venues` the real
                # run would send. Without them it printed `[]` every time — and
                # `venues` is both the field this integration is least sure of
                # and the one the setup guide tells operators to read before
                # turning the switch on, so a preview that cannot show it is a
                # preview that cannot be trusted for the thing it exists for.
                # Cached only: a preview must never resolve, because resolving
                # can register a venue.
                preview = echo_lu.build_experience_payload(
                    event, venue_ids=echo_lu.cached_venue_ids(event)
                )
                missing = echo_lu.missing_required_fields(preview)
                self.stdout.write(
                    json.dumps(preview, indent=2, ensure_ascii=False, default=str)
                )
                if missing:
                    self.stdout.write(
                        self.style.WARNING(
                            "  ↑ echo.lu would reject this: missing "
                            + ", ".join(missing)
                            + (
                                " (venues resolves on the real run)"
                                if missing == ["venues"]
                                else ""
                            )
                        )
                    )

            try:
                if withdraw:
                    # An operator asking for this by name outranks the event's
                    # own state: --withdraw on a still-published event must
                    # survive the next sweep, not be undone by it.
                    outcome = echo_lu.withdraw_event(
                        event, client=client, dry_run=dry_run, explicit=True
                    )
                else:
                    outcome = echo_lu.sync_event(
                        event, client=client, force=force, dry_run=dry_run
                    )
            except echo_lu.EchoLuError as exc:
                # Keep going: one event with a bad address should not stop the
                # rest of the calendar from reaching the portal. Every failure
                # is also persisted on the sync row for the admin to show.
                failures.append((event, exc))
                self.stdout.write(
                    self.style.ERROR(f"  ✗ [{event.pk}] {event.title}: {exc}")
                )
                continue

            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome == "blocked":
                blocked.append(event)
            if outcome in ("unchanged", "skipped"):
                continue
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ [{event.pk}] {event.title}: {outcome}")
            )

        self._report(counts, failures, dry_run, deferred)

        isolated = [(e, exc) for e, exc in failures if echo_lu.is_isolated_failure(exc)]
        systemic = [
            (e, exc) for e, exc in failures if not echo_lu.is_isolated_failure(exc)
        ]

        # Scope comes from the kind of failure alone, never from how many
        # events happened to share it in one run. Counting was tried and is
        # wrong both ways: a retired shared slug usually reaches only the one
        # event that needed a write this hour, and two unrelated rejections
        # can share a generic body. A bad shared slug therefore shows up as a
        # per-event warning carrying echo.lu's rejection text, and
        # `echo_taxonomy --check` is the gate that validates those settings.

        if isolated or blocked:
            # Named here because nothing else names them: the lines above go
            # to a stdout the endpoint only keeps on success, at INFO. One
            # line per sweep, so a stuck event costs one warning an hour
            # rather than an exception, and says which event and why.
            #
            # `blocked` belongs here rather than in the failure below. An
            # orphan never recovers by itself, so it must not go quiet — but
            # failing the sweep over one is how a single orphan kept the
            # EchoLuSync timer red for 18 days without the exception ever
            # saying which event it was.
            attention = [
                f"[{e.pk}] {str(exc)[:300]}"
                # The one isolated failure with a manual way out: once the
                # back office shows the listing is gone, --forget settles it.
                + (
                    f" — if it is gone from echo.lu, run --event-id {e.pk} --forget"
                    if echo_lu.listing_not_in_folder(exc)
                    else ""
                )
                for e, exc in isolated
            ] + [
                f"[{e.pk}] blocked on an untracked listing — run --audit"
                for e in blocked
            ]
            # Rendered here rather than passed as %s arguments: the production
            # PII filter masks any argument containing "@" wholesale, as if it
            # were one email, and event titles and echo.lu's error bodies can
            # hold one. As the message, only real addresses get masked — see
            # api_admin_events._log_output.
            logger.warning(
                f"[ECHO] sweep left {len(attention)} event(s) needing attention: "
                + "; ".join(attention)
            )

        if systemic or ((event_id or withdraw) and (failures or blocked)):
            # The Function turns a clean return into a successful invocation,
            # so swallowing a sweep-wide failure would leave a revoked key or
            # a day-long outage showing green on the timer's failure count —
            # the one signal anybody is watching. Every event was still
            # attempted. With --event-id there is only one event, and its
            # failure is the whole answer. With --withdraw somebody asked for
            # listings to come down, and any one still up means it did not
            # work — a script taking the calendar off must not exit 0.
            parts = []
            if systemic:
                parts.append(
                    f"{len(systemic)} event(s) failed on echo.lu's side or the "
                    f"key's, or on a shared setting (auth, a missing route, "
                    f"rate limit, timeout, 5xx, no answer, an empty "
                    f"ECHO_LU_DEFAULT_* facet or fallback picture)"
                )
            if isolated:
                parts.append(f"{len(isolated)} event(s) rejected by echo.lu")
            if blocked:
                parts.append(f"{len(blocked)} event(s) blocked on an untracked listing")
            raise CommandError(
                f"{'; '.join(parts)}. See the errors above and the sync row on "
                f"each event; --audit resolves the blocked ones."
            )

    def _resolve_orphan(self, event_id, adopt, forget):
        """Take an event out of the blocked state, the one way out of it.

        The blocked state is deliberately immovable from code — nothing
        automatic can clear it, because the whole point is that only a person
        who has looked at echo.lu knows whether a listing is there. This is
        that person's tool: `--adopt` when `--audit` found the listing and its
        id should be reattached, `--forget` when it was deleted in the back
        office and a fresh one should be created next sync.

        It also takes a Failed row whose last error is echo.lu's 404 "no
        experience found in your folder". That answer cannot tell a deleted
        listing from one the key can no longer see, so the sync keeps the id
        and keeps retrying (see ``echo_lu.listing_not_in_folder``); this is how
        a person who has checked the back office settles it.

        Writes nothing to echo.lu, so it needs neither the key nor the switch.
        """
        from crush_lu.models.echo_lu import EchoExperienceSync

        if not event_id:
            raise CommandError("--adopt and --forget need --event-id")
        if adopt and forget:
            raise CommandError(
                "--adopt and --forget do opposite things; pass one of them"
            )

        try:
            sync = EchoExperienceSync.objects.get(event_id=event_id)
        except EchoExperienceSync.DoesNotExist:
            raise CommandError(
                f"Event {event_id} has no echo.lu sync row, so there is "
                f"nothing blocked to resolve."
            )

        # Gated on the recorded answer, not on Failed alone: a row failed by
        # a 503 or a timeout still holds a perfectly good id.
        not_in_folder = (
            sync.status == EchoExperienceSync.Status.FAILED
            and echo_lu.NOT_IN_FOLDER_PHRASE in sync.last_error.lower()
        )
        if sync.status != EchoExperienceSync.Status.ORPHANED and not not_in_folder:
            # Pointed at a healthy row, --forget would clear a perfectly good
            # experience id and the next sync would POST a second listing
            # beside the live one — the exact duplicate this command exists to
            # clean up. A mistyped event id is all it would take.
            raise CommandError(
                f"Event {event_id} is {sync.get_status_display()}, not "
                f"blocked. --adopt and --forget only apply to a blocked row, "
                f"or to one echo.lu no longer finds in the key's folder; on a "
                f"healthy one they would strand its listing. Use "
                f"--event-id {event_id} --force to resync it instead."
            )

        previous = sync.get_status_display()
        if adopt:
            adopt = str(adopt).strip()
            if not adopt:
                # `--adopt ""` would otherwise clear the id and drop the row to
                # PENDING, which is --forget's behaviour under --adopt's name:
                # the next sync creates a listing while the untracked one this
                # command was called to recover may still be live.
                raise CommandError(
                    "--adopt needs an experience id. To clear the id instead "
                    "— only when the listing really is gone from echo.lu — "
                    "use --forget."
                )
            clash = (
                EchoExperienceSync.objects.filter(experience_id=adopt)
                .exclude(pk=sync.pk)
                .first()
            )
            if clash is not None:
                # Two events pointing at one listing is not a smaller problem
                # than the orphan: each sweep would PUT a different payload to
                # the same id, so the listing flips between two events and one
                # of them is never really published. A mistyped id is all it
                # takes, so it is worth the query.
                raise CommandError(
                    f"Experience {adopt} is already tracked by event "
                    f"{clash.event_id}. Adopting it here would point two "
                    f"events at one listing and they would overwrite each "
                    f"other every sweep. Check the id against --audit."
                )
            sync.experience_id = adopt
        else:
            sync.experience_id = ""
        # PENDING, not SYNCED: no payload has been confirmed against this
        # listing, so the next sync must send a full update rather than
        # trusting a fingerprint nobody has verified.
        sync.status = EchoExperienceSync.Status.PENDING
        sync.payload_hash = ""
        sync.last_error = ""
        sync.save(
            update_fields=[
                "experience_id",
                "status",
                "payload_hash",
                "last_error",
                "updated_at",
            ]
        )

        if adopt:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Event {event_id}: adopted experience {sync.experience_id} "
                    f"(was {previous}). The next sync updates that listing."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Event {event_id}: cleared the experience id (was "
                    f"{previous}). The next sync creates a new listing — make "
                    f"sure the old one is really gone from echo.lu first."
                )
            )

    def _audit(self):
        """Compare what echo.lu holds against what we think we published.

        The failure this exists for: a create that answers 2xx without an id
        leaves a listing we can never address again. Nothing else can find
        those — they are invisible from our side by definition — so the only
        way to see them is to ask echo.lu what it has and subtract what we
        track. Read-only, and it needs the key but not the sync switch.
        """
        from crush_lu.models.echo_lu import EchoExperienceSync

        client = echo_lu.EchoLuClient()
        try:
            # Paged, because echo.lu pages. A single unpaged call used to be
            # taken for the whole portal, which made this command report live
            # listings as "deleted there?" — and `--forget` on that advice is
            # exactly how a duplicate gets created. If any page fails the whole
            # audit fails: a partial answer is worse than none, because it
            # reads as a complete one.
            remote = _experience_entries(client.iter_experiences())
        except echo_lu.EchoLuError as exc:
            raise CommandError(str(exc))
        tracked = dict(
            EchoExperienceSync.objects.exclude(experience_id="").values_list(
                "experience_id", "event_id"
            )
        )

        self.stdout.write(f"echo.lu holds {len(remote)} experience(s):")
        untracked = []
        for experience_id, title in remote:
            event_id = tracked.get(experience_id)
            if event_id:
                self.stdout.write(f"  ✓ {experience_id}  event {event_id}  {title}")
            else:
                untracked.append((experience_id, title))
                self.stdout.write(
                    self.style.WARNING(f"  ? {experience_id}  UNTRACKED  {title}")
                )

        missing = [
            (experience_id, event_id)
            for experience_id, event_id in tracked.items()
            if experience_id not in {rid for rid, _title in remote}
        ]
        for experience_id, event_id in missing:
            self.stdout.write(
                self.style.WARNING(
                    f"  ! {experience_id}  event {event_id} — tracked but not "
                    f"returned by echo.lu (deleted there?)"
                )
            )

        self.stdout.write("")
        if untracked:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(untracked)} untracked listing(s). Delete them in the "
                    f"echo.lu organiser back office, or adopt one by setting its "
                    f"id on the event's EchoExperienceSync row."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("No untracked listings."))

    def _select_events(self, event_id, withdraw, all_listed):
        if event_id:
            queryset = MeetupEvent.objects.filter(pk=event_id).select_related(
                "echo_sync"
            )
            if not queryset.exists():
                raise CommandError(f"No MeetupEvent with id {event_id}")
            return list(queryset)

        if withdraw and all_listed:
            return list(
                MeetupEvent.objects.filter(echo_sync__isnull=False)
                .exclude(echo_sync__experience_id="")
                .select_related("echo_sync")
            )

        return list(echo_lu.events_needing_sync())

    def _report(self, counts, failures, dry_run, deferred=0):
        prefix = "Would sync" if dry_run else "Synced"
        summary = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{prefix}: {summary or 'nothing'}"))

        if deferred:
            self.stdout.write(
                self.style.WARNING(
                    f"Stopped on the time budget with {deferred} event(s) left; "
                    f"the next run continues from there. Raise "
                    f"ECHO_LU_SWEEP_BUDGET_SECONDS (or --max-seconds) if this "
                    f"keeps happening — it usually means echo.lu is slow."
                )
            )

        if counts.get("blocked"):
            self.stdout.write(
                self.style.ERROR(
                    f"{counts['blocked']} event(s) blocked: echo.lu accepted a "
                    f"create without returning an id, so a listing exists that "
                    f"we cannot address. Run --audit to find it, then adopt its "
                    f"id on the sync row or delete it in the back office."
                )
            )

        if counts.get("disabled"):
            self.stdout.write(
                self.style.WARNING(
                    "Some events reported 'disabled' — ECHO_LU_SYNC_ENABLED is off."
                )
            )

        if failures:
            self.stdout.write(
                self.style.ERROR(f"{len(failures)} event(s) rejected by echo.lu:")
            )
            for event, exc in failures:
                self.stdout.write(self.style.ERROR(f"  [{event.pk}] {exc}"))
            self.stdout.write(
                "Unknown category/audience/format slugs are the usual cause — "
                "run `python manage.py echo_taxonomy --check` to compare the "
                "configured values against echo.lu's vocabularies."
            )


def _experience_entries(records):
    """Normalise experience records into [(id, title), ...].

    Takes an iterable of records — `client.iter_experiences()` yields them
    across pages — but still accepts a raw response body so a caller holding
    one page does not have to unwrap it first. The envelope key is ``records``;
    it was absent from the original list and every audit therefore parsed as an
    empty portal.
    """
    if isinstance(records, dict):
        records = echo_lu.response_records(records)

    entries = []
    for entry in records or []:
        if not isinstance(entry, dict):
            continue
        experience_id = echo_lu.extract_experience_id(entry)
        title = entry.get("title") or ""
        if isinstance(title, dict):
            title = title.get("en") or next(iter(title.values()), "")
        if experience_id:
            entries.append((experience_id, str(title)))
    return entries
