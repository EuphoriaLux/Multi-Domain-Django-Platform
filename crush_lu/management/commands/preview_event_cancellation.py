"""preview_event_cancellation — what cancelling an event would actually do.

The admin "cancel events" action (``MeetupEventAdmin.cancel_events``) does a
lot silently and, crucially, tells **nobody**: there is no organiser-cancellation
email or push anywhere in this codebase. ``event_cancellation.html`` is the
template for a *member dropping their own seat*, not for the event being called
off. So the people who need contacting have to be pulled out by hand, and the
money likewise — there is no refund path either (``PaymentTransaction.REFUNDED``
is a status nothing ever sets; see ``views_payments.py``).

This command answers the three questions you need before pressing that button:

  1. What changes mechanically (listings, echo.lu, wallet passes)?
  2. Who must be contacted, and how do I reach them?
  3. Who is owed money, and who is explicitly NOT?

On (3), the binding rule is **Terms of Service §7.3**, and it is not a single
rule but four:

    member cancels >48h before  -> full refund
    member cancels 24-48h before -> 50%
    member cancels <24h before   -> nothing
    CRUSH.LU cancels the event   -> full, regardless of timing

So the two groups are computed differently and reported apart. Everyone still
holding a seat when you call the event off is owed the full amount, no timing
test. A member who had already dropped their own seat is owed whatever §7.3 says
for *when they dropped it* — which is often still a full refund, not nothing.

⚠️ That timing cannot be read exactly. ``EventRegistration`` has no
``cancelled_at``; the closest thing is ``updated_at`` (auto_now), which is the
last time anything touched the row. It is a good proxy and it is not proof, so
every §7.3 tier below is printed as an ESTIMATE with the hours it was derived
from. Check any row near a boundary by hand.

Either way the money is invisible to the query you would reach for first: a
self-cancelled registration's PaymentTransaction still reads ``paid``, because
``REFUNDED`` is never set by anything.

This command is READ-ONLY. It writes nothing, sends nothing, and charges
nothing. The apply path is the admin action; run this first, act on the report,
then press it.

Usage::

    # Which upcoming events could I cancel?
    python manage.py preview_event_cancellation

    # Full report for one event
    python manage.py preview_event_cancellation --event-id 42

    # Just the address list, ready to paste into a BCC field
    python manage.py preview_event_cancellation --event-id 42 --emails
"""

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction

# Registration states that still represent a person expecting to turn up (or
# waiting for a seat). These are the people who must be told, and the only ones
# with a refund claim. "cancelled" is excluded deliberately — see module
# docstring.
LIVE_STATUSES = ("confirmed", "pending", "waitlist")

# Terms of Service §7.3. Hours before event start -> share of the fee owed when
# the MEMBER is the one who cancelled. When Crush.lu cancels, none of this
# applies: the refund is full regardless of timing.
TOS_FULL_REFUND_HOURS = 48
TOS_HALF_REFUND_HOURS = 24

RULE = "=" * 72
THIN = "-" * 72


def tos_tier(event, registration):
    """(share owed, label, hours before start) under ToS §7.3 for a self-cancel.

    ``updated_at`` stands in for the cancellation time — there is no
    ``cancelled_at`` on the model. Anything else that wrote to the row after the
    member cancelled moves it later, which biases the estimate toward a SMALLER
    refund, so a row this calls "none" is the one worth checking by hand.
    """
    hours = (event.date_time - registration.updated_at).total_seconds() / 3600
    if hours >= TOS_FULL_REFUND_HOURS:
        return Decimal("1.00"), "FULL", hours
    if hours >= TOS_HALF_REFUND_HOURS:
        return Decimal("0.50"), "50%", hours
    return Decimal("0.00"), "none", hours


class Command(BaseCommand):
    help = (
        "Dry-run an event cancellation: what changes, who must be contacted, "
        "and who is owed a refund. Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--event-id",
            type=int,
            help="MeetupEvent id to preview. Omit to list candidate events.",
        )
        parser.add_argument(
            "--emails",
            action="store_true",
            help="Print only a comma-separated address list for the people who "
            "must be told (for pasting into BCC).",
        )

    # -- entry ----------------------------------------------------------
    def handle(self, *args, **options):
        event_id = options.get("event_id")
        if not event_id:
            self._list_candidates()
            return

        try:
            event = MeetupEvent.objects.get(pk=event_id)
        except MeetupEvent.DoesNotExist:
            raise CommandError(f"No MeetupEvent with id={event_id}")

        regs = list(
            EventRegistration.objects.filter(event=event)
            .select_related("user")
            .order_by("status", "user__email")
        )
        live = [r for r in regs if r.status in LIVE_STATUSES]
        self_cancelled = [r for r in regs if r.status == "cancelled"]

        if options["emails"]:
            self._write_email_list(live)
            return

        self._report_header(event, regs)
        self._report_mechanics(event, regs)
        self._report_contacts(live)
        self._report_refunds(event, live, self_cancelled)
        self._report_edge_cases(event, regs)

    # -- sections -------------------------------------------------------
    def _list_candidates(self):
        # Deliberately looks BACKWARD as well. The event you most urgently need
        # this report for is the one you just called off hours before it was due
        # to start, and a strictly-future filter drops it off its own index
        # exactly when you go looking. A week covers the refund window.
        now = timezone.now()
        events = MeetupEvent.objects.filter(
            is_cancelled=False, date_time__gte=now - timedelta(days=7)
        ).order_by("date_time")[:20]
        if not events:
            self.stdout.write("No upcoming, non-cancelled events.")
            return
        self.stdout.write(self.style.MIGRATE_HEADING("Upcoming events"))
        self.stdout.write(THIN)
        for ev in events:
            seats = ev.get_confirmed_count()
            self.stdout.write(
                f"  id={ev.pk:<5} {ev.date_time:%Y-%m-%d %H:%M}  "
                f"{ev.title[:44]:<44} {seats}/{ev.max_participants} seats"
            )
        self.stdout.write("")
        self.stdout.write("Re-run with --event-id <id> for the full report.")

    def _report_header(self, event, regs):
        self.stdout.write(RULE)
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"CANCELLATION PREVIEW — {event.title}")
        )
        self.stdout.write(RULE)
        self.stdout.write(f"  Event id        : {event.pk}")
        self.stdout.write(f"  Starts          : {event.date_time:%a %d %b %Y %H:%M}")
        self.stdout.write(f"  Fee             : EUR {event.registration_fee}")
        self.stdout.write(
            f"  Capacity        : {event.get_confirmed_count()}/"
            f"{event.max_participants} confirmed"
        )
        self.stdout.write(f"  Published       : {event.is_published}")
        self.stdout.write(f"  Already cancelled: {event.is_cancelled}")
        self.stdout.write(f"  Registrations   : {len(regs)} rows total")
        if event.is_cancelled:
            self.stdout.write(
                self.style.WARNING(
                    "\n  NOTE: this event is ALREADY cancelled. The action would "
                    "be a no-op; the rosters below are still accurate."
                )
            )
        self.stdout.write("")

    def _report_mechanics(self, event, regs):
        """What the admin action changes. Traced from cancel_events()."""
        self.stdout.write(self.style.MIGRATE_HEADING("1. WHAT THE ACTION CHANGES"))
        self.stdout.write(THIN)
        for line in (
            "sets is_cancelled=True -> registration closes immediately",
            "event drops off the homepage, events list and My Events",
            "withdraws the listing from echo.lu (national portal)",
            "schema.org JSON-LD flips to EventCancelled (for Google)",
            "reminder emails are suppressed for this event",
            'the "happening now" banner can never appear',
        ):
            self.stdout.write(f"  [x] {line}")

        apple_tickets = (
            EventRegistration.objects.filter(event=event)
            .exclude(apple_wallet_ticket_serial="")
            .count()
        )
        apple_members = (
            CrushProfile.objects.filter(user__eventregistration__event=event)
            .exclude(apple_pass_serial="")
            .distinct()
            .count()
        )
        self.stdout.write(
            f"  [x] voids {apple_tickets} Apple Wallet ticket(s) and refreshes "
            f"{apple_members} member pass(es)"
        )

        # Reuse the action's OWN selector so this count cannot drift from what
        # the real run would push. It must be evaluated pre-cancellation, which
        # is exactly the situation we are in.
        try:
            from crush_lu.admin.events import MeetupEventAdmin

            google = MeetupEventAdmin._google_holders_for_cancellation([event])
            self.stdout.write(
                f"  [x] refreshes {len(google)} Google Wallet member object(s)"
            )
        except Exception as exc:  # pragma: no cover - diagnostic only
            self.stdout.write(
                self.style.WARNING(f"  [?] Google Wallet holders unknown ({exc})")
            )

        self.stdout.write("")
        self.stdout.write(self.style.ERROR("  [ ] emails or notifies attendees: NO."))
        self.stdout.write(
            "      Nothing in the codebase tells a registrant their event was\n"
            "      called off. Everyone in section 2 must be contacted by hand."
        )
        self.stdout.write("")

    def _report_contacts(self, live):
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"2. WHO MUST BE TOLD ({len(live)} people) — the app will not"
            )
        )
        self.stdout.write(THIN)
        if not live:
            self.stdout.write("  Nobody holds a live registration. Nothing to send.")
            self.stdout.write("")
            return

        phones = {
            p.user_id: p.phone_number
            for p in CrushProfile.objects.filter(
                user_id__in=[r.user_id for r in live]
            ).exclude(phone_number="")
        }
        self.stdout.write(f"  {'status':<10} {'email':<34} {'phone':<16} name")
        for reg in live:
            user = reg.user
            name = (user.get_full_name() or user.username or "")[:20]
            self.stdout.write(
                f"  {reg.status:<10} {user.email[:34]:<34} "
                f"{phones.get(user.id, '-')[:16]:<16} {name}"
            )
        self.stdout.write("")
        self.stdout.write("  Re-run with --emails for a paste-ready BCC list.")
        self.stdout.write("")

    def _report_refunds(self, event, live, self_cancelled):
        self.stdout.write(self.style.MIGRATE_HEADING("3. MONEY"))
        self.stdout.write(THIN)
        self.stdout.write(
            "  There is no refund path in this codebase. Everything below has\n"
            "  to be done by hand in the SumUp dashboard, and the app will keep\n"
            "  showing these transactions as 'paid' afterwards.\n"
        )

        live_ids = [r.pk for r in live]
        cancelled_ids = [r.pk for r in self_cancelled]

        owed = list(
            PaymentTransaction.objects.filter(
                event_registration_id__in=live_ids,
                status=PaymentTransaction.Status.PAID,
            ).select_related("event_registration__user")
        )
        not_owed = list(
            PaymentTransaction.objects.filter(
                event_registration_id__in=cancelled_ids,
                status=PaymentTransaction.Status.PAID,
            ).select_related("event_registration__user")
        )

        # --- owed ---
        self.stdout.write(self.style.WARNING(f"  REFUND OWED — {len(owed)} payment(s)"))
        self.stdout.write("  You cancelled, so these people get their money back.\n")
        total = Decimal("0.00")
        if owed:
            self.stdout.write(
                f"    {'email':<32} {'amount':>9}  {'sumup_checkout_id':<38} ref"
            )
            for tx in owed:
                total += tx.amount
                email = (
                    tx.event_registration.user.email if tx.event_registration else "?"
                )
                self.stdout.write(
                    f"    {email[:32]:<32} {tx.amount:>6} {tx.currency}  "
                    f"{(tx.sumup_checkout_id or '-'):<38} {tx.transaction_reference}"
                )
            self.stdout.write(f"\n    TOTAL TO REFUND: {total} EUR")
        else:
            self.stdout.write("    (none)")
        self.stdout.write("")

        # --- self-cancelled: graded by ToS §7.3, not a flat no ---
        self.stdout.write(
            self.style.WARNING(
                f"  SELF-CANCELLED, STILL HOLDING MONEY — {len(not_owed)} payment(s)"
            )
        )
        self.stdout.write(
            "  These members dropped their own seat before you called the event\n"
            "  off, so ToS §7.3 grades them by WHEN they dropped it — >48h full,\n"
            "  24-48h half, <24h nothing. Tiers below are ESTIMATED from\n"
            "  updated_at (there is no cancelled_at); check rows near a boundary.\n"
        )
        if not_owed:
            due = Decimal("0.00")
            kept = Decimal("0.00")
            self.stdout.write(
                f"    {'email':<30} {'paid':>8}  {'h before':>9}  "
                f"{'§7.3':<5} {'owed':>8}"
            )
            for tx in not_owed:
                reg = tx.event_registration
                email = reg.user.email if reg else "?"
                share, label, hours = tos_tier(event, reg)
                owed_amount = (tx.amount * share).quantize(Decimal("0.01"))
                due += owed_amount
                kept += tx.amount - owed_amount
                style = self.style.WARNING if share else self.style.SUCCESS
                self.stdout.write(
                    style(
                        f"    {email[:30]:<30} {tx.amount:>8}  {hours:>9.1f}  "
                        f"{label:<5} {owed_amount:>8}"
                    )
                )
            self.stdout.write(f"\n    OWED UNDER §7.3 : {due} EUR")
            self.stdout.write(f"    RETAINED        : {kept} EUR")
            if due:
                self.stdout.write(
                    self.style.ERROR(
                        "\n    NOTE: each of these seats was offered to the "
                        "waitlist on\n    cancellation "
                        "(promote_waitlist_on_cancellation). Promotion is\n"
                        "    gender-pool gated, so it may have promoted nobody — "
                        "but where\n    the seat WAS resold, retaining money owed "
                        "here means being paid\n    twice for one chair. Check the "
                        "pool before deciding."
                    )
                )
        else:
            self.stdout.write("    (none)")
        self.stdout.write("")

        # The half of a refund that lives in our DB, and is easy to forget.
        if owed or not_owed:
            self.stdout.write(self.style.MIGRATE_HEADING("  AFTER YOU REFUND"))
            self.stdout.write(
                "  Refunding in SumUp is only step one. _admitted_status() reads\n"
                "  payment_confirmed, so a refunded member who re-registers for\n"
                "  THIS event is admitted confirmed and free. Once the money has\n"
                "  actually left SumUp:\n\n"
                "      r = EventRegistration.objects.get(pk=<id>)\n"
                "      r.payment_confirmed = False\n"
                "      r.payment_date = None\n"
                "      r.save(update_fields=['payment_confirmed', 'payment_date'])\n\n"
                "  Order matters: clear the flag only once the refund is real.\n"
            )

    def _report_edge_cases(self, event, regs):
        """Things that will bite quietly if nobody looks for them."""
        self.stdout.write(self.style.MIGRATE_HEADING("4. WATCH OUT"))
        self.stdout.write(THIN)
        found = False

        reg_ids = [r.pk for r in regs]

        # An open checkout can still capture AFTER the event is cancelled —
        # cancelling the event does not close SumUp's side.
        pending = PaymentTransaction.objects.filter(
            event_registration_id__in=reg_ids,
            status=PaymentTransaction.Status.PENDING,
        ).select_related("event_registration__user")
        if pending:
            found = True
            self.stdout.write(
                self.style.WARNING(
                    f"  * {len(pending)} checkout(s) still PENDING. Cancelling the "
                    "event does not\n    close them at SumUp — one could capture "
                    "after you press the button.\n    Verify with: python manage.py "
                    "sumup_checkout_status --registration-id <id>"
                )
            )
            for tx in pending:
                email = (
                    tx.event_registration.user.email if tx.event_registration else "?"
                )
                self.stdout.write(
                    f"      reg={tx.event_registration_id} {email[:36]} "
                    f"{tx.amount} {tx.currency}"
                )

        # Double charges: more than one PAID row against a single registration.
        paid_per_reg = {}
        for tx in PaymentTransaction.objects.filter(
            event_registration_id__in=reg_ids,
            status=PaymentTransaction.Status.PAID,
        ):
            paid_per_reg.setdefault(tx.event_registration_id, []).append(tx)
        doubles = {k: v for k, v in paid_per_reg.items() if len(v) > 1}
        if doubles:
            found = True
            self.stdout.write(
                self.style.ERROR(
                    f"  * {len(doubles)} registration(s) carry MORE THAN ONE paid "
                    "transaction.\n    Refund each row, not each person."
                )
            )
            for reg_id, txs in doubles.items():
                self.stdout.write(
                    f"      reg={reg_id}: {len(txs)} payments totalling "
                    f"{sum(t.amount for t in txs)} EUR"
                )

        # Paid money for this event we cannot attribute to a registration.
        orphans = PaymentTransaction.objects.filter(
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            status=PaymentTransaction.Status.PAID,
            event_registration__isnull=True,
        ).count()
        if orphans:
            found = True
            self.stdout.write(
                self.style.WARNING(
                    f"  * {orphans} paid event payment(s) platform-wide have no "
                    "registration attached\n    (FK is SET_NULL). Some may belong "
                    "to this event — check by hand before refunding."
                )
            )

        # Anyone already through the door on an event being called off.
        odd = [r for r in regs if r.status in ("attended", "no_show")]
        if odd:
            found = True
            self.stdout.write(
                self.style.WARNING(
                    f"  * {len(odd)} registration(s) are already marked "
                    "attended/no_show on an event\n    you are cancelling. Worth a "
                    "look before you press it."
                )
            )

        if not found:
            self.stdout.write("  Nothing unusual.")
        self.stdout.write("")
        self.stdout.write(RULE)
        self.stdout.write("Read-only preview. Nothing above has been changed.")
        self.stdout.write(RULE)

    # -- helpers --------------------------------------------------------
    def _write_email_list(self, live):
        addresses = [r.user.email for r in live if r.user.email]
        if not addresses:
            self.stdout.write("(nobody to contact)")
            return
        self.stdout.write(", ".join(sorted(set(addresses))))
