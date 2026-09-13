# Premium payments received but not applied

Status: **proposal for product approval; not implemented**. Investigated on
2026-09-13 against `8148fbc52490472866ccf3625daaee958f6e48cf`.
Tracks [issue #925](https://github.com/EuphoriaLux/Multi-Domain-Django-Platform/issues/925)
and the canonical backlog task `t_5c2531d8`.

Every verified Premium capture must have an attributable outcome: the initial
membership activation, or a visible recovery case that ends in activation or a
verified refund. A buyer must not pay again to repair an existing capture.

This document proposes that outcome and supplies local evidence. It does not
authorize live repairs, refunds, deployment, a new billing schedule or opening
Premium purchasing. Price/offer work (A3), checkout navigation (A12), general
notification infrastructure (A14), and the Tier-2 refund timer remain separate.

## Verified current behavior

| Path | Current outcome | Evidence in this checkout |
|---|---|---|
| Verified first capture; eligible pending membership; coach has capacity | Payment becomes `paid`; membership becomes `active`; receipt is queued after commit. | [`_apply_paid_checkout`](../../crush_lu/views_payments.py), [`PremiumMembership.confirm`](../../crush_lu/models/profiles.py), existing `PremiumBetaAllowlistTests` |
| Coach fills up before activation | Payment stays `paid`; membership stays `pending`; an error is logged; no Premium receipt. | `PremiumCompletionRevalidationTests.test_payment_for_a_coach_that_filled_up_does_not_grant_premium` |
| Buyer loses beta selection | Payment stays `paid`; activation is refused; error logged; no success receipt. | `_premium_purchase_refused(lock=True)`; `PremiumBetaAllowlistTests` |
| Membership already cancelled when capture is applied | Payment becomes `paid`; membership remains cancelled; `confirm()` is skipped, with no Premium error log or receipt. | Snapshot probe; existing cancelled-request test |
| Two distinct pending checkouts capture sequentially | First activates and receives one receipt; second becomes `paid` but skips `confirm()`, log and receipt because membership is already active. | Snapshot probe |
| Replaying an already-paid checkout after capacity/eligibility is repaired | `_apply_paid_checkout` immediately returns. `_sync_checkout_with_sumup` only acts on pending payments. This is not a recovery API. | Snapshot probe; both helpers in `views_payments.py` |
| Browser returns after a failed activation | A warning states that payment succeeded but activation did not; it asks the buyer to contact support. This does not cover a buyer who never returns. | `sumup_return`, existing return tests |
| Browser returns from the second capture after first activation | Membership-level status is active, so the return path can show normal Premium success for the duplicate. No per-payment application attribution exists. | `sumup_return` reads `pm.status` |
| Staff rechecks an already-paid row | Refreshes the provider snapshot; it does not retry Premium activation or create a recovery case. | [`PaymentTransactionAdmin.recheck_with_sumup`](../../crush_lu/admin/payments.py) |
| An external refund is reconciled for the second, duplicate payment | **Current reconciliation cancels the shared membership and clears its assigned coach even while the first payment remains paid.** | Snapshot probe; [`Command._reconcile_refunded`](../../crush_lu/management/commands/reconcile_sumup_payments.py) |

Two corrections to the historical investigation matter:

- The payment is **saved** as paid before activation, inside the same outer
  `transaction.atomic()`. It is not committed in a separate transaction before
  `confirm()`. Expected `ValueError` refusals are caught, and early returns commit
  the paid record. An unexpected activation exception rolls that outer block back;
  SumUp can still hold the money while the local row remains pending. A probe
  reproduces this distinction. Preserve this existing capture transaction boundary
  in the recovery work; do not add a separate commit to make a notice easier.
- Not every failure logs an error: already-active and already-cancelled memberships
  bypass the pending-only block. A query limited to paid/non-active memberships
  therefore misses duplicate captures on active memberships.

`PaymentTransaction.failure_reason` describes unsuccessful provider payment
attempts and is cleared on capture. It is not an activation-failure field.
`PremiumMembership` has no activation-payment link. Manual/beta memberships also
exist, so active membership alone does not prove which payment bought it.

The repository's [`infra/alerts.bicep`](../../infra/alerts.bicep) defines request,
exception and availability/performance alerts, not a dedicated recovery queue or
Premium-refusal trace alert. **Live Azure alert wiring, delivery, incident counts
and historical customer outcomes were not inspected.** Do not infer that nobody
was notified, or that there are no affected buyers, from this code-only review.

## Product decisions to approve

These are proposed defaults, not promises already made to customers.

| Decision | Recommended policy | Approval needed |
|---|---|---|
| D1: Member notice or staff alert? | Both: a truthful transactional acknowledgement plus a durable staff case. The return page reads that case. | Approve the notice and operating owner. |
| D2: Duplicate capture | Refund the extra capture in full, manually authorized; preserve the original paid membership and coach. Do not silently buy an extra month or issue credit. | Confirm remedy and who may authorize/execute each refund. |
| D3: Coach unavailable | Offer a coach with capacity, with recorded member agreement; otherwise arrange a full refund. Do not promise an activation date or silently substitute a coach. | Confirm reassignment/refund choice and maximum unresolved wait. |
| D4: Cancelled request or beta revocation | Default to manual refund review. Re-selection/reactivation requires explicit review of eligibility and member intent; a cancelled request must not silently revive. | Confirm exceptions and the approver. |
| D5: Response commitments | Assign an owner and an internal first-review target before enabling notices saying staff will follow up. Suggested internal target: next business day; no bank-settlement deadline in copy. | Name coverage, escalation recipient and the actual service target. |
| D6: Prevent multiple pending checkouts | Separate implementation: reuse a verified payable checkout or safely retire older ones before publishing a replacement, using a durable claim around unlocked provider I/O. | Approve behavior after examining decline/retry UX; never blanket-block all pending rows. |

Neither a receipt nor a recovery notice grants entitlement. The existing
activation receipt remains reserved for a real activation. Any paid-service start
date or recurring-charge adjustment after delayed activation needs an explicit
billing policy; this model currently has no service-period ledger to adjust.

## State and attribution contract

Keep provider settlement separate from membership activation and case handling.
`paid` remains paid until a real refund is verified; no staff button resets a
captured payment to `pending`, `failed` or `cancelled` to make checkout work again.

Proposed data additions, subject to implementation review:

1. An immutable activation-payment attribution for newly paid initial memberships
   (for example `PremiumMembership.activation_payment`, nullable for historical,
   manual and beta grants). One captured payment can fund at most one initial
   activation; one initial activation has at most one source payment. Preserve
   attribution through later cancellation/refund. Do not infer or overwrite it
   from the latest capture or `payment_date`.
2. One `PremiumPaymentRecovery` case per unapplied `PaymentTransaction`, enforced
   by a database uniqueness constraint. Store a stable reason code, lifecycle
   state, creation/assignment/resolution timestamps, assigned staff, optimistic
   version or lock-protected claim, and the approved resolution. Keep capture
   reference, amount and currency on the payment; keep the transaction immutable
   as the financial source. Retain enough linked-ID provenance if the membership
   is removed; do not copy provider secrets or unnecessary personal data.
3. An append-only case event history (actor, reason, decision, resulting state)
   and durable notice records keyed by `(case, event/version, audience)`. Track
   pending/sent/failed/unknown delivery and attempts separately from resolution.
   A failed email must never reopen or undo a completed financial operation.

Reason codes distinguish `coach_unavailable`, `membership_cancelled`,
`beta_ineligible`, `duplicate_capture`, `missing_membership`, and
`attribution_unknown`. An active membership without reliable source attribution
must enter review rather than automatically labelling its payment a duplicate.
Unexpected application errors require investigation/reconciliation; they must not
be labelled as an expected product refusal.

| Verified event | Membership action | Case/result |
|---|---|---|
| First eligible capture | Activate once and record source payment atomically | Applied; ordinary activation receipt after commit |
| Expected activation refusal | Preserve pending/cancelled entitlement state | Create case and acknowledgement intent in the payment transaction |
| Distinct capture for an activation already funded by another payment | Preserve existing activation and its source | Duplicate case; refund review |
| Repeated callback for the same payment | No second application, case or notice intent | Return existing outcome |
| Approved coach/eligibility repair with consent | Revalidate, apply the original captured funds once, record source | `resolved_applied`; activation notice after commit |
| Refund decision recorded | No new entitlement and no claim that cash was returned | `refund_pending` |
| Full refund confirmed for an unapplied/duplicate payment | Do not touch an activation funded by another payment | `resolved_refunded`; refund confirmation |
| Refund confirmed for the actual activation source | Follow reviewed membership cancellation rules | Preserve cancellation provenance and unrelated coach relationships |

Case progression is `open -> in_review -> awaiting_member / ready_to_apply /
refund_pending -> resolved_applied / resolved_refunded`. Reassignment and retries
are recorded events, not deletion/recreation of the case. Unexpected input,
partial refunds, multiple successful transaction IDs inside one checkout, or
missing ownership evidence remain explicit review states; do not guess a remedy.

## Safe application and retry design

The implementation must retain the structural lock order already required by
payments and profile merge: **PaymentTransaction before CrushProfile**. The current
Premium path additionally takes the beta waitlist lock, then membership, coach and
profile locks. Document the complete order across capture, recovery, merge,
cancellation and refund before adding case/provenance locks; check both directions
on PostgreSQL. In a duplicate flow, do not lock a second payment after acquiring
membership/profile locks. Acquire any required payment set first in deterministic
order or use the immutable source attribution without a reversed lock acquisition.

- Keep the existing provider verification authority. Webhook/request status is a
  hint; it cannot manufacture a captured payment. Use stored amounts/currency and
  the verified provider capture identity, not current offer pricing.
- On expected refusal, persist the case and notice intent in the same successful
  outer transaction that preserves `paid`. Use `transaction.on_commit` only to
  attempt/wake delivery; the durable intent must exist before the callback.
- Repair uses a dedicated application operation, not `_apply_paid_checkout` replay,
  not a new checkout and not direct admin edits of payment flags. It rechecks
  captured/not-refunded status, case version, identity, consent, beta eligibility,
  membership state, activation provenance and coach availability under locks.
- Two operators repairing the same case yield one activation and one resolution
  event. Conflicting refund/activation decisions yield a conflict response; a stale
  browser form cannot override the winner. Database uniqueness is the final guard.
- Provider I/O and notice sends occur outside entitlement locks. Preserve the
  existing capture transaction and `on_commit` isolation. Do not treat Django
  `@task.enqueue()` as asynchronous: production uses `ImmediateBackend`.
- A bounded, separately deployed dispatcher may retry **notice delivery**, with
  claim expiry, backoff and a finite attempt count. Timer/job ownership and a
  demonstrated run are prerequisites for promising proactive follow-up. Do not
  silently reuse the campaign dispatcher without an approved integration.
- There is no automatic monetary retry in this proposal. A refund timeout is an
  unknown result: verify provider history before any repeated submission. Never
  assume the provider supports an idempotency key without verifying its contract.
  Mark refunded only with provider evidence, for the correct successful provider
  transaction ID, amount and currency; a checkout ID is not a refund transaction ID.
  SumUp's current [Transactions reference](https://developer.sumup.com/api/transactions)
  identifies refunds by transaction ID and permits full or partial refunds.
  Consequently a `REFUNDED` label alone is not proof that the whole liability was
  returned; compare verified refund amounts/events before closing the case.
- Email submission has a possible send-succeeded/process-crashed ambiguity. Stable
  notice IDs and durable claims prevent ordinary duplicates; they do not promise
  exactly-once delivery when the mail provider has no matching guarantee. Preserve
  an unknown-delivery state for bounded retry/manual review.

**Required dependency:** make Premium refund reconciliation source-aware before
offering duplicate-refund resolution. Its current unconditional membership
cancellation would undo the valid purchase. This is a specific #925 acceptance
case, not permission to change event/credit refund policy or deploy the Tier-2 timer.

## Buyer messaging

Use stored buyer language and the existing email translation machinery, EN/DE/FR
(`DE du`, `FR vous`). For assisted purchases the buyer is `membership.user`, not
`payment.user` (which can be staff). A missing membership or ambiguous buyer enters
staff review; do not mail payment details to a guessed recipient. Retain the normal
authentication/ownership rules for return and case pages, including incomplete
onboarding and beta-revoked members.

Proposed English source copy, translated only during implementation:

| Situation | Copy intent |
|---|---|
| Initial unresolved charge | “We received your payment of %(amount)s %(currency)s, but it has not been applied to your Premium membership. Please do not pay again. Reference: %(reference)s. Contact support@crush.lu if you need help.” |
| Staff ownership and follow-up are operationally verified | Add “Our team will contact you about the next step.” Do not add it merely because `logger.error` executed. |
| Coach choice needs agreement | “Your selected coach is unavailable. We will contact you to agree another coach or arrange a refund.” Requires D3 and active coverage under D5. |
| Duplicate confirmed | “We received an additional payment for the same membership. Your existing Premium membership remains active. The additional payment is under refund review; please do not pay again.” Use only when original entitlement is verified. |
| Refund approved but not executed | “A refund has been approved. We will confirm when it has been issued.” No bank-arrival promise. |
| Refund confirmed by provider | State exact refunded amount/reference and that refund has been issued. Separate any settlement estimate from a guarantee. |
| Recovery activated | Send the existing activation receipt once for its source payment, with the current coach; it is now truthful. |

`support@crush.lu` is the existing contact in the repository's
[`support.html`](../../crush_lu/templates/crush_lu/support.html), email base and
payment return copy. Mailbox routing and actual monitoring were not live-verified;
D5 includes confirming that this published contact reaches the assigned owner.

The same per-payment result drives email and browser return. Never show generic
activation success for an unapplied duplicate solely because the membership is
active. While a capture remains unresolved, all checkout entry paths should explain
the existing payment and suppress re-payment; membership/coach changes must not
create a fresh payable request that evades the existing paid-membership guard.

## Staff queue and operational resolution

Provide a restricted payment-recovery view, distinct from the generic payment list.
Default to unresolved cases ordered by age; filter by reason, assignee and notice
failure. Show case/payment references, amount/currency, payment time, entitlement
source, current membership state, coach, reason, next action and delivery status.
Expose personal details only to staff who need to resolve the case. Do not put raw
provider payloads, full payment data or member details in alert subjects/logs.

The operator claims a case, refreshes read-only provider evidence, checks identity
and all related captures, records the policy decision and obtains any required
member agreement. Activation and refund actions have separate permissions and
explicit confirmation of the selected transaction. A refund resolution requires
Tom's approval under current operating practice; this spec adds no spend authority.
The UI must distinguish “approved”, “submitted/unknown” and “verified refunded”.

Escalate unassigned/overdue cases and delivery failures to the named owner using
case IDs and counts. Demonstrate an alert reaches that owner before launch. A
read-only dashboard is not proof somebody is monitoring it. Closed cases retain
the decision, actor, provider evidence reference and member-notice status.

For the initial read-only inventory, report aggregate counts/age bands rather than
customer details. Include (a) paid payments linked to non-active memberships,
(b) multiple distinct paid captures linked to one membership **including active
memberships**, (c) paid Premium payments with no membership, and (d) provider-paid
but locally pending rows found by verified reconciliation. These are candidates,
not automatic verdicts: later cancellation, manual activation or legitimate future
renewals can make a simple status/count query misleading. Do not backfill the
earliest paid row as the funding source without evidence.

Existing `sumup_checkout_status --sync` or the admin recheck does not repair an
already-paid activation failure. The broad refund reconciliation command is also
not a safe duplicate-recovery recipe until the source-aware guard is implemented.
No production inventory or repair was run for this specification.

## Validation evidence and implementation acceptance

The opt-in [snapshot probes](evidence/test_premium_payment_recovery_snapshot.py)
use synthetic members, a mocked SumUp checkout read and mocked receipt delivery.
They reproduce current defects; their assertions are evidence, **not the desired
post-fix contract**, and are intentionally outside the default CI testpaths.
They make no payment/refund request and send no email. A socket-connect guard
also rejects unexpected network activity in the five new probes.

Run from the dedicated worktree with the project venv active and local test
settings (`SECRET_KEY` set to a test value, `DBHOST` empty):

```powershell
python -m pytest docs/specs/evidence/test_premium_payment_recovery_snapshot.py crush_lu/tests/test_sumup_payments.py::PremiumCompletionRevalidationTests crush_lu/tests/test_sumup_payments.py::PremiumBetaAllowlistTests -n 0 --basetemp=build-artifacts/pytest-925 --tb=short -q
```

Observed result: **38 tests passed** (five snapshot probes and 33 existing Premium
completion/beta cases). Existing Azure-storage and OpenTelemetry deprecation
warnings were emitted. Targeted Ruff/Black and local Markdown-link checks pass.

SQLite proves sequential outcomes and transaction rollback in these probes; it
does not prove row-lock correctness. Before implementation is accepted, replace
the defect assertions with intended-behavior tests and add:

| Case | Required result |
|---|---|
| Ordinary initial capture; callback/return replay | One activation/source attribution and one receipt intent after commit |
| Sequential and simultaneous distinct captures | One funded activation; each extra capture gets its own case and safe acknowledgement |
| Coach becomes unavailable; beta revoked; cancellation before/during confirm | Recorded paid payment plus typed case, no false activation receipt |
| Repair after capacity/eligibility restoration | Applies original captured funds once with required consent; no new charge |
| Two staff apply attempts; refund races apply | Exactly one winning resolution; no reversed locks; conflict is explicit |
| Duplicate refund; first payment still paid | Duplicate becomes refunded; valid membership, source and coach remain intact |
| Actual activation-source refund | Reviewed membership cancellation occurs; unrelated coach assignment is preserved |
| Partial/unknown refund; provider outage | Case remains unresolved, amount is not overstated and no automatic resubmission |
| Missing/deleted profile or membership; staff-assisted buyer | Correct ownership and no accidental disclosure; unresolved case retained |
| Notice raises; crash before/after delivery; repeated dispatcher tick | Financial/entitlement state unaffected; durable intent and honest delivery status |
| DE/FR/EN, incomplete onboarding, beta revoked, duplicate return page | Correct language and truthful per-payment message without requiring hub admission |
| Unexpected activation exception after provider capture | Existing atomic boundary retained; reconciliation can identify local pending/provider-paid mismatch |
| Historical paid rows and manual/beta grants | No automatic refund/grant or invented source attribution from status alone |
| Every purchase entry point while recovery is open | No second charge through fresh pending membership or coach-change workaround |

Use synchronized PostgreSQL connections for race/lock cases and preserve the
existing payment/profile merge ordering tests. No new billing period, fee, capture
mechanism, event refund policy or broad browser-CI work belongs in this change.

## Delivery sequence

1. Approve D1–D5 and name the operating owner; obtain a separate read-only live
   inventory/alert verification when authorized. Decide D6 as a follow-up scope.
2. Implement provenance, typed cases and source-aware refund handling with
   migration/backfill review. No automatic historical financial repair.
3. Implement permissioned resolution, truthful notices/return display, bounded
   delivery and proven staff escalation. Keep fulfillment and delivery independent.
4. Complete acceptance tests including PostgreSQL races, then obtain release
   approval. Production rollout and each monetary resolution remain explicit actions.
