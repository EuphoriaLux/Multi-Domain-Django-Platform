# Schueberfouer Thermal Ticket — technical surface audit

Date: 2026-08-29

Task: `t_efaaec39`

Status: documentation only; **NO-GO for 2026**

Decision source: product-owner task `t_98963758` (as carried into this task and
sibling campaign brief)

## Disposition

The repository can support parts of a future Schueberfouer Thermal Ticket pilot,
but it does not contain the anonymous acquisition, consent, session, activation,
or deletion flow defined by the campaign brief. The existing event ticket and
event registration paths are not safe substitutes.

The smallest correct change for the current task is this audit. Do not add a
public route, create a campaign record, print a QR code, alter the live event
check-in ticket, collect prospect data, enable dispatch, or change production
configuration. A future build requires a newer product-owner decision and the
privacy, security, operational, and product gates in the companion brief being
prepared by sibling task `t_fb85daa6` (target path
`docs/specs/schueberfouer-thermal-ticket-campaign.md`).

If those gates are later approved, the smallest safe implementation is a
Django-owned, default-off, anonymous ticket-activation vertical slice. It needs
its own model, token resolver, consent state, printer renderer, public localized
page, staff issue controls, deletion path, and focused tests. It should not use
`MeetupEvent`, `EventRegistration`, Event Lobby, invitation acceptance, Crush
Connect waitlist, or the external Next.js staff SPA as its domain model.

## Repository and Next.js boundary

This repository is the Django monolith and its Django REST Framework API. It
does **not** contain a tracked Next.js application: there is no
`next.config.*`, no tracked application `.tsx` surface, and the root
`package.json` has no `next` dependency.

The only explicit Next.js boundary is the external staff SPA at `hub.crush.lu`:

- `azureproject/urls_api.py:1-6` describes `/hub/*` as the JSON API consumed by
  that SPA.
- `azureproject/views_spa_auth.py:1-18` implements its staff-only
  session-to-JWT exchange.
- `hub/urls.py:36-143` exposes staff CRM, finance, events, social, and WhatsApp
  APIs.

A future public `/fouer/` experience belongs on `crush.lu`. Ticket issue and
physical handoff are staff operations, but the current Django admin/coach
surfaces can host them without a new Hub API. There is no reason to make a
Next.js change for the first vertical slice. If a later product decision
requires Hub reporting, that is a separate repository and API-contract task.

## Current surface map

| Concern                 | Existing implementation                                                                                               | Decision                                                                                                         |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Public landing pages    | `crush_lu/views_static.py`, `home.html`, `about.html`, `how_it_works.html`, and `invitation_landing.html`             | Reuse the shared base and presentation conventions, not the invitation business flow. No `/fouer/` route exists. |
| Localized routing       | `azureproject/urls_crush.py:1-13` and the include of `crush_lu/urls.py` inside the i18n urlconf                       | A future route can naturally expose `/fr/fouer/`, `/de/fouer/`, and `/en/fouer/`.                                |
| Event registration      | `crush_lu/views_events.py:754-1023,1089-1708`, `forms.py:711-970`, and `event_register.html`                          | Unsuitable. It requires authentication and applies event/profile/capacity/payment rules.                         |
| Browser event ticket    | `crush_lu/views_ticket.py` and `event_ticket.html`                                                                    | Unsuitable. It is an authenticated, owner-only ticket for a real event registration.                             |
| Thermal check-in ticket | `crush_lu/services/ticket_printer.py`, `views_checkin.py:2052-2160`, and `test_ticket_printing.py`                    | Reuse only the low-level ESC/POS primitives. Do not modify or call the event renderer for anonymous acquisition. |
| Invitation landing      | `crush_lu/views_invitations.py`, `invitation_landing.html`, and `EventInvitation`                                     | Unsuitable. It starts from a pre-created invitation containing guest PII and creates an account/profile.         |
| Crush Connect waitlist  | `crush_lu/api_crush_connect.py` and `crush_connect/_waitlist_join.html`                                               | Unsuitable. It is authenticated and product-specific.                                                            |
| Outreach campaign       | `models/campaigns.py`, `services/campaigns.py`, `admin/campaign_dashboard.py`, and `docs/specs/campaign-dashboard.md` | May notify consented existing members after approval; it is not an anonymous street-acquisition system.          |
| Anonymous activation    | None                                                                                                                  | New, explicitly approved data and state contract required.                                                       |
| Public session creation | None suitable                                                                                                         | Existing login, event, WhatsApp OTP, and Hub SPA flows do not meet the brief. This is a launch blocker.          |
| Pairing/first value     | None approved for this campaign                                                                                       | Do not invent or expose matching, chat, radar, reward, or “ready” states.                                        |

## Why the live thermal ticket must remain untouched

`crush_lu/services/ticket_printer.py:1-10` builds an 80 mm ESC/POS check-in
receipt from an `EventRegistration`. Its renderer includes attendee/event
identity, table and candidate assignments, event-specific “passport” items,
missions, and a QR link into the authenticated post-event product. The related
APIs in `views_checkin.py` are coach-authenticated and the tests construct real
`MeetupEvent`, `CrushProfile`, and `EventRegistration` rows.

That is a different object from the proposed anonymous paper activation key.
Reusing it would:

- require an account and event registration before the information/consent step;
- risk printing personal or event-attendee data;
- mix acquisition issue counts with event check-in behavior;
- route QR scans to Event Lobby/MyCrush rather than `/fouer/`; and
- make rollback of a street pilot capable of regressing a live event operation.

A future campaign renderer should be a separate
`crush_lu/services/fouer_ticket_printer.py`. It may reuse the generic directives,
encoding, QR, paper, and layout utilities under `power_up.atmos.printing`, but
not `build_checkin_ticket_*`, its dictionaries, or its event APIs.

The campaign process must record three distinct milestones: activation record
created, RawBT payload handoff succeeded, and a staff member confirmed physical
issue. A generated payload is not evidence that paper reached a visitor.

## Landing-page and reusable component findings

A future information/activation page should extend `crush_lu/base.html` to
inherit navigation, CSP, SEO blocks, language behavior, analytics integration,
dark mode, and the shared cookie banner. Follow `crush_lu/STYLE.md` and reuse:

- shared icon includes;
- `crush_lu/templates/crush_lu/components/form_field.html` for approved Django
  fields;
- current page-width, card, hero, button, focus, and error conventions; and
- the existing language switch and locale-aware date/text machinery.

Use caution with `includes/cta_section.html`: its destination is hardcoded to
signup, but the campaign must present information before participation or
account/session creation. Do not copy `invitation_landing.html` wholesale; its
token, PII, account, and profile assumptions are specific to private event
guests.

No existing anonymous email/phone lead form is reusable. Adding one would
invent a purpose, retention period, legal basis, deduplication rule, abuse
control, and follow-up channel. The approved future contract should instead
start with an anonymous, non-PII ticket record.

## Localization

The site supports English, German, and French URL prefixes and gettext catalogs:

- `azureproject/settings.py:1168-1193` defines `LANGUAGES`, `LOCALE_PATHS`, and
  modeltranslation languages.
- `crush_lu/translation.py` registers translated model fields.
- templates use `{% trans %}` and `{% blocktrans %}`.

The campaign brief makes French the source copy. A future implementation should:

- expose stable `/fr/fouer/`, `/de/fouer/`, and `/en/fouer/` information routes;
- wrap every web string in gettext tags;
- put reviewed French text into the French catalog;
- require human review of German and English before printing or publishing;
- use a finite, reviewed set of printed role labels rather than free text;
- retain the chosen language through token resolution, consent, session
  creation, activation, support, and deletion; and
- test the rendered `/fr/` page directly rather than assuming catalog
  compilation proves the right text appeared.

Do not change the global `LANGUAGE_CODE` or modeltranslation fallback. Do not add
a Luxembourgish product variant without a product/localization decision; it is
not a configured application locale.

## Analytics and attribution

### Existing behavior

`crush_lu/base.html:1-10,213-215,1453` installs the shared analytics tags and
cookie banner. `azureproject/templatetags/analytics.py` provides CSP-nonced tags
for GA4, Meta Pixel, and Application Insights.

The existing event funnel emits `begin_checkout`, waitlist/reservation,
`purchase`, and Meta events in `event_register.html` and
`_event_registration_success.html`. Those events describe event commerce and
must not be reused as proxies for anonymous ticket issue or activation.

Existing member outreach can use `CampaignLink` and `/c/<token>/`. Per
`models/campaigns.py` and `docs/specs/campaign-dashboard.md:65-68`, click rows
store link, optional attributed user, and timestamp—no IP address or user agent.
Physical QR attribution is different: it should use the approved UTM values from
the campaign brief and must not contain a signed member recipient.

### Essential state versus optional analytics

Ticket lifecycle records are operational/security state, not optional analytics.
They must remain correct when analytics is blocked or declined. A future model
or append-only event log should record idempotent state transitions such as
record creation, printer handoff, physical issue, consent, activation, stop,
expiry, and revocation.

Optional analytics may mirror approved milestones only under the site's current
consent behavior. Allowed properties are random, non-reversible campaign/ticket
analytics IDs, language, batch/station IDs, state versions, and approved UTM
values. Never send:

- the activation token or its digest;
- a database primary key exposed as a visitor identifier;
- name, email, phone, birth date, IP, user agent, or free text;
- orientation, preference, counterpart identity, or role selection; or
- invitation, payment, or authentication secrets.

### Token-safe resolution

Do not render an analytics-enabled page at a URL containing the activation
token. A safer future pattern, still subject to threat-model review, is:

1. The QR targets a minimal resolver such as
   `/fr/fouer/resolve/#t=<opaque-token>`. URL fragments are not sent in the HTTP
   request. This page must load no analytics or third-party resources.
2. A small CSP-nonced resolver script on an `ensure_csrf_cookie` view
   immediately removes the fragment from browser history and submits the token
   in the body of a CSRF-protected POST to a rate-limited resolver endpoint.
   Request-body and error logging must redact it.
3. The server validates the token, stores only an internal ticket handle in the
   anonymous Django session, and redirects to stable `/fr/fouer/`.
4. The stable page may emit consent-permitted information/activation events
   using the separate random analytics ID, never the token.

The printed fallback URL can open the same token-free resolver and offer an
approved manual-code entry that posts in the request body. This design keeps the
secret out of access-log URLs, referrers, automatic page-view locations, browser
history after resolution, and client event payloads. Exact token generation,
storage, hashing, rotation, request-body scrubbing, and fallback-code design
still require security review.

### Consent caveat

The server helper currently treats an absent cookie-consent value as enabled for
rendering, while the browser banner initially has no saved preference. Do not
expand Meta landing-page instrumentation or call the current behavior proven
compliant. Resolve that pre-existing consent question before any marketing
pixel is part of a pilot launch gate.

## Consent and privacy

There are three separate decisions; none substitutes for another:

1. **Cookie consent** — `core/templates/includes/cookie_banner.html:461-590`
   stores analytics/marketing choices and updates Google Consent Mode.
2. **Crush account data consent** — `crush_lu/consent_middleware.py:20-78`
   gates authenticated Crush.lu routes.
3. **Fouer participation consent** — does not exist and must be purpose-specific,
   versioned, revocable, and separate from optional marketing consent.

A future public information route must be accessible without login. Its exact
prefix should be explicitly exempted in `CrushConsentMiddleware`; do not exempt
all campaign or event paths. Reading information, scanning a QR, receiving a
paper ticket, or starting consent must not create marketing consent.

Before a model is designed, the owner/privacy review must decide legal basis,
consent text/versioning, minimum retention, expiry, deletion/anonymization,
support access, audit evidence, and what happens if a visitor later creates or
links an account. Those are launch blockers and must not be inferred by an
implementer.

## Feature flags and kill switches

This repository uses explicit default-off settings rather than a generic flag
service. Examples in `azureproject/settings.py:586-615` default new Crush
features off; campaign dispatch is separately guarded by
`CAMPAIGN_DISPATCH_ENABLED=False` (`settings.py:69-73`).

After approval, use independent, default-false controls rather than one broad
“campaign on” switch:

```python
FOUER_INFORMATION_ENABLED = _env_bool("FOUER_INFORMATION_ENABLED", default=False)
FOUER_TICKET_ISSUANCE_ENABLED = _env_bool(
    "FOUER_TICKET_ISSUANCE_ENABLED", default=False
)
FOUER_TICKET_ACTIVATION_ENABLED = _env_bool(
    "FOUER_TICKET_ACTIVATION_ENABLED", default=False
)
```

The information flag gates public pages. Issuance stops creation/printing without
hiding support or deletion from already-issued tickets. Activation stops new
consent/activation transitions without deleting records. Stop/deletion and
staff incident paths must remain available during rollback. Production values
must not be changed by the implementation task.

Campaign Dashboard delivery remains a fourth independent gate. Any
existing-member notice starts as `Campaign.status="draft"`, and global dispatch
stays off until separately approved.

## Future data and state contract (approval required)

No current model safely represents the brief. A future focused model, named here
only to make the integration surface concrete, should cover:

### `FouerTicket`

- random opaque secret generated with a cryptographic RNG;
- only a one-way/keyed digest persisted if the chosen lookup design supports it;
- separate random, non-reversible analytics ID;
- campaign/year identifier, reviewed language, print batch, and printer station;
- explicit states such as created, printer-handoff, issued, consented, activated,
  stopped, expired, and revoked;
- expiry and transition timestamps;
- consent-version reference after consent, not before;
- optional participant/session/account link only after the approved consent and
  authentication contract; and
- database constraints/transactions that make issue and activation idempotent.

Do not add name, phone, email, date of birth, IP, user agent, free text, or a
counterpart identity. Whether finite role codes are permissible is a privacy and
product decision; do not add them to the schema merely because draft print copy
contains placeholders.

### Participation consent

Use a separate immutable record or an equally auditable append-only structure
for consent version, age confirmation, timestamp, ticket, and withdrawal. Keep
marketing consent separate and default it off. Do not overload
`UserDataConsent`: an anonymous visitor may never become a Crush user, and the
campaign purpose/version differs from general account consent.

### State changes

All mutations should be POST-only, CSRF-protected for browser sessions,
rate-limited, transactional, and idempotent. Ticket resolve, consent, activation,
stop, expiry, and revocation need explicit transition tests. Do not let optional
analytics failure roll back essential state.

## Concrete future Django surface

Only after a superseding owner decision, expect a focused vertical slice across:

1. `azureproject/settings.py`
   - default-off information, issuance, and activation flags;
2. `crush_lu/models/fouer.py` plus one migration
   - anonymous ticket lifecycle and participation-consent records;
3. `crush_lu/admin/fouer.py` or a narrowly permissioned coach view
   - create record, render ticket, record RawBT handoff, confirm physical issue,
     revoke, and support lookup without exposing secrets;
4. `crush_lu/services/fouer_ticket_printer.py`
   - new campaign renderer using only generic ESC/POS/layout primitives;
5. `crush_lu/views_fouer.py`
   - public information page, token resolver, consent, activation, status, stop,
     and deletion endpoints with explicit method/transition guards;
6. `crush_lu/urls.py`
   - localized stable `/fouer/` plus a token resolver that immediately removes
     the token from the browser URL;
7. `crush_lu/consent_middleware.py`
   - exact public information/resolver exemptions only;
8. `crush_lu/templates/crush_lu/fouer/`
   - information, consent, truthful waiting/status, invalid/expired, support,
     and stop/deletion states;
9. locale catalogs
   - reviewed French source and human-reviewed German/English variants; and
10. focused model, view, security, printer, analytics, localization, consent,
    and browser tests.

The approved public authentication/session contract is not present. Do not fill
that gap with generic signup, Event Lobby, WhatsApp OTP, invitation acceptance,
or Hub SPA auth. The first vertical slice may stop at anonymous information,
consent, and a reviewed anonymous session only if product/privacy/security
explicitly approve that scope and truthful first-value state.

## Required future tests

### Disabled-by-default and closure

- every new setting defaults false;
- public information/resolver routes 404 while disabled;
- issue and activation POSTs cannot mutate state while disabled;
- rollback blocks new issue/activation but preserves support and deletion;
- no campaign/outreach record is created automatically; and
- existing check-in ticket snapshots and APIs remain byte/behavior compatible.

### Token and state security

- token has sufficient entropy, expires, revokes, and cannot be replayed;
- only issued tickets reach information/consent as the approved contract allows;
- activation is single-use and retry-idempotent under concurrent requests;
- raw token never appears in templates, analytics, error bodies, operator lists,
  or stable URLs after resolution;
- invalid/expired/revoked responses do not disclose token existence beyond the
  approved support message;
- resolver sets the approved referrer/cache policy;
- CSRF, rate limits, permissions, and session binding fail closed; and
- no endpoint accepts a client-supplied state transition or analytics ID as
  authority.

### Consent and deletion

- information is readable anonymously before participation;
- age/participation consent is explicit and versioned;
- marketing consent is separate, unchecked by default, and optional;
- declining analytics does not block essential issue/consent/stop operations;
- withdrawal/deletion is authenticated to the ticket/session contract,
  idempotent, and remains available during campaign rollback; and
- retention/expiry jobs preserve only the approved minimum evidence.

### Localization and accessibility

- `/fr/fouer/`, `/de/fouer/`, and `/en/fouer/` render the reviewed copy;
- language survives token resolution and every state transition;
- printed copy and QR fallback use the same reviewed locale;
- mobile focus order, labels, errors, contrast, zoom, and keyboard behavior pass;
- invalid/expired/support states are understandable without color alone; and
- physical 80 mm output has no clipping, accidental wraps, or unreadable QR.

### Analytics and data minimization

- essential state succeeds when analytics throws or is absent;
- optional events obey the accepted cookie state;
- events deduplicate by server milestone, not page refresh;
- only approved non-PII properties appear;
- the token, digest, PII, preferences, counterpart, IP, and user agent never
  appear; and
- existing-member outreach remains consent-gated and excluded from new-member
  acquisition counts.

Adjacent regression suites would include:

```bash
pytest crush_lu/tests/test_ticket_printing.py -q
pytest crush_lu/tests/test_events.py -q
pytest crush_lu/tests/test_invitation_security.py -q
pytest crush_lu/tests/test_campaign_tracking.py -q
pytest crush_lu/tests/test_campaigns.py -q
pytest crush_lu/tests/test_api_admin_campaigns.py -q
python manage.py check
python crush_lu/scripts/lint_design_tokens.py <changed-fouer-templates>
```

The future focused test commands cannot be named until the approved module split
exists, but they must cover every gate above. Browser and physical-printer checks
are required; unit tests alone cannot prove QR readability or field handoff.

## Current no-action checklist

For 2026, verified safe behavior is:

- no `/fouer/` route or destination;
- no anonymous ticket/consent/activation model;
- no campaign QR, token, print batch, or hardware order;
- no modification to `ticket_printer.py` or the event check-in APIs;
- no Campaign Dashboard send, schedule, or tracking link;
- no paid media or production flag change;
- no prospect data collection; and
- no Next.js or Hub API change.

A superseding owner decision must provide year/dates, venue permission, budget,
staff/support/stop owners, visitor cap, first-value behavior, authentication and
session design, privacy/legal basis, retention/deletion, token threat model,
operational thresholds, and final localized copy. Until then, an implementer
must stop at documentation rather than infer missing product or privacy rules.
