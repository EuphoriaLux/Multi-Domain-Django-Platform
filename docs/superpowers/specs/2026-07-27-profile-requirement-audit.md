# `profile_requirement` audit — the account/profile state space

Written 2026-07-27 after the dropdown proved confusing in production use.
Source of truth: `MeetupEvent.PROFILE_REQUIREMENT_CHOICES`
(`crush_lu/models/events.py:165-180`) and the registration gates in
`crush_lu/views_events.py:808-920`.

## 1. Account is not profile

A `User` and a `CrushProfile` are separate rows. Signing up creates the `User`;
the two `post_save` receivers on `User` create only `EmailPreference` and
`UserDataConsent` (`signals.py:160`, `:196`). **Nothing creates a
`CrushProfile` at signup.** It is created later, when the member enters the
profile/onboarding flow (`views_profile.py:37`), phone verification
(`views_phone_verification.py:147,505`), an invitation
(`views_invitations.py:107`), or certain social-login paths.

So "logged-in crush.lu account" and "has a Crush profile" are genuinely
different populations, and only `profile_requirement="none"` admits the first
without the second.

## 2. The fields the gates actually read

| field | meaning | notes |
|---|---|---|
| `verification_status` | `incomplete` → `pending` → `verified` / `rejected` | the current field |
| `is_approved` | boolean | **legacy.** The model comment (`profiles.py:737`) says it is "replaced by `verification_status == 'verified'`" |
| `phone_verified` | boolean | |
| `assigned_coach_id` | FK | granted on first *attended* event |

`CrushProfile.save()` syncs **one way only**: `is_approved=True` forces
`verification_status="verified"`, never the reverse (`profiles.py:1046-1047`).
In practice both live verification paths — LuxID (`signals.py:2105-2108`) and
coach-at-event (`views_checkin.py:310-313`) — set both fields together, so the
two do not currently diverge. It is a latent hazard, not an active bug.

**But the gates are split across both fields**, which is the root of the
confusion:

- `completed` reads `verification_status` + `phone_verified`
- `approved`, `unverified`, `profile_exists` read the **legacy** `is_approved`
- `coach_assigned` reads `assigned_coach_id`
- `none` reads nothing

## 3. The real states

| id | state |
|---|---|
| **S0** | Account, **no CrushProfile** |
| **S1** | Profile, `incomplete` (form not finished) |
| **S2** | Profile, `pending`, phone **not** verified |
| **S3** | Profile, `pending`, **phone verified** — the LuxID-less member who gets verified in person at the door |
| **S4** | Profile, `verified` via **LuxID** |
| **S5** | Profile, `verified` via **coach at event** |
| **S6** | Profile, `verified` **+ assigned coach** — a *service relationship*, **not** Premium (see F6) |
| **S7** | Profile, `rejected` |

## 4. Who each option actually admits

✅ = can register, ✗ = blocked.

| option (label) | S0 no profile | S1 incomplete | S2 pending, no phone | S3 pending + phone | S4 LuxID | S5 coach-verified | S6 + coach | S7 rejected |
|---|---|---|---|---|---|---|---|---|
| **completed** — "Completed profile (entry event)" | ✗ | ✗ | ✗ | ✅ | ✅¹ | ✅¹ | ✅¹ | ✗ |
| **approved** — "Verified profile only" | ✗ | ✗ | ✗ | ✗ | ✅ | ✅ | ✅ | ✗ |
| **coach_assigned** — "Premium — coach assigned" | ✗ | ✗ | ✗ | only if coach set | only if coach set | only if coach set | ✅ | **only if coach set** ⚠ |
| **unverified** — "Unverified profile only" | ✗ | ✅ | ✅ | ✅ | ✗ | ✗ | ✗ | **✅** ⚠ |
| **profile_exists** — "Profile must exist" | ✗ | ✅ | ✅ | ✅ | **✗** ⚠ | **✗** ⚠ | **✗** ⚠ | **✅** ⚠ |
| **none** — "No profile required" | **✅** ⚠ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**The whole matrix assumes no other event restrictions.** `profile_requirement`
is not the only gate in `event_register`. Two later checks can reject a member
the table marks ✅ — and both reject a *missing profile* outright, so they
narrow the S0 row in particular:

- **Age** — `event_has_age_restriction = min_age > 18 or max_age < 99`
  (`views_events.py:941`). When true, `profile is None` is rejected, so S0
  cannot register **even under `none`**.
- **Languages** — a non-empty `event.languages` list routes through
  `user_meets_language_requirement()`, which rejects a user with no profile
  (`models/events.py:619-627`).

For **event 12** neither applies — min 18 / max 99 (so the restriction flag is
false) and no languages selected — which is why its S0 cohort is real rather
than theoretical. For any other event, read the matrix as "subject to the age
and language gates".

¹ **`completed` does not universally require `phone_verified`.** The gate is
`verification_status == "verified" OR (verification_status == "pending" AND
phone_verified)` (`views_events.py:818-821`). The phone check applies **only to
`pending` profiles**. An already-`verified` member registers regardless of their
phone flag — so S4/S5/S6 are admitted even with `phone_verified=False`. Any label
or summary implying "phone verification required" is wrong for verified members.

## 5. Findings

### F1 — `profile_exists` is mislabelled and duplicates `unverified` (bug)

`views_events.py:897-910` rejects `is_approved` profiles, identical to the
`unverified` branch above it. The label "Profile must exist" promises the
opposite: any profile, verified or not.

**Consequence:** an organiser picking "Profil muss vorhanden sein" to mean
"anyone with a profile" silently locks out **every verified member** — the best
members are the ones excluded. Two options in the dropdown do the same thing and
neither is named for what it does.

### F2 — there is no option for "any profile, verified or not"

Because F1 broke the option that should mean it. The closest is `completed`,
which also demands `phone_verified` and excludes `incomplete`/`rejected`. If an
organiser genuinely wants "has a profile, don't care about state", the dropdown
cannot currently express it.

### F3 — `unverified` and `profile_exists` admit **rejected** profiles

Both gate on `not is_approved`, and a rejected profile has `is_approved=False`,
so it passes. The `completed` branch already guards against exactly this and
says so in a comment (`views_events.py:812-813`):

> Allowlist on purpose — `!= incomplete` would wrongly admit rejected profiles

That reasoning was never applied to the other two branches. (A profile approved
first and rejected later may retain `is_approved=True` and be blocked instead —
so the behaviour is not even consistent within the state.)

### F4 — gates read a field the model calls legacy

Three of the six branches read `is_approved` rather than `verification_status`.
The sync is one-directional, so any future path that sets
`verification_status="verified"` without also setting `is_approved` would make
those three gates disagree with `completed`.

### F5b — `coach_assigned` is a second route for rejected profiles

The branch tests **only** `assigned_coach_id` (`views_events.py:874-885`) and
never looks at verification state. Nothing in the rejection path clears
`assigned_coach` — `views_coach.py:1368` sets
`verification_status = "rejected"` and there is no `assigned_coach = None`
anywhere in the codebase. So a member who was assigned a coach (by attending an
event) and *later* rejected keeps that coach and can still register for premium
`coach_assigned` events.

Together with F3 this makes **two** independent paths by which a rejected
profile registers, so the fix in §6 must gate on `verification_status` in this
branch too, not only in the two loose ones.

### F6 — `coach_assigned` is labelled "Premium" but does not check Premium

The option reads "Premium member — coach assigned", and the gate gates on
`assigned_coach_id` alone. But an assigned coach is **not** the Premium
entitlement. `CrushProfile.has_active_premium` says so in its own docstring
(`models/profiles.py:1024-1035`):

> This — not `assigned_coach` — is the Premium entitlement. A coach can be
> assigned without payment (the 0150 backfill, the attendance auto-assign
> signal), so `assigned_coach` only expresses the service relationship

Every attendee who has attended a coached event is auto-assigned a coach by
`assign_coach_on_first_attendance` — without paying anything. So an organiser
who picks the option named "Premium" to restrict an event to paying members
**admits every past attendee instead**. That is the inverse of F1: there the
label was stricter than the gate, here it is looser, and this one leaks paid
access rather than blocking members.

The fix is either to gate on `has_active_premium` (a behaviour change — decide
deliberately, since it would exclude the coach-assigned non-payers who can
register today) or to rename the option to "Member with an assigned coach" and
add a separate genuinely-Premium level. Not a doc-only fix; flagged for the
same PR as §6.

### F5 — `none` is the only option admitting S0

Everything discussed for event 12 (profile-less attendee → no coach assigned →
unrouted lead in the pool) follows solely from this option — and only because
event 12 also has no age or language restriction to catch the missing profile
downstream (see the note under the matrix). Every other `profile_requirement`
value guarantees a `CrushProfile` exists regardless of those gates.

## 6. Recommended fix

Ordered by value, none blocking for Wednesday.

1. **Fix `profile_exists` to mean what it says** — drop the `is_approved`
   check so it admits any existing profile. This also gives F2 its answer.
   Alternatively remove the option entirely and migrate existing rows to
   `unverified`; but the label describes a level organisers genuinely want, so
   fixing is better than deleting.
2. **Exclude `rejected` from `unverified`, `profile_exists` *and*
   `coach_assigned`** — switch all three to an allowlist on
   `verification_status`, matching `completed`. `coach_assigned` matters as much
   as the loose two (F5b): it never inspects verification state at all, so a
   rejected member who kept an assigned coach registers for premium events.
3. **Read `verification_status` everywhere**, retiring the `is_approved` reads
   in the three gates (F4).
4. **Relabel the options to name what they check.** Be precise about
   `completed` — "phone verified" is true only for the `pending` case, so a
   label like "Completed profile — phone verified (entry event)" misdescribes
   the verified members it also admits. Something closer to "Verified members,
   plus phone-verified profiles awaiting verification (entry event)" and "Any
   profile, verified or not" for `profile_exists`. The current labels describe
   intent; the gates implement something different in both directions.

## 7. For Wednesday

**Correction (verified against production 2026-07-27): event 12 is still
`none`.** An earlier revision of this file said it had been moved to
`completed`; that change never reached the database. The cohort audit came back
clean regardless — 0 of 71 non-cancelled registrations lack a profile — so the
permissive setting was never exercised. Details and the raw output are in
`2026-07-27-crush-pre-event-readiness.md` §1.1b.

Moving it to `completed` remains the right setting if you want the window shut
before the 18:30 deadline, and needs no code:

- requires a profile → **S0 cannot occur for anyone registering from now on**;
- still admits **S3** — profile built, phone verified, *not yet verified* —
  which is exactly the LuxID-less member who gets verified in person at the
  door by a coach (and S4–S6 regardless of their phone flag, per ¹);
- excludes `incomplete` and `rejected`.

**One caveat, and it is the one that can still bite on the day.** The setting is
enforced in `event_register` only, at the moment of registering. It does **not**
revalidate registrations already confirmed while the event was `none`: QR
check-in never re-checks `profile_requirement`, and the attendance signal just
skips assignment when its fresh profile lookup returns nothing. So the guarantee
covers **new registrations only**. Audit the existing cohort before relying on
it — the command is in
`2026-07-27-crush-pre-event-readiness.md` §1.1b. Anyone it returns needs a
profile created *before* they are scanned.

Nothing in §6 needs to ship before the event.
