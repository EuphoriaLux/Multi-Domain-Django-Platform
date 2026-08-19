# SECRET_KEY rotation

Task 5.4b ("`SECRET_KEY` Rotation im Hub"). The task name points at the Hub
because that's the most fragile consumer, but there is **no separate
Hub-specific secret** — the Hub SSO token exchange
(`azureproject/views_spa_auth.py`, wired for hub.crush.lu) signs its JWTs
with `SIMPLE_JWT["SIGNING_KEY"]`, which is set to the project's global
`SECRET_KEY`. This doc covers rotating that one key, project-wide, plus the
two consumers that don't automatically benefit from Django's built-in
rotation support.

## What SECRET_KEY signs in this codebase

| Consumer | Mechanism | Honours `SECRET_KEY_FALLBACKS`? |
| --- | --- | --- |
| Django sessions (all logged-in users, incl. staff) | `django.contrib.sessions` signing (`django.core.signing`) | Yes (Django 4.1+, built in) |
| Session auth hash (`AbstractBaseUser.get_session_auth_hash`) | `salted_hmac` via `SECRET_KEY` | Yes — `get_session_auth_fallback_hash()` walks the list |
| Password reset tokens (`django.contrib.auth.tokens.PasswordResetTokenGenerator`) | `salted_hmac`, its own key resolution (not `django.core.signing`) | Yes — its `_secret_fallbacks` property returns `settings.SECRET_KEY_FALLBACKS` when unset |
| allauth email confirmation tokens (`allauth.account.models.EmailConfirmationHMAC`) | `django.core.signing.dumps()` / `.loads()`, called with no explicit `key=` | Yes — same mechanism as the QR tickets below |
| Event check-in QR tickets (`crush_lu/views_ticket.py` `_generate_checkin_token`, verified in `crush_lu/views_checkin.py` `event_checkin_api`) | `django.core.signing.Signer()` called with no explicit `key=` | Yes |
| Hub SSO access/refresh JWTs (`SIMPLE_JWT`, `azureproject/views_spa_auth.py`) | `djangorestframework-simplejwt` `TokenBackend` | **No — confirmed gap, see below** |
| iOS native-app auth handoff code (`crush_lu/models/ios_app.py` `IOSNativeAuthCode`) | hand-rolled `sha256(SECRET_KEY + code)` | Fixed in this PR (was: no) |

The first five rows work automatically once `SECRET_KEY_FALLBACKS` is set —
this is Django's own rotation mechanism (available since 4.1; this repo runs
Django 6.0.8), not something built here. Verified by reading the installed
source directly, not assumed — note these are **two different underlying
mechanisms**, both of which happen to read the same setting:

- `django/core/signing.py`, `Signer.__init__`: `self.fallback_keys =
  fallback_keys if fallback_keys is not None else settings.SECRET_KEY_FALLBACKS`,
  and `unsign()` walks `[self.key, *self.fallback_keys]`. Sessions,
  allauth's `EmailConfirmationHMAC.key`/`.from_key()` (`signing.dumps()` /
  `signing.loads()`, no explicit `key=`), and both `crush_lu/views_ticket.py`
  and `crush_lu/views_checkin.py`'s bare `Signer()` all go through this path.
- `django/contrib/auth/tokens.py`, `PasswordResetTokenGenerator`: a
  *separate* implementation built on `salted_hmac`, not
  `django.core.signing`. Its `secret_fallbacks` property returns
  `settings.SECRET_KEY_FALLBACKS` whenever `_secret_fallbacks` hasn't been
  explicitly set, and its token-checking loop tries the primary secret then
  each fallback.
- `django/contrib/auth/base_user.py`, `get_session_auth_fallback_hash()`:
  explicitly iterates `settings.SECRET_KEY_FALLBACKS`.

## Known gap: Hub SSO JWTs do not survive a rotation

`djangorestframework-simplejwt==5.5.1`'s `TokenBackend`
(`rest_framework_simplejwt/backends.py`) holds a single `signing_key` /
`verifying_key` — there is no fallback-list concept, and `SIMPLE_JWT`'s
`"SIGNING_KEY": SECRET_KEY` is evaluated once at settings-module import time.
**Rotating `SECRET_KEY` immediately invalidates every outstanding Hub access
token (1h lifetime) and refresh token (7d lifetime).**

This is deliberately **not** patched with custom fallback-verification code
in this PR. Two reasons:

1. **It self-heals.** The Hub SPA's next API call gets a 401, the SPA redoes
   the session→JWT exchange (`GET /api/auth/spa-callback/` →
   `POST /api/token/exchange-code/`), and that exchange runs on the staff
   member's crush.lu **session**, which — per the table above — *does*
   survive rotation via `SECRET_KEY_FALLBACKS`. So the practical effect is a
   silent one-time re-auth for Hub staff, not a lockout, as long as the
   fallback key is in place at the moment of rotation.
2. **A partial fix isn't worth the risk.** Covering only access-token
   verification would still lose every refresh token, forcing the same
   re-bounce anyway — the only way to actually preserve a session across
   rotation would be to also override `TokenRefreshView`'s serializer, which
   sits right next to `ROTATE_REFRESH_TOKENS` / `BLACKLIST_AFTER_ROTATION`
   and is not a place to bolt on custom crypto-verification paths without a
   dedicated review.

Action item for the person rotating: **tell Hub/CRM staff to expect to be
logged out of hub.crush.lu once**, around the rotation.

## Known gap (fixed here): iOS native-app auth handoff codes

`crush_lu/models/ios_app.py`'s `IOSNativeAuthCode` looked up its one-time
code by `sha256(SECRET_KEY + code)`, computed fresh at both issue and
consume time — not `django.core.signing`, so it got no automatic
`SECRET_KEY_FALLBACKS` support. The blast radius was small (default TTL is 5
minutes — `IOS_AUTH_CODE_TTL_SECONDS`), but a code issued in the seconds
before a rotation would have silently failed to match on read. Fixed by
`_candidate_auth_code_hashes()`, which hashes the code under the current key
and every fallback key and matches with `code_hash__in=...`.

## The one thing that makes this rotation different from a normal one: unbounded-lifetime QR tickets

`event_checkin_api`'s own comment says it plainly: **"tickets do not
expire."** `_generate_checkin_token()` signs with `Signer()` (not
`TimestampSigner`), and once a `checkin_token` is written to a registration
it is reused forever (`if registration.checkin_token: return
registration.checkin_token`) — it can be issued the moment someone registers,
weeks before the event, and printed/saved to a wallet long before doors open.
The signature itself never expires; only the door's own ±12h
(`EVENT_CHECKIN_WINDOW_HOURS`) check-in window makes it *useless* outside
that span.

That means the usual "wait out the max token/session lifetime, then drop the
old key" rule is not enough here — the relevant lifetime is not a fixed
duration, it's **"every event that had at least one registration created
before the rotation has finished its check-in window."** Concretely:

> Do not drop the old key from `SECRET_KEY_FALLBACKS` until
> `event.date_time + 12h` (or your current `EVENT_CHECKIN_WINDOW_HOURS`) is
> in the past for every `MeetupEvent` that had any `EventRegistration`
> created before the rotation.

For a platform with events booked weeks out, this can mean carrying the
fallback key for weeks, not hours — check the furthest-out published event
with existing registrations before scheduling the second deploy.

## Rotation procedure

This code makes rotation an **app-settings-only operation** — no code
deploy is required to rotate, only to *enable* rotation (this PR) and, later,
to remove old code paths if desired. Two app-settings changes, separated by
a waiting period:

**Step 1 — introduce the new key**
1. Generate a new key: `python -c "from django.core.management.utils import
   get_random_secret_key; print(get_random_secret_key())"`. Its alphabet has
   no comma, so it's always safe to join into the fallback list as-is.
2. Set `SECRET_KEY` to the new value.
3. Set `SECRET_KEY_FALLBACKS` to the *old* `SECRET_KEY` value (comma-separate
   if more than one old key needs to stay accepted).
4. Restart/redeploy so the new app settings are picked up. Before doing this
   on prod, confirm whether `SECRET_KEY` is slot-pinned via
   `slotConfigNames` on the App Service — other env vars in this project
   (e.g. `SUMUP_*`) are slot-sticky by design, and an unpinned `SECRET_KEY`
   during a slot swap would mean prod and staging silently diverge on it.
   Check, don't assume either way.
5. Tell Hub/CRM staff to expect one re-login on hub.crush.lu (see the JWT
   gap above).

**Step 2 — retire the old key**, only once *both* of these are true:
- The longest-lived session/password-reset/allauth-token window has fully
  elapsed since Step 1.
- Per the QR-ticket rule above: every event with registrations that existed
  before Step 1 has passed its check-in window.

Then remove the old key from `SECRET_KEY_FALLBACKS` (empty list, or drop just
that entry if multiple rotations are stacked) and redeploy/restart again.

## What this PR does NOT do

- It does not rotate any real key, on any environment. `SECRET_KEY_FALLBACKS`
  defaults to empty (`[]`), identical to today's behavior.
- It does not contain a new secret value anywhere in the diff, commit
  history, or this doc.
- It does not build custom multi-key JWT verification for the Hub SSO flow —
  see "Known gap" above for why, and what to expect instead.
