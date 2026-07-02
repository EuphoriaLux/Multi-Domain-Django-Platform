# Crush Connect: Asymmetric Catalogue Transition (LuxID-gated)

**Date:** 2026-06-09
**Status:** Phase 1 implemented
**See also:** [Crush Connect — Product Overview](../../products/crush-connect.md) (living product inventory, taxonomy, and copy rules)

Crush.lu is transitioning from purely event-based dating to a **digital
experience** where a Crush Coach curates matches for Premium Members. This
document records the product decision, what changed in Phase 1, and the
roadmap for the remaining phases.

## 1. Product decision

1. **Asymmetric catalogue model.** Premium Members (personal coach assigned +
   onboarded into Crush Connect) RECEIVE coach-curated Drops. The CANDIDATE
   CATALOGUE the Drop algorithm / coaches pick from is open to ALL members who
   are (a) LuxID-verified and (b) opted in (onboarded `CrushConnectMembership`
   with a Story) — Premium is no longer required on the candidate side.
2. **LuxID is the ticket into the catalogue.** Government-eID identity is what
   keeps the catalogue "real people only" and is the trust promise made to
   Premium Members.
3. **No grandfathering.** Members verified via `coach_event` / `admin` /
   `legacy` stay verified for events and post-event connections, but are NOT
   selectable in the catalogue until they link LuxID. They see "Connect LuxID
   to become discoverable" prompts instead.
4. **Scope.** LuxID is mandatory ONLY for the Crush Connect catalogue.
   Coach-at-event verification remains a fully valid path for events,
   visibility, and post-event connections.

## 2. Current state (before this change, as of M4.5)

- `get_eligible_pool()` required Premium (`assigned_coach`) on BOTH sides.
- Any `verification_status='verified'` member qualified regardless of method —
  LuxID was never checked for the pool.
- `verification_method` records only the FIRST verification path; a
  coach-verified member who later linked LuxID kept `method='coach_event'`
  with no profile-level record of the LuxID link.

## 3. Target state (Phase 1, implemented)

### Two tracks

| Track | Requirement | Gets |
|---|---|---|
| **Receiver** (Premium) | verified + `assigned_coach` + onboarded | Today's Drop at `/crush-connect/today/` |
| **Candidate** (free) | verified + LuxID linked + onboarded | Catalogue entry; status page at `/crush-connect/catalogue/` |

A Premium member with LuxID is both. A Premium member WITHOUT LuxID still
receives Drops but no longer appears in other members' Drops.

### What changed in code

- `CrushProfile.has_luxid_connected` property (`crush_lu/models/profiles.py`)
  — authoritative LuxID check via `SocialAccount(provider in ['luxid',
  'openid_connect'])`. No new DB field; no migration.
- `get_eligible_pool()` (`crush_lu/services/crush_connect.py`) — candidate
  side: dropped `assigned_coach` requirement, added LuxID `Exists` filter.
  Requester side (Premium receive-gate) unchanged.
- Two-track gating in `crush_lu/views_crush_connect.py`
  (`_user_is_connect_receiver_eligible`, `_user_is_connect_candidate_eligible`);
  onboarding redirect branches by track; new `crush_connect_catalogue_status`
  view + `catalogue_status.html` template.
- Teaser fast-path (`crush_lu/views_static.py`) routes both tracks; teaser
  template gained a LuxID status row + "Enter the catalogue for free" card.
- Signal `auto_approve_profile_on_luxid_connect` (`crush_lu/signals.py`) —
  already-verified members linking LuxID keep status AND method, get a
  success message confirming catalogue eligibility.
- "Connect LuxID" prompts: dashboard banner (verified non-Premium without
  LuxID), `_verification_journey.html` secondary callout,
  `_verification_options.html` copy.
- New email `crush_connect_catalogue_welcome.html` sent when a candidate-track
  member finishes onboarding (they receive no Drops, so this is how they know
  they're discoverable).
- Model cleanups: removed duplicate `VERIFICATION_STATUS_CHOICES` block,
  fixed stale "waiting for LuxId" comment.

### What stays the same

- Event registration gates (`profile_requirement`) and coach-at-event
  verification.
- Post-event connections, sparks, messaging.
- Premium receive-gate and coach exclusion (panic button) mechanics.
- The Connect onboarding form (prompt + story + consent).

## 4. Phased roadmap

### Phase 1 — this change ✅
See "What changed in code" above.

### Phase 2 — content & pricing (pending product decisions)
- [ ] Retire / evolve the "4 weeks / 4 matches — 20 testers" beta framing in
      `crush_connect.html` once the beta concludes.
- [ ] `how_it_works.html`: add the digital experience as an explicit step
      (currently events-only narrative).
- [ ] `membership.html`: present the two tracks (free LuxID catalogue entry
      vs Premium receiving) alongside the points tiers.
- [ ] DE/FR translations for all new `{% trans %}` strings (compile .po).
- [ ] Comms to existing Premium members WITHOUT LuxID (they silently fell out
      of the candidate pool — decide on an email nudge or grace messaging).

### M5 — Curiosity Sparks ✅ (implemented, asymmetric-aware)
- `CuriositySpark` model (sender, recipient, drop audit FK, message, status;
  one Spark per pair, no self-sparks). Migration 0158.
- Send: Premium receivers only, and only to people who actually appeared in
  one of their Drop snapshots (`can_send_spark` / `send_spark` in
  `services/crush_connect.py`). Drop card CTA replaces the "coming soon"
  placeholder; sent state shown on the card.
- Respond: `/crush-connect/sparks/` works for BOTH tracks — candidates never
  receive Drops, so the bell notification + email is how they learn someone
  is curious. Accept → sender notified (in-app + email) and the pair lands
  in the admin accepted-sparks queue for the coach to arrange the date.
  Decline → silent by design; the sender is never told.
- Emails: `crush_connect_spark_received.html`, `crush_connect_spark_accepted.html`.
- Admin: `CuriositySparkAdmin` (status filter = the coach's interim M7 queue).

### M7 — Coach Picks ✅ (implemented)
- `ConnectCoachPick` model (migration 0159): coach proposes ONE candidate
  per member with a personal note; new proposal withdraws the previous one.
- Coach curation hub at `/coach/connect/` (+ `/coach/connect/member/<id>/`):
  coaches browse the member's eligible pool with FULL profiles and pick.
- The pick REPLACES the algorithmic Drop as the hero card on `/today/`,
  with the coach's note and accept/decline buttons.
- Accept → coach is notified and contacts the candidate personally to
  arrange the date (no automatic Spark to the candidate). Decline → coach
  notified, picks again. Stale candidates hide the pick automatically.

### Phase 3+ — deferred features
- [ ] Mutual-interest reveal flow (M6) — unblur, name reveal chapters.
- [ ] Spark quotas / expiry (e.g. auto-expire pending Sparks after 14 days).
- [ ] Coach admin view: catalogue size, members without LuxID, exclusions.
- [ ] Decay/activity cron + push notifications (M8).

## 5. Open questions

1. **Pricing:** is catalogue entry free forever, or part of a future tier?
   (Currently free; the teaser still advertises €10/month for receiving.)
2. **Premium-without-LuxID:** do we eventually require LuxID for receivers
   too, so every participant is eID-verified?
3. **Catalogue exit UX:** "leave the catalogue anytime from settings" is
   promised in copy — today that means coach exclusion or support; a
   self-serve opt-out toggle should land in Phase 2.
4. **Coach-event verified members:** how aggressively do we nudge them to
   link LuxID (one email? recurring? badge on profile)?

## 6. Verification

- `crush_lu/tests/test_crush_connect.py` — pool asymmetry, two-track view
  routing, catalogue status page (all passing).
- `crush_lu/tests/test_luxid_auto_approve.py` — already-verified + LuxID
  connect keeps method, adds message (passing).
- Manual: `python manage.py simulate_luxid_verify` (dev) to walk a
  coach-verified user through LuxID linking → onboarding → catalogue page.
