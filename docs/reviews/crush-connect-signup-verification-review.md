# Crush.lu — Sign-Up & Verification Review (for Crush Connect)

> **Scope.** This review looks at the **sign-up process** and **user verification** on
> `crush.lu`, then assesses what they imply for **Crush Connect** — the planned online-first
> experience where members opt in and discover others. It is a read-only assessment; no
> application code was changed. Findings follow the same shape as `crush-lu-review-issues.md`
> (Priority, Category, Files, Description, Recommendation) so they convert cleanly to issues.
>
> Reviewed on the `claude/crush-lu-signup-verification-uvqf43` branch.

---

## Executive summary

Crush.lu today is **event-first**: people sign up, verify a phone number, build a profile,
and get *verified in person by a coach at an event* (or self-serve via LuxID). The
foundations are strong — mandatory email verification, real phone verification (Firebase SMS
/ WhatsApp OTP / LuxID), GDPR consent capture, and a coach-reviewed trust model.

Crush Connect (the **online-first** pivot) is already substantially scaffolded in code as a
milestone rollout (M1–M7): the matching engine, daily "Drop", Curiosity Sparks, and
coach-picks exist. The gaps are **not** in the matching engine — they are in **access
friction**, **safety**, and **interaction completeness**:

- The single most important gap for an online platform is **there is no way for a member to
  report or block another member** (confirmed absent). That is table stakes — and likely a
  legal / app-store requirement — before exposing members to each other online.
- **Discovery is gated behind Premium** (a paid coach) and the candidate pool requires
  **LuxID**. A free, opted-in user is *passive-only* today. Whether "opt in and discover
  others" is reachable for a free user is a **product decision that defines the whole online
  experience**.
- **Identity trust leans on manual coach review**, which is high-touch and does not scale to
  an online-first funnel; **age and photo authenticity are not hard-verified** for non-LuxID
  users.

Priorities asserted at the end: **P0/P1** = reporting + blocking, age/identity strategy, the
free-user discovery decision; **P1/P2** = a few auth-hardening items; **P2/P3** = interaction
completeness (messaging, mutual reveal, photo verification at scale).

---

## Current state (verified)

**Sign-up.** django-allauth, email-only login, `ACCOUNT_EMAIL_VERIFICATION = "mandatory"`
(`azureproject/settings.py:449`). The dedicated `/signup/` view (`crush_lu/views_account.py`,
`signup()`) uses `CrushSignupForm` (`crush_lu/forms.py:20`), which captures Terms + marketing
consent into `UserDataConsent` (`forms.py:108-112`).

**Onboarding journey** (`crush_lu/onboarding.py`) — 5 steps, derived from profile state:

| Step | Key | Gate |
|------|-----|------|
| 1 | Welcome | `welcome_seen_at` set + intent probe |
| 2 | Verify number | `phone_verified` |
| 3 | Meet the Coaches | `coach_intro_seen_at` set |
| 4 | Build profile | `verification_status == 'incomplete'` |
| 5 | Get verified | submitted → `pending`/`verified` (event or LuxID) |

**Verification is two-layered:**
- *Phone* — Firebase SMS, WhatsApp OTP (`crush_lu/models/phone_otp.py`, hashed single-use,
  rate-limited), and LuxID. Phone is extracted from the verified token, not the request body
  (`crush_lu/views_phone_verification.py`).
- *Identity / trust* — `verification_status` (`incomplete → pending → verified → rejected`)
  with `verification_method` (`luxid` / `coach_event` / `premium_coach` / `admin` / `legacy`)
  — `crush_lu/models/profiles.py:420-435`.

**Crush Connect is opt-in and asymmetric** (`crush_lu/views_crush_connect.py`,
`crush_lu/services/crush_connect.py`), behind `CRUSH_CONNECT_LAUNCHED` (default `False`,
`settings.py:400`):

- **Receiver track** — gets a daily Drop and can send Sparks → verified profile **+ Premium
  coach assigned** (`_user_is_connect_receiver_eligible`, `views_crush_connect.py:62`).
- **Candidate track** — appears in others' Drops, can receive/respond to Sparks → verified
  profile **+ LuxID linked + completed 7-step Connect onboarding**
  (`_user_is_connect_candidate_eligible`, `views_crush_connect.py:74`; `get_eligible_pool`,
  `services/crush_connect.py:71`).

---

## Part 1 — Sign-up process

### S1. Two sign-up entry points capture consent differently
**Priority:** P1-High **Category:** `security`, `gdpr`, `architecture`
**Files:** `azureproject/settings.py:555-556`, `azureproject/urls_shared.py:44`,
`crush_lu/views_account.py` (`signup()`), `crush_lu/forms.py:20` (`CrushSignupForm`),
`azureproject/adapters.py`

The global setting is `ACCOUNT_FORMS = {"signup": "entreprinder.forms.CustomSignupForm"}`,
with a comment that it "will be overridden by adapters for other domains." **No adapter
actually overrides `get_signup_form_class`** — verified by grep. Meanwhile allauth's URLs are
mounted on the crush domain through `base_patterns` (`urls_shared.py:44`,
`path('accounts/', include('allauth.urls'))`), and `urls_crush.py` only redirects the exact
paths `/accounts/` and `/accounts/email/` — **not** `/accounts/signup/`.

Net effect: on `crush.lu`, `/accounts/signup/` resolves to allauth's `SignupView` using
**Entreprinder's** `CustomSignupForm`, which does **not** capture Crush.lu's `crushlu_consent`
/ `marketing_consent`. Only the dedicated `/signup/` view (using `CrushSignupForm`) records
consent into `UserDataConsent`.

This is mitigated — not eliminated — by `CrushConsentMiddleware`, which bounces consent-less
users to `/consent/confirm/` after the fact. But two entry points with divergent forms is a
GDPR-audit and UX smell.

**Recommendation:** Implement a per-domain `get_signup_form_class()` on the account adapter
(making the settings comment true) so `/accounts/signup/` uses `CrushSignupForm` on crush, or
redirect `/accounts/signup/` → `/signup/` in `urls_crush.py` alongside the existing
`/accounts/` redirects. Either way, one canonical consent-capturing signup path.

### S2. `SOCIALACCOUNT_LOGIN_ON_GET = True` (login CSRF)
**Priority:** P2-Medium **Category:** `security`
**Files:** `azureproject/settings.py:321-326`

Still `True`. The in-code comment acknowledges it is a temporary state pending templates that
POST to the provider login endpoints. As django-allauth's docs warn, GET-initiated OAuth lets
an attacker force-login a victim into the attacker's social account (login CSRF) — more
sensitive on a dating product. *Note: the duplicate definition flagged as Issue 7 in
`crush-lu-review-issues.md` has been resolved — there is now a single definition.*

**Recommendation:** Finish the template migration to POST-based provider login and set
`SOCIALACCOUNT_LOGIN_ON_GET = False`. Track the remaining templates explicitly so the flag
doesn't stay "temporarily" True indefinitely.

### S3. Social auto-linking by email — review the trust boundary
**Priority:** P2-Medium **Category:** `security`
**Files:** `azureproject/settings.py:468-556` (`SOCIALACCOUNT_EMAIL_VERIFICATION = "none"`,
`SOCIALACCOUNT_EMAIL_AUTHENTICATION(_AUTO_CONNECT)`, `SOCIALACCOUNT_EMAIL_VERIFIED_PROVIDERS`)

Social signups skip email verification (`"none"`) and auto-connect to existing accounts by
matching email. Safety rests on `SOCIALACCOUNT_EMAIL_VERIFIED_PROVIDERS`
(`google, facebook, microsoft, apple, luxid`). This is reasonable for those issuers, but the
auto-connect-by-email behavior is exactly the surface where a provider that returns an
unverified/attacker-controlled email could lead to account linking. Worth an explicit
reflection + a test that asserts only verified-provider emails auto-connect.

**Recommendation:** Keep the allow-list tight; add a regression test asserting a non-listed /
unverified provider email cannot auto-connect to an existing Crush account. Document the
decision next to the setting.

### S4. Carry-over: `@csrf_exempt` on authenticated endpoints
**Priority:** P1-High (carry-over) **Category:** `security`
**Files:** `crush_lu/api_push.py`, `crush_lu/views_account.py`, `crush_lu/api_admin_*.py`,
others (16+ files still use `@csrf_exempt`)

`crush-lu-review-issues.md` Issue 2 remains broadly applicable — `@login_required` does not
defend against CSRF. Several still-exempt endpoints touch the account/auth surface (e.g. email
preference, push subscription). Re-triage against that issue; an online platform widens the
blast radius of any forged state-changing request.

**Recommendation:** As in the prior review — drop `@csrf_exempt` and send the CSRF token from
`fetch()`/HTMX, or use DRF `SessionAuthentication`.

---

## Part 2 — User verification

### V1. Age is self-declared for everyone except LuxID users
**Priority:** P1-High **Category:** `safety`, `compliance`
**Files:** `crush_lu/forms.py:133-144` (`date_of_birth`, "Must be 18+"), `:281` (`clean_date_of_birth`),
`crush_lu/models/profiles.py` (`get_age`)

18+ is enforced only by form validation on a **self-entered** date of birth. The sole path to
a *verified* age is LuxID (government OIDC), which is **optional**. Coach/event verification
confirms a person exists but does not cryptographically attest age. For an online product that
exposes members to one another (and must keep minors out), self-declared age is a real risk.

**Recommendation:** Decide a policy for the online surface: either (a) require LuxID — and thus
verified age — to *appear in or browse* Crush Connect, or (b) add an alternative age/ID
verification step. Make the requirement explicit at the Connect gate rather than relying on
DOB form validation.

### V2. No photo / liveness verification (catfishing surface)
**Priority:** P1-High **Category:** `safety`, `trust`
**Files:** `crush_lu/views_profile.py` (photo upload + validation), `crush_lu/forms.py:445-450`
(`python-magic` MIME check), coach screening checklist (`ProfileSubmission`)

Uploads are validated for size/format/MIME and EXIF-stripped, but there is **no check that the
photo is of the actual account holder** — authenticity depends on a coach eyeballing it during
review. That is fine at event scale and breaks down online, where photo authenticity is the
core trust signal and the main catfishing vector.

**Recommendation:** Plan a selfie/liveness or "verified photo" badge for Connect members (even
a lightweight pose-match), and surface the badge on the Drop card so members can weight it.

### V3. Manual coach review as the trust backbone — scale reflection
**Priority:** P2-Medium **Category:** `scalability`, `product`
**Files:** `crush_lu/models/profiles.py:966+` (`ProfileSubmission`: 48h SLA, screening call,
revision rounds), `crush_lu/admin/profiles.py`

The trust model is high-touch: a coach completes a screening call before approval
(`review_call_completed`), with SLA, revision, and recontact workflows. Excellent for quality
and the event business; a bottleneck for an online funnel that wants volume. Note the existing
self-serve lever already in code — **LuxID direct verification** sets
`verification_status='verified'`, `method='luxid'` *without* a `ProfileSubmission`
(`crush_lu/signals.py`). The strategic question is how much of the online funnel should ride on
LuxID self-serve vs. coach review.

**Recommendation:** Define which Connect actions require coach-grade verification vs. accept
LuxID self-serve, so coach capacity scales sub-linearly with online sign-ups.

---

## Part 3 — Crush Connect online-readiness

### C1. Eligibility friction is the strategic centerpiece
**Priority:** P0-Critical (product decision) **Category:** `product`, `access`
**Files:** `crush_lu/views_crush_connect.py:62-101` (the three gate helpers),
`crush_lu/services/crush_connect.py:71-127` (`get_eligible_pool`),
`crush_lu/models/crush_connect.py` (`CrushConnectMembership`)

Two facts compound:

1. **Event-verified members cannot enter the candidate pool without separately linking LuxID.**
   `coach_event`-verified users are "verified" for events/connections but
   `_user_is_connect_candidate_eligible` additionally requires `has_luxid_connected`, and
   `get_eligible_pool` filters the pool to LuxID-linked members. So today's verified event
   members are **invisible online** until they link LuxID.
2. **Discovery itself (receiving a Drop, sending a Spark) requires Premium** — a paid,
   assigned coach (`_user_is_connect_receiver_eligible`). A free opted-in member can complete
   onboarding, appear in others' pools, and *respond* to a Spark — but cannot browse or
   initiate. They are **passive-only**.

This is by design, not a bug — but it means "opt in and discover others" is, for a free user,
"opt in and *wait to be discovered*." That likely surprises users and caps the network's
liquidity (everyone passive, few initiators).

**Recommendation (needs reflection, not a code fix):** Decide the free-user online experience
explicitly. Options: (a) give free members a limited browse/Drop so the funnel has reciprocity;
(b) keep Premium-gated initiation but message it honestly in the teaser; (c) reduce the LuxID
hard-requirement for the candidate pool to grandfather event-verified members (with an age
caveat — see V1). Whatever the choice, make the gates and their rationale a first-class product
artifact.

### C2. No user reporting and no peer blocking — critical safety gap
**Priority:** P0-Critical **Category:** `safety`, `compliance`
**Files:** *(absent — confirmed)* `crush_lu/models/` has no `Block`/`Report`/`Abuse`/`Flag`
model; the only exclusion lever is coach-side `CrushConnectMembership.excluded_by_coach`
(`models/crush_connect.py:134`)

Members cannot **report** an inappropriate profile/message, nor **block** another member.
The only tool is a coach manually setting `excluded_by_coach`. For an in-person, coach-mediated
product that is defensible; for an online discovery platform it is a **must-fix before launch** —
both ethically (user safety) and practically (app-store / DSA-style obligations typically
require user reporting and blocking).

**Recommendation:** Treat this as launch-blocking. Add `UserReport` (reporter, target, reason,
context object, status) and `UserBlock` (blocker, blocked) models; wire blocking into
`get_eligible_pool` / Drop selection and Spark eligibility so blocked pairs never surface to
each other; route reports into a coach/admin moderation queue (the admin patterns already
exist for `ProfileSubmission`).

### C3. No peer-to-peer messaging on the online surface
**Priority:** P2-Medium **Category:** `feature-completeness`
**Files:** `crush_lu/models/connections.py` (`ConnectionMessage` — event-scoped, coach-moderated),
`crush_lu/models/crush_connect.py:573` (`CuriositySpark` docstring: reveal is M6)

The only messaging that exists is the **event-connection**, coach-moderated `ConnectionMessage`.
There is no Connect DM; an accepted Spark is handed to a coach to arrange the date (M6 reveal
not built). That is a deliberate milestone gap, but it means the online loop currently can't
close without a human in the middle.

**Recommendation:** Confirm M6 (mutual reveal + messaging) is on the roadmap and sequence it
*after* C2 (you don't want DMs before blocking/reporting exist).

### C4. No mutual matching / reveal yet
**Priority:** P2-Medium **Category:** `feature-completeness`
**Files:** `crush_lu/models/crush_connect.py:573-648` (`CuriositySpark` — one-directional,
silent declines), `crush_lu/services/crush_connect.py` (`send_spark`/`respond_to_spark`)

Sparks are intentionally asymmetric (only Premium receivers send; declines are silent; mutual
reveal is M6). Worth stating plainly in the review so stakeholders know the "match" mechanic
most users expect from online dating is not in place yet.

**Recommendation:** No action beyond roadmap visibility; ensure the teaser copy doesn't imply
mutual matching before M6 ships.

### C5. Discovery is one Drop/day — by design
**Priority:** P3-Low (note) **Category:** `product`
**Files:** `crush_lu/services/crush_connect.py:278` (`get_or_create_daily_drop`),
`crush_lu/models/crush_connect.py:447` (`ConnectDailyDrop`)

There is no browse/search; discovery is a pinned, once-daily Drop (a deliberate
low-overwhelm choice). Flagged only so "discover others" expectations are calibrated: the
product is a curated daily ritual, not a swipe deck.

**Recommendation:** Keep, but make sure marketing/teaser language matches the daily-drop model.

---

## Prioritized roadmap (what to do with this)

| Pri | Item | Why now |
|-----|------|---------|
| **P0** | C2 — user reporting + blocking models, wired into pools/Sparks | Safety + compliance; launch-blocking for online |
| **P0** | C1 — decide the free-user discovery experience & LuxID/Premium gates | Defines the entire online product; everything else hangs off it |
| **P1** | V1 — age/identity verification policy for non-LuxID members | Keeps minors out; pairs with C1's gate decision |
| **P1** | V2 — verified-photo / liveness for Connect members | Core online trust signal; anti-catfishing |
| **P1** | S1 — unify the two signup paths' consent capture | GDPR consistency |
| **P1** | S4 — re-triage `@csrf_exempt` auth endpoints (carry-over) | Wider blast radius online |
| **P2** | S2/S3 — `SOCIALACCOUNT_LOGIN_ON_GET=False`; social auto-link test | Auth hardening |
| **P2** | V3 — define coach-review vs. LuxID self-serve split | Scale the funnel |
| **P2** | C3/C4 — Connect messaging + mutual reveal (M6), sequenced after C2 | Closes the online loop |
| **P3** | C5 — keep daily-Drop model; align teaser copy | Expectation-setting |

---

## How these findings were verified

- Each cited file/line was read on the current branch before the claim was written.
- Carry-over items were re-checked against `crush-lu-review-issues.md`: Issue 5 (email
  verification) is **resolved** (now `mandatory`); Issue 7's *duplicate* setting is
  **resolved** (single definition), though the `True` value remains (see S2).
- The two strongest claims were re-confirmed by grep: **no** `Block`/`Report`/`Abuse`/`Flag`
  model exists under `crush_lu/models/`, and **no** `get_signup_form_class` override exists in
  the adapters (so `/accounts/signup/` falls back to Entreprinder's form on crush.lu).
