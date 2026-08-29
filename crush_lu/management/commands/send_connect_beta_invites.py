"""
Invite the Crush Connect waitlist into the beta, one wave at a time.

The 2026-08-21 production pool audit split the 295-person waitlist into three
cohorts by what each member can actually DO the moment they arrive, so nobody
is mailed a promise the gates will refuse:

  --wave 1  Connect Week      event-verified members (incl. dual-verified).
                              ``connect_phase.cycle_access_open`` already lets
                              them in, so the mail can say "start now".
                              Audit expectation: ~83 members.
  --wave 2  In the Mix        LuxID-verified but never verified at an event.
                              Visible in the Mix, but the Week would bounce
                              them, so the mail explains the catalogue and
                              points at the event route.
                              Audit expectation: ~69 members.
  --wave 3  Get verified      on the waitlist, not yet verified for Connect.
                              Told how to become eligible, nothing more.
                              Audit expectation: ~74 members.

**This command grants nothing.** It never writes ``selected_as_tester`` — that
flag opens selected-tester Connect Week access and the Premium purchase funnel,
and mailing a 295-person waitlist must not hand either out. Every
recipient reaches exactly the surface their existing verification already
opens; the wave only decides which true sentence they are told. See the note
on ``CrushConnectWaitlist.beta_invited_at``.

Idempotency: ``beta_invited_at`` is stamped on the row per member, immediately
after that member's email actually sends. Re-running a wave mails nobody
twice, and a crash mid-wave leaves every already-sent member marked. There is
deliberately NO batch-wide ``transaction.atomic``: one rollback at the end
would un-mark every successful send in the run while those emails stay
delivered, which turns a retry into a duplicate mailing.

Consent is checked twice: ``notification_preference`` on the waitlist row (the
member's own "tell me when Connect launches" tick) and the ``marketing`` email
preference inside ``send_connect_beta_invite``.

Usage::

    # ALWAYS look first — prints the cohort size and who is in it.
    python manage.py send_connect_beta_invites --wave 1 --dry-run

    # Send, capped
    python manage.py send_connect_beta_invites --wave 1 --limit 50

    # Send the rest of the wave (default limit 200)
    python manage.py send_connect_beta_invites --wave 1

Scheduling: none. This is a hand-run launch campaign, not a recurring sweep.
"""

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from crush_lu.email_helpers import (
    CONNECT_BETA_WAVE_CONNECT_WEEK,
    CONNECT_BETA_WAVE_IN_THE_MIX,
    CONNECT_BETA_WAVE_UNVERIFIED,
    CONNECT_BETA_WAVES,
    send_connect_beta_invite,
)
from crush_lu.models.crush_connect import CrushConnectWaitlist

# Cross-process lock so two overlapping runs (a retry started before the first
# finished) cannot both select the same un-stamped member and mail them twice.
# Mirrors send_crush_credit_expiry_reminders' SWEEP_LOCK_KEY.
SWEEP_LOCK_KEY = "connect_beta_invite_sweep_lock"
SWEEP_LOCK_TTL = 900  # seconds

WAVE_LABELS = {
    CONNECT_BETA_WAVE_CONNECT_WEEK: "Wave 1 - Connect Week (event-verified)",
    CONNECT_BETA_WAVE_IN_THE_MIX: "Wave 2 - In the Mix (LuxID only)",
    CONNECT_BETA_WAVE_UNVERIFIED: "Wave 3 - Get verified (unverified waitlist)",
}


def wave_for_user(user) -> int:
    """Which invite wave this waitlist member belongs to.

    Resolved from the member's CURRENT verification, not from anything stored,
    so a member who verifies between two runs simply moves cohort.

    ``has_attended_event`` already requires ``verification_status ==
    "verified"``, so an unapproved profile with a past attendance falls to
    wave 3 — matching the audit's tiering, where "event-verified" means the
    coach actually completed the verification, not merely that a seat was
    marked attended.
    """
    profile = getattr(user, "crushprofile", None)
    if profile is None:
        return CONNECT_BETA_WAVE_UNVERIFIED
    if profile.has_attended_event:
        return CONNECT_BETA_WAVE_CONNECT_WEEK
    if profile.has_luxid_connected:
        return CONNECT_BETA_WAVE_IN_THE_MIX
    return CONNECT_BETA_WAVE_UNVERIFIED


def candidates_for_wave(wave):
    """Un-invited, consenting waitlist members currently in ``wave``.

    The wave test needs ``has_attended_event`` / ``has_luxid_connected``,
    which are Python properties over EventRegistration and SocialAccount
    rather than columns, so the cohort is resolved in Python over the whole
    waitlist. At ~295 rows that is one small query plus a bounded walk, not
    something worth denormalising a column for.
    """
    rows = (
        CrushConnectWaitlist.objects.filter(
            beta_invited_at__isnull=True,
            notification_preference=True,
        )
        # A member who paused Crush Connect themselves must not be mailed
        # "your Connect Week is open" — their own pause is what is holding it
        # shut. Nothing deletes the waitlist row when a member onboards, so
        # waitlist -> onboarded -> paused is a reachable, live state that
        # otherwise stays a candidate forever.
        #
        # Written as exclude(...__isnull=False) because that states the rule
        # directly: drop the rows that have a paused membership. The obvious
        # alternative, filter(paused_at__isnull=True), happens to behave
        # identically -- checked, both emit a LEFT OUTER JOIN, because Django
        # promotes the join for __isnull=True -- so members who never onboarded
        # survive either way. That equivalence is the kind of thing that is easy
        # to assume and easy to get backwards, so
        # test_a_member_without_a_membership_row_is_still_a_candidate pins the
        # behaviour rather than trusting either reading of it.
        .exclude(user__crush_connect_membership__paused_at__isnull=False)
        # The coach panic button is a moderation action, not a preference: it
        # "removes a member from every other user's pool and blocks their
        # Connect surfaces" (CrushConnectMembership docstring). Mailing an
        # excluded member "your Connect Week is open" would walk them straight
        # into surfaces the exclusion exists to shut, which is the one failure
        # mode a safety control must not have. Excluded and paused are stored
        # separately on purpose -- so that a voluntary break is never recorded
        # as a moderation action -- so neither filter implies the other and
        # both have to be stated here.
        .exclude(user__crush_connect_membership__excluded_by_coach=True)
        # A ban keeps the CrushProfile but sets ``user.is_active`` False, and a
        # profile deactivation sets ``crushprofile.is_active`` False. Neither is
        # visible to ``can_send_email``, which only reads EmailPreference -- the
        # same P1 already documented on ``send_profile_completion_reminders``,
        # which filters on exactly these two flags for exactly this reason.
        #
        # ``user__is_active`` is a plain filter because every waitlist row has a
        # user. The profile flag must be an ``exclude(...=False)`` instead: a
        # waitlist member need not have a CrushProfile at all (see
        # test_user_without_profile_is_wave_three), and filtering
        # ``crushprofile__is_active=True`` would inner-join those members away.
        .filter(user__is_active=True)
        .exclude(user__crushprofile__is_active=False)
        # The third deactivation flag, and the one neither of the above catches.
        # ``delete_crushlu_profile_only`` is the "delete my Crush.lu presence but
        # keep my PowerUp account" path: it deletes the CrushProfile, KEEPS the
        # Django user active, and sets ``data_consent.crushlu_banned=True`` as a
        # permanent bar on re-creating the profile. The waitlist row is a
        # OneToOne on *User* with on_delete=CASCADE, and that user is retained,
        # so the row survives the deletion with notification_preference intact.
        #
        # Such a member is therefore active, profile-less -- i.e. wave 3 -- and
        # was a live candidate: we would mail a Connect launch invite to someone
        # who deleted their Crush.lu account. Excluded, not filtered on
        # ``crushlu_banned=False``, because a user may have no data_consent row.
        .exclude(user__data_consent__crushlu_banned=True)
        .select_related("user__crushprofile")
        .order_by("joined_at")
    )
    return [row for row in rows if wave_for_user(row.user) == wave]


def paused_candidates_for_wave(wave):
    """The members ``candidates_for_wave`` just dropped for pausing.

    Reporting-only, and deliberately wave-scoped the same way the cohort is: a
    flat count of every paused waitlist row would report wave-2 members while
    wave 1 is being sent, which is worse than saying nothing.
    """
    rows = (
        CrushConnectWaitlist.objects.filter(
            beta_invited_at__isnull=True,
            notification_preference=True,
            user__crush_connect_membership__paused_at__isnull=False,
        )
        # A coach exclusion does not clear ``paused_at``, so "paused AND
        # excluded" is reachable and would otherwise be counted once here and
        # once in ``coach_excluded_candidates_for_wave`` -- two skipped members
        # reported for one real one, which breaks the aggregate reconciliation
        # these counts exist to feed. Attribute the member to the exclusion,
        # matching the send-time guard's precedence: the moderation action is
        # the reason that matters, not the member's own snooze.
        .exclude(user__crush_connect_membership__excluded_by_coach=True)
        .select_related("user__crushprofile")
        .order_by("joined_at")
    )
    return [row for row in rows if wave_for_user(row.user) == wave]


def coach_excluded_candidates_for_wave(wave):
    """The members ``candidates_for_wave`` just dropped for coach exclusion.

    Reporting-only, wave-scoped for the same reason as
    ``paused_candidates_for_wave``. Counted separately from the paused figure
    rather than folded into one "skipped" total: one number is a member's own
    choice and the other is a moderation action, and a coach reading "3
    skipped" cannot tell whether the panic button actually held.
    """
    rows = (
        CrushConnectWaitlist.objects.filter(
            beta_invited_at__isnull=True,
            notification_preference=True,
            user__crush_connect_membership__excluded_by_coach=True,
        )
        .select_related("user__crushprofile")
        .order_by("joined_at")
    )
    return [row for row in rows if wave_for_user(row.user) == wave]


class Command(BaseCommand):
    help = (
        "Email one wave of the Crush Connect waitlist their beta invite. "
        "Grants no entitlement; --dry-run first."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--wave",
            type=int,
            required=True,
            choices=list(CONNECT_BETA_WAVES),
            help=(
                "Which cohort to mail: 1 = Connect Week, 2 = In the Mix, "
                "3 = get verified"
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the cohort and who would be mailed, without sending",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Maximum members to email in one run (default: 200)",
        )

    def handle(self, *args, **options):
        wave = options["wave"]
        dry_run = options["dry_run"]
        limit = options["limit"]

        if limit < 0:
            self.stderr.write(self.style.ERROR("--limit cannot be negative."))
            return

        lock_acquired = False
        if not dry_run:
            acquired = cache.add(SWEEP_LOCK_KEY, "1", SWEEP_LOCK_TTL)
            if acquired is False:
                self.stdout.write(
                    self.style.WARNING(
                        "Another Connect beta invite run is in progress; skipping."
                    )
                )
                return
            if acquired is None:
                # Cache unavailable (Redis down with IGNORE_EXCEPTIONS). Fail
                # open rather than silently no-op every run until it recovers.
                self.stdout.write(
                    self.style.WARNING(
                        "Invite lock cache is unavailable; proceeding without "
                        "cross-process locking."
                    )
                )
            else:
                lock_acquired = True

        try:
            self._run(wave, dry_run, limit)
        finally:
            if lock_acquired:
                cache.delete(SWEEP_LOCK_KEY)

    def _run(self, wave, dry_run, limit):
        candidates = candidates_for_wave(wave)

        self.stdout.write(self.style.MIGRATE_HEADING(WAVE_LABELS[wave]))
        self.stdout.write(
            f"  eligible, not yet invited: {len(candidates)}  "
            f"(limit {limit}{', DRY RUN' if dry_run else ''})"
        )
        # Say so out loud: a cohort that silently shrank looks identical to one
        # that was always that size, and "why did N members not get this?" is
        # the first question anyone asks after a wave goes out.
        paused_skipped = len(paused_candidates_for_wave(wave))
        if paused_skipped:
            self.stdout.write(
                f"  skipped, paused Connect themselves: {paused_skipped}"
            )
        coach_excluded_skipped = len(coach_excluded_candidates_for_wave(wave))
        if coach_excluded_skipped:
            self.stdout.write(
                f"  skipped, excluded by a coach: {coach_excluded_skipped}"
            )

        if not candidates:
            self.stdout.write(self.style.SUCCESS("Nothing to send."))
            return

        if dry_run:
            for row in candidates[:limit]:
                user = row.user
                self.stdout.write(
                    f"  would email {user.email} "
                    f"({user.get_full_name() or user.username})"
                )
            remaining = max(0, len(candidates) - limit)
            if remaining:
                self.stdout.write(f"  ... and {remaining} more beyond --limit {limit}")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry run: would email {min(limit, len(candidates))} member(s)."
                )
            )
            return

        sent = 0
        skipped = 0
        failed = 0
        for row in candidates:
            if sent >= limit:
                break
            user = row.user
            try:
                delivered = send_connect_beta_invite(user, wave)
            except Exception:
                # One bad address must not abandon the rest of the wave. The
                # row stays un-stamped, so the next run retries this member.
                failed += 1
                self.stderr.write(self.style.ERROR(f"  send failed for {user.email}"))
                continue

            if not delivered:
                # Unsubscribed from marketing, or the backend delivered 0.
                # Left un-stamped deliberately: an unsubscribe is not an
                # invite, and stamping it would consume the member's slot.
                skipped += 1
                continue

            # Stamp per member, immediately after their own send — never in one
            # batch commit at the end (see the module docstring).
            row.beta_invited_at = timezone.now()
            row.beta_invite_wave = wave
            row.save(update_fields=["beta_invited_at", "beta_invite_wave"])
            sent += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Wave {wave}: sent={sent} skipped={skipped} failed={failed}"
            )
        )
