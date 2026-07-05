# crush.lu Robustness Review — Findings Report

**Date:** 2026-07-05
**Scope:** deep read of `crush_lu` (forms, data integrity, queries, security, Crush Connect logic, Quiz Night real-time) plus the security-adjacent items called out for review (CSP `unsafe-inline`, the accepted login-CSRF risk, WhatsApp send reliability).
**Nature:** read-only analysis pass. No code was changed. The full test suite is green (1507 passed / 27 skipped), so none of these fail existing tests — they are gaps the tests do not cover.

## Context: what is already solid

The crush.lu core is well-built. The member-facing IDOR surface (connections, messages, notifications, profiles, drafts, moderation, sparks) is correctly scoped to `request.user`; admin/webhook endpoints use `secrets.compare_digest`; the WhatsApp webhook HMAC, OTP flow, SPA→JWT bridge, private-photo SAS gating, and rate-limiting are all sound; and the **newer** Crush Connect models show real concurrency discipline (unique + check constraints, `select_for_update`, savepoints).

The problems cluster in three places:
1. **Older code** written before that discipline (referrals, `EventConnection`).
2. The **profile wizard's two parallel write paths** (form vs AJAX).
3. **Crush Connect's eligibility logic**, expressed 3–4 times in incompatible shapes.

Counts: **1 critical (adjacent app), 5 high, ~20 medium, ~10 low.** Items marked **[verified]** were read and confirmed directly.

---

## 🔴 CRITICAL

### C1 — Unauthenticated exposure of Azure billing data *(adjacent app: `power_up/finops`, not `crush_lu` — but same Azure deployment)* — ✅ FIXED (merged in #572)
Several FinOps Hub endpoints **served** the operator's internal Azure billing data (subscription/service names, resource IDs, per-service spend, and a CSV export) without an effective authentication check: the dashboard views (`power_up/finops/views.py`) carried no auth decorator, and the DRF cost APIs used a **fail-open** permission class (`power_up/finops/permissions.py`) that admitted ordinary browser requests. Because all sites share one `User` table, the exposure was not limited to staff.
**Remediated in #572 (merged to `main`):** `@staff_member_required` on every dashboard view; `IsAdminOrStaff` + `SessionAuthentication` on every ViewSet/API endpoint; the fail-open permission classes deleted; permission tests assert the lockdown. Deployed to **staging** via the `main` CD — **production closure still requires the slot swap.**
*Detailed reproduction steps are intentionally kept out of this document — see PR #572 and the private incident notes. Outside the stated crush.lu scope, but the single most severe issue found on the instance, so it leads.*

---

## 🟠 HIGH

### H1 — Profile photo upload skips all server-side content validation on the primary path [verified pattern]
`save_profile_step3` (`crush_lu/views_profile.py:523-534`) assigns `profile.photo_1 = request.FILES[...]` directly — running **neither** of the app's two photo validators. The AJAX draft endpoint `upload_photo_draft` calls `process_uploaded_image` (size cap + EXIF strip + resize), and the form path runs `CrushProfileForm._validate_photo` (`crush_lu/forms.py:416-517`: MIME sniff, extension allowlist, `Image.verify()`, dimension/bomb guard) — but `save_profile_step3` (the JS-off/backward-compat path) invokes no validation at all. So a 200 MB TIFF or a decompression-bomb PNG is stored and later decoded by thumbnailing/coach review → memory-exhaustion DoS.
**Fix:** extract `_validate_photo`'s content checks out of the form into a shared validator, and apply it **plus** `process_uploaded_image` on every raw-file write — `save_profile_step3` and the non-AJAX `create_profile`/`edit_profile` path. (Applying only `process_uploaded_image`, as the draft endpoint does, still omits the MIME/extension/dimension checks.)

### H2 — Referral reward double-award race (no row lock on the guard)
`apply_referral_reward` (`crush_lu/referrals.py:169-218`) reads `attribution.reward_applied` **outside** any lock, then credits points with `F()`. The wrapper `check_and_apply_profile_approved_reward` (which calls it) is invoked from **coach approval** (`crush_lu/views_coach.py:1128`), **event check-in** (`crush_lu/views_checkin.py:347`), and the **login/auto-approve signals** (`crush_lu/signals.py:1021, 2154`) — so two of those can pass the guard concurrently for one attribution → referrer credited twice for one signup (points map to €-discounts, so this is financial).
**Fix:** re-fetch the attribution with `select_for_update()` *inside* the atomic block and re-check under the lock; add a dedicated `approval_bonus_applied` flag instead of inferring from a summed counter.

### H3 — Quiz WebSocket: any authenticated user can subscribe to any quiz [verified]
`crush_lu/consumers.py:160-182` — for an authenticated user, `connect()` does zero authorization against the quiz/event; it just `group_add(quiz_{id})` and `accept()`s. `quiz_id` is a sequential int. An attacker joins any event's live feed (roster, standings). Answer data *is* stripped for non-hosts (`:208`), which contains the blast radius, but subscription is not scoped to participation.
**Fix:** in `connect()`, verify the user is a host or has a confirmed/attended `EventRegistration` for `quiz.event_id`; else `close()` before `group_add`.

### H4 — Every authenticated page pays a ~12–15-query context-processor tax [verified]
`crush_user_context` (`crush_lu/context_processors.py:44-268`) runs a dozen-plus queries on every authenticated render, including `blocked_user_ids(request.user)` **called twice** (`:85` and `:140`, no memoization) and two separate `EventConnection.count()` badge queries (`:91`, `:103`). This is the biggest fixed per-request DB cost site-wide, multiplied across all logged-in traffic.
**Fix:** memoize `blocked_user_ids` on the request; merge the two badge counts into one `aggregate(Count(..., filter=Q(...)))`; consider a 30–60 s Redis cache for navbar badges.

### H5 — Public SEO event list fires ~3–4 COUNT queries per event [verified]
`event_list` (`crush_lu/views_events.py:160`) fetches with `.prefetch_related("coaches__user")` but **not** `.with_registration_counts()`, so `event.is_full`/`spots_remaining` (view JSON-LD loop + `event_card.html`) each re-run `get_confirmed_count()`. 30 events → ~90–120 extra COUNTs on the most SEO-critical, unauthenticated page.
**Fix:** swap in the existing `.with_registration_counts()` manager method (0 per-event queries).

---

## 🟡 MEDIUM

### Validation / flow
- **M1 — Final profile submit validates *presence*, not *validity*.** `complete_profile_submission` gates on `get_missing_fields()` (presence-only), never running `CrushProfileForm.clean_*`. Invalid values POSTed straight to `save_profile_step1/2/3` (e.g. bogus `event_languages` codes, age not re-checked) enter the match pool. → *See Redesign R1.*
- **M2 — Connect 7-step wizard finalizes on the step pointer, validating only step 7** (`crush_lu/views_crush_connect.py:346-369`). Steps 1–6 are not re-asserted, so a partially-populated membership can enter the match pool. **Note:** this is the *Crush Connect* onboarding wizard, distinct from the *profile* wizard R1 addresses — it needs its own finalize-time re-validation of steps 1–6; collapsing the profile write paths (R1) does **not** fix it.
- **M3 — Phone uniqueness normalizes differently per channel** (`crush_lu/views_phone_verification.py`). The Firebase path (`mark_phone_verified`) does an exact-string dedup while WhatsApp canonicalizes; `00352…` vs `+352…` lets **two accounts verify the same physical number**. Fix: canonicalize to one E.164 form at every write site.
- **M4 — Age-preference range has no server-side clamp** [verified] (`crush_lu/forms_crush_connect.py:230`). `18..99` is only a widget attr; a crafted POST stores `preferred_age_min=1`. Bounded (all profiles are 18+, so no minors can surface) but a real validation gap with bad optics. Fix: `min_value=18, max_value=99` on the fields.
- **M5 — Journey gift upload accepts file if extension *OR* content-type matches (should be AND) and trusts client `content_type`** (`crush_lu/forms.py:1239,1259`). A `payload.mp4` containing HTML/SVG passes and is served from the journey page. Fix: require sniffed MIME **and** extension, like `_validate_photo`.

### Concurrency / integrity
- **M6 — `EventConnection` mutual-match race** (`crush_lu/views_connections.py:283-319`). Simultaneous A→B / B→A can both read "no reverse" and leave two `pending` rows — mutual interest, but nobody accepted, no coach, no notification (silently broken match). Fix: serialize on a per-pair lock before the reverse lookup.
- **M7 — `propose_coach_pick` withdraw-then-create is not atomic** (`crush_lu/services/crush_connect.py:891-917`; corroborated by two reviewers). Race can leave two `proposed` picks, or (on create-failure) zero. The unique constraint on `(member, candidate)` helps but does not enforce "≤1 open proposal." Fix: atomic + `select_for_update`, or a partial unique index on `status='proposed'`.
- **M8 — Quiz "all tables scored" detection runs outside the lock** (`crush_lu/consumers.py:1418`, `crush_lu/api_quiz.py:423`). Concurrent last-table scores → duplicate reveal/leaderboard broadcasts, plus a window to re-score an already-revealed question. Fix: compute the count inside a `select_for_update` on the quiz row.
- **M9 — `set_quiz_status` is a non-atomic read-check-write** (`crush_lu/consumers.py:1094`). A pause racing an auto-finish can leave the quiz in the wrong terminal state mid-event. Fix: lock the row before the transition check (as `advance_question` already does).
- **M10 — 2-table rotation can seat one player twice in a round** (`crush_lu/services/quiz_rotation.py:129-146`); the `unique_together(quiz, round, user)` then throws `IntegrityError` and aborts schedule generation for certain participant counts — 2-table events are fragile. Fix: partition seats without index collisions + dedupe assertion before `bulk_create`.

### Security-adjacent (the named items + neighbors)
- **M11 — CSP is report-only, and `unsafe-inline` is in `script-src`** [verified] (`azureproject/settings.py:1038,1044`; prod confirmed report-only at `azureproject/production.py:600`). In report-only mode nothing is blocked, so there is **no CSP enforcement today**. Fix order: migrate inline HTMX/Alpine handlers to nonces → remove `unsafe-inline` from `script-src` → flip `SECURE_CSP_REPORT_ONLY` to `SECURE_CSP`. Note: switching to **enforce** is worthwhile even *before* `unsafe-inline` is removed — it activates the policy's other protections (`object-src 'none'`, `frame-ancestors 'self'`, `base-uri 'self'`, and the `default-src`/`connect-src` allowlists). What `unsafe-inline` leaves open is specifically **inline-script XSS**; removing it (via nonces) is what closes that gap.
- **M12 — Google Wallet callback signature check is a no-op stub** (`crush_lu/wallet/google_callback.py:236`). `@csrf_exempt` endpoint whose "verification" just parses JSON; a POST can clear a victim's `google_wallet_object_id` (bounded only by a random object-id suffix). Fix: verify the signed JWT against Google's keys.
- **M13 — Any active coach can read any member's dossier/matches** (`crush_lu/views_coach.py:2734,2869`). `coach_member_overview`/`_matches` do `get_object_or_404(User, id=...)` with no assigned-coach scoping, unlike sibling views. May be an intended cross-coach admin tool (there is a reassignment dropdown) — **confirm the coach trust model**; if per-coach isolation is intended, add `assigned_coach=request.coach`.
- **M14 — `respond_connection` changes state on GET** [verified] (`crush_lu/views_connections.py:605-734`). Accept/decline runs for both GET and POST; a lured top-level link force-accepts the victim's own pending request (SameSite=Lax blocks the `<img>` vector but not navigation). Scoped to `recipient=request.user`, so bounded, not IDOR. Fix: make it POST-only.
- **M15 — WhatsApp send has no retry/backoff and no dedup — across two distinct send paths.** (a) **Phone-verification OTP** goes through `crush_lu/services/whatsapp.py::send_otp` (called from `views_phone_verification.py`); a transient Meta 5xx there leaves the user unable to complete verification. (b) The **admin/CRM template sender** `hub/views_whatsapp.py` marks a message FAILED permanently on a transient error, and rate-limiting exists only on OTP send, not this general path. Fix: bounded retry with backoff on 5xx + an idempotency key on **both** paths; the verification-impact fix specifically belongs to `send_otp`.

### Crush Connect logic + perf
- **M16 — Gate-question-less members are not filtered from the candidate pool** (`crush_lu/services/crush_connect.py:326`). `get_eligible_pool` requires photo consent but not the 3 gate questions, yet every *interaction* path requires them → un-answerable "dead" Drop cards and coach picks that cannot consummate the M9 product. → *See Redesign R2.*
- **M17 — Inactivity uses `last_login`** (`crush_lu/services/crush_connect.py:134,483`). A daily-active user on a persistent session never re-logs-in, so `last_login` goes stale and they silently drop out of every pool after 30 days. Fix: a throttled `last_active_at` bumped on any Connect surface.
- **M18 — Paid testers get a monetized dead-end** (`crush_lu/models/crush_connect.py:45-52`). `selected_as_tester`/`payment_confirmed` are surfaced to the teaser but grant no access — a confirmed €10/mo tester is bounced back to the teaser on every Connect surface. → *See Redesign R2 (tier enum).*
- **M19 — `coach_event_list` per-event registration N+1 + unbounded list** (`crush_lu/views_coach.py:1907,1949`). 40 upcoming events → ~50 sequential queries; no slice. Fix: `Prefetch` registrations once + cap the list.
- **M20 — In-memory channel-layer fallback is dev-only (corrected — not a production risk)** (`azureproject/settings.py:255`). `production.py:341` overrides `CHANNEL_LAYERS` unconditionally to `RedisChannelLayer` (hosts = `AZURE_REDIS_CONNECTIONSTRING` or `redis://localhost:6379/0`), so **production never falls back to in-memory** — a missing Redis var fails loudly against localhost, it does not silently split rooms across workers. The in-memory layer only applies to the WSGI/dev path. Residual (low): the `redis://localhost` default masks an unset `AZURE_REDIS_CONNECTIONSTRING` as a runtime connection error rather than a config error at boot.

---

## 🟢 LOW (fix opportunistically)
- **L1** — Two different "today" definitions: Drop uses a 06:00 cutoff, question-week + countdown do not (`crush_lu/services/crush_connect.py:314` vs `:405`) → week-boundary scoring seam. → *Redesign R3.*
- **L2** — Empty Drop is cached as terminal for the whole day; a member who becomes eligible at noon still sees "no candidates" until 06:00 (`crush_lu/services/crush_connect.py:329`). Bad for a low-liquidity market.
- **L3** — `bio`/`interests` stored uncapped on the AJAX path (`TextField.max_length` is not a DB constraint; `save()` skips `full_clean()`) (`crush_lu/views_profile.py:262,559`).
- **L4** — Non-constant-time secret compares: PassKit token (`crush_lu/wallet/passkit_service.py:82`), quiz PIN (`crush_lu/views_quiz.py:331`, but rate-limited). Use `secrets.compare_digest`.
- **L5** — Quiz WS display-token path has no rate limit (`crush_lu/consumers.py:99`), unlike the HTTP PIN endpoint → 4-digit PIN brute-forceable onto the projector feed.
- **L6** — `submit_challenge` challenge lookup unscoped to the user's journey (`crush_lu/api_journey.py:48`); self-only impact (point inflation).
- **L7** — `crush_connect_enabled` decorator exists but is unused; 8 inline flag copies instead, and `crush_connect_spark_respond` has no flag check at all (`crush_lu/decorators.py:31`, `crush_lu/views_crush_connect.py:956`). Drift risk. → *Redesign R2.*
- **L8** — Stale debug instrumentation: `[PRE-CSRF-DEBUG]` logging on POST `/login/` (`azureproject/middleware.py:90`) and full-PII `logger.debug` of phone/DOB/bio (`crush_lu/views_profile_draft.py:190`).
- **L9** — Admin `list_display` per-row N+1s (`crush_lu/admin/profiles.py:1539` missing `assigned_coach__user`, etc.) — staff-only, ~200 queries/changelist. One-line `select_related` fixes.
- **L10** — Dead code: the `CrushSpark(event,sender,recipient)` double-submit race is **not reachable** — `spark_send_inline`'s URL now redirects to the Connect teaser (`crush_lu/urls.py:416`). Latent only; add the missing `UniqueConstraint` if that view is ever resurrected.

---

## 🔧 Redesign proposals (where the logic is fundamentally fragile)

**R1 — Collapse the profile wizard's two write paths into one validator.** *Root cause of H1, M1, L3.* The wizard has a `CrushProfileForm` (JS-off path) and hand-rolled AJAX endpoints (`save_profile_step1/2/3` + `complete_profile_submission`) that re-implement a *subset* of the form's validation and finalize on presence-only checks. Any validator added to the form (age bounds, photo bombs, choice/length checks) silently does not protect the primary AJAX path. **Better:** have each step endpoint validate *through* the corresponding form fields (or a shared `validate_step(n, data)` service), and make finalize run the full-model validation before submit. One source of truth; the paths cannot diverge.

**R2 — One eligibility predicate + one queryset annotation for Crush Connect.** *Root cause of M16, M18, M7, L7.* "Surfaceable" is currently defined 3–4 times in incompatible shapes: ORM filters (`get_eligible_pool`), per-object predicates (`is_catalogue_eligible`, `is_sender_eligible`, `_user_is_connect_receiver_eligible`, …), and render-time re-filters. New prerequisites get threaded through some and forgotten in others (photo-consent everywhere, gate-questions nowhere in the pool). **Better:** a single `catalogue_ready(user) -> (bool, reason)` + a matching queryset annotation, both generated from one shared `(field, required_value)` constant tuple. Add an explicit `tier ∈ {candidate, premium, tester}` field so the beta's paid testers get real access instead of a dead-end.

**R3 — Model the read-the-photo gate as a two-cell directional state + version the answers.** Today a single `CuriositySpark.status` is overloaded to encode a two-directional read, forcing the two directions through different branches with different eligibility rules, and guesses are locked by `(responder, owner, question)` so a viewer who missed can never re-read a *changed* gate. **Better:** a `GateRead(reader, owner, aligned)` record with `matched` derived from both cells, and each guess tied to the owner's gate *version/snapshot* so re-picks cleanly retire stale reads.

**R4 — Bring the older concurrency hot spots up to the newer models' standard.** Lock the referral attribution row (H2) and the `EventConnection` pair (M6) the same way `PhoneOTP`, `CuriositySpark`, and event registration already do (`select_for_update` + atomic, plus DB constraints where the invariant is expressible). Optionally a single `ConnectClock` helper to kill the three hand-rolled date/week/drop computations (L1).

---

## Suggested order
1. **C1** (finops lockdown) — ~1 hr, stops active data exposure.
2. **H1 + H2** — photo-bomb DoS and the referral double-award (both concrete, both have clean local fixes).
3. **H4 + H5** — the two query fixes are cheap and pay off across all traffic.
4. **H3 + M8/M9** — quiz auth + scoring races before the next live event.
5. **M11 nonce migration** — unblocks turning CSP on.
6. Then the Crush Connect redesigns (R1/R2) as part of the launch work.
