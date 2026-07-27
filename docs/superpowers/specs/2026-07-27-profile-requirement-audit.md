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
| **S6** | Profile, `verified` **+ assigned coach** (premium) |
| **S7** | Profile, `rejected` |

## 4. Who each option actually admits

✅ = can register, ✗ = blocked.

| option (label) | S0 no profile | S1 incomplete | S2 pending, no phone | S3 pending + phone | S4 LuxID | S5 coach-verified | S6 + coach | S7 rejected |
|---|---|---|---|---|---|---|---|---|
| **completed** — "Completed profile (entry event)" | ✗ | ✗ | ✗ | ✅ | ✅ | ✅ | ✅ | ✗ |
| **approved** — "Verified profile only" | ✗ | ✗ | ✗ | ✗ | ✅ | ✅ | ✅ | ✗ |
| **coach_assigned** — "Premium — coach assigned" | ✗ | ✗ | ✗ | only if coach set | only if coach set | only if coach set | ✅ | ✗ |
| **unverified** — "Unverified profile only" | ✗ | ✅ | ✅ | ✅ | ✗ | ✗ | ✗ | **✅** ⚠ |
| **profile_exists** — "Profile must exist" | ✗ | ✅ | ✅ | ✅ | **✗** ⚠ | **✗** ⚠ | **✗** ⚠ | **✅** ⚠ |
| **none** — "No profile required" | **✅** ⚠ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

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

### F5 — `none` is the only option admitting S0

Everything discussed for event 12 (profile-less attendee → no coach assigned →
unrouted lead in the pool) follows solely from this option. Every other value
guarantees a `CrushProfile` exists.

## 6. Recommended fix

Ordered by value, none blocking for Wednesday.

1. **Fix `profile_exists` to mean what it says** — drop the `is_approved`
   check so it admits any existing profile. This also gives F2 its answer.
   Alternatively remove the option entirely and migrate existing rows to
   `unverified`; but the label describes a level organisers genuinely want, so
   fixing is better than deleting.
2. **Exclude `rejected` from `unverified` and `profile_exists`** — switch both
   to an allowlist on `verification_status`, matching `completed`.
3. **Read `verification_status` everywhere**, retiring the `is_approved` reads
   in the three gates (F4).
4. **Relabel the options to name what they check**, e.g.
   "Completed profile — phone verified, verification pending or done (entry
   event)" and "Any profile, verified or not". The current labels describe
   intent; the gates implement something narrower.

## 7. For Wednesday

Event 12 was moved to **`completed`** ("Vollständiges Profil / Einstiegs-
veranstaltung"). That is the correct choice and needs no code:

- guarantees every attendee has a profile → **S0 cannot occur**, so nobody
  arrives coachless and no declaration lands unrouted in the pool;
- still admits **S3** — profile built, phone verified, *not yet verified* —
  which is exactly the LuxID-less member who gets verified in person at the
  door by a coach;
- excludes `incomplete` and `rejected`.

Nothing in §6 needs to ship before the event.
