# Crush Connect mobile implementation — validation handoff

Implemented on the verified base `68810bb8`, in five sequential PRs.

| Phase | Branch | Result |
| --- | --- | --- |
| 1 | `codex/connect-mobile-1-hub` | Action-first hub, real readiness, Today / Requests / Chats, mobile spacing |
| 2 | `codex/connect-mobile-2-discovery` | One expanded daily card, actual progress, compact review, deliberate send/decline confirmation |
| 3 | `codex/connect-mobile-3-chat` | Incremental messages, history, retry deduplication, read acknowledgement, real unread counts |
| 4 | `codex/connect-mobile-4-onboarding` | Seven-step resume flow, compact controls, search and selection summaries, profile preview |
| 5 | `codex/connect-mobile-5-polish` | Consistent localized explanations, accepted Coach's Pick persistence, lobby feedback, final eligibility and concurrency fixes |

The PRs are stacked: merge and validate them in order. Final staging/device validation remains pending. No merge or deployment was performed.

## Local evidence

### Browser compatibility and PostgreSQL follow-up

- **#986:** browsers without `crypto.randomUUID` generate a version-4 submission
  UUID with `crypto.getRandomValues`, retaining the same ID for retries. If Web
  Crypto is unavailable entirely, the composer retains normal form submission.
  Both paths passed real mobile browser checks with the APIs disabled.
- **#988:** request-response queries explicitly lock only the request row, so
  nullable joined profile/membership rows are not included in PostgreSQL's
  `FOR UPDATE`. Accept and decline regression tests assert this lock scope;
  the deployment PostgreSQL checks below remain required.

The final targeted discovery, weekly-request and chat regression suite passed
**126 tests**, including both new lock-scope cases. Focused Ruff, JavaScript
syntax and whitespace checks passed.

### Final review corrections

Five further findings were addressed on their owning branches and merged forward:

- **#984:** pending-request counts now use the inbox's live phase, eligibility
  and pause gates. Hidden counts do not cancel pending requests. The German lobby
  action now reads "Ich möchte dich kennenlernen".
- **#987:** privacy explanations distinguish other members from coaches, who can
  inspect answers, life situation and family preferences for curation. The same
  explanation appears in the public experience page in phase 5. Non-field errors
  render once in the shared validation summary. Work and education use the
  canonical select component with its light/dark chevron treatment.

The latest combined suite passed **395 tests**. The owning hub branch passed 16
tests; the onboarding and hub suite passed 73. Six additional mobile checks passed:
EN/DE/FR coach-visibility disclosures, light/dark selects, and a single question
validation error. Updated screenshots are in the review corrections gallery.
Django checks, migration drift, focused Ruff, affected-template design-token lint,
Crush-only CSS build and whitespace checks passed. Merged catalogues parsed and
compiled with both changes retained and no new duplicate translation entries.

### Second review corrections

Six subsequent findings have corresponding fixes, propagated through the stack:

- **#985:** daily progression and counts exclude unavailable cards; the collapsed
  review summary shows the suggested connection. Eligibility filtering is now
  included in phase 2 so its own review flow is complete.
- **#986:** chat availability checks reuse prefetched participant memberships,
  including after state refresh. The refresh locks only the chat row, avoiding
  PostgreSQL nullable-join locking errors. HTTP requests use an AbortController
  timer instead of requiring `AbortSignal.timeout`.
- **#988:** review highlights are recomputed from the currently visible cards when
  the previous suggestion becomes unavailable. An unavailable sender receives
  localized recovery guidance and returns to Connect home without spending the
  weekly request or changing its deadline.

The final combined regression suite passed **389 tests**. Four additional mobile
browser checks passed against a fresh server using the current code: asynchronous
chat sending with `AbortSignal.timeout` unavailable, and the collapsed suggestion
visible in EN/DE/FR at 360 x 800 without horizontal overflow. Screenshots are in
the [review corrections gallery](http://localhost:8016/review-fixes/).

Django checks, migration drift, focused Ruff, JavaScript syntax, design-token
lint, the Crush-only CSS build and diff whitespace checks passed. Physical-device
and staging PostgreSQL validation below remain outstanding.

### PR review corrections

All 19 Codex findings from PRs #984–#988 have corresponding fixes. Each earlier
PR contains its own corrections; merge commits carry them forward without
rewriting the published branch history. The completed DE/FR catalogue was moved
into phase 1 so phases 1–4 no longer depend on phase 5 for translation coverage.

- **#984:** staff Today preview, live visibility status, and existing-week lifecycle
  synchronization. Overview requests still cannot create a week or daily cards.
- **#985:** prefetched inbox interests, solid confirmation button, and translated
  fallback names in daily/review/request summaries.
- **#986:** unavailable participants filtered before previews; expiry synchronized
  under the same row lock as sends; bounded read-only history remains available
  after closure or blocking. Polling stops on the returned closed state; sending
  and read acknowledgements remain blocked.
- **#987:** public/private labels and preview now cover relationship intention,
  lifestyle and question text; final consent emits one completion message; rejected
  forms remain dirty until saved or deliberately discarded.
- **#988:** latest active Coach's Pick survives later terminal proposals; acceptance
  notification runs after commit; stored cards use bulk eligibility filtering.

Review validation: 374 combined regression cases passed, followed by the remaining
two passing after correcting their fixture to use a distinct later candidate
(376 cases total). The full-week filtering test bounds 21 cards to at most six
queries. The notification test forces a real database integrity failure and verifies
that acceptance and its chat remain committed. The stale-expiry test verifies the
row-lock path and preserves an already extended deadline; PostgreSQL concurrency
validation remains a staging check.

Six additional mobile browser checks passed: invalid wizard Exit cancellation,
invalid profile Back cancellation, EN/DE/FR public profile previews, and loading
all 65 messages in an ended conversation. Updated screenshots are available at
<http://localhost:8016/review-fixes/>. Django checks, migration drift, focused Ruff,
JavaScript syntax, 44-template design-token lint, Crush-only CSS build and diff
whitespace checks passed.

### Initial implementation evidence

- Final combined regression: **346 passed**, covering Connect core, hub, onboarding, daily/weekly experience, chat, pause and Event Lobby/recap.
- Final screenshot matrix: **120 states** across EN/DE/FR, 360/390/430 px, light/dark, plus the refreshed live lobby. No unexpected authentication/consent redirects or horizontal overflow remained.
- Additional browser checks: one-card progression, seven-step completion, exit cancellation and resume, request/decline cancellation, searchable interests, keyboard focus, and reduced-height composer visibility.
- Two-account chat delivery measured **4.98 seconds**. Draft preservation, history loading, stable history scroll, and the New messages control passed.
- Simulated visibility recovery measured **0.05 seconds**; a failed poll selected a 30-second retry. An uncertain send that succeeded on the server was retried and produced exactly one message.
- Django checks, migration drift check, focused Ruff checks, design-token lint, JavaScript syntax checks, and `git diff --check` passed. Only the Crush CSS bundle was rebuilt.
- DE/FR catalogues cover the changed surfaces, including 199 previously missing/untranslated entries and ten older French informal-address strings. Compiled with `polib`; localized browser requests succeeded.

Local screenshot gallery: <http://localhost:8016/>. It includes the final matrix, decision/composer details, and original-review comparison images. The image files and detailed execution logs remain local under `screenshots/connect-mobile-review/`; the original review is preserved under `docs/reviews/connect-mobile-2026-09-14/`.

## Backend behavior to verify on staging

New authenticated routes live under the existing Connect URL tree:

- `week/chats/<id>/messages/`: bounded initial/history/incremental retrieval.
- `week/chats/<id>/read/`: POST acknowledgement of displayed incoming message IDs.
- `week/chats/<id>/send/`: JSON response for asynchronous clients, normal form redirect retained.
- `summary/`: current counts and Coach's Pick status, without starting a week or generating cards.

Migration `0252_connect_chat_submission` adds the optional submission UUID and uniqueness per chat/sender/submission. It initializes legacy messages as read so deployment does not fabricate unread history. The data initialization is intentionally not reversed into unread messages.

Polling and read acknowledgement do not extend chat lifetime. Only genuine new sends retain the existing rolling expiry behavior; retrying an existing submission does not extend it. Paused members retain existing chats. Membership removal, exclusion, deactivation, blocking and expiry are checked at the relevant request boundaries.

Weekly sends lock the session and recheck the one-request limit and current eligibility inside the transaction. Responses and expiry lock and refresh the request to prevent stale transitions from overwriting an accepted request. Stored discovery/review cards recheck current photo consent and safety before exposing member details. No private correct answers, matching internals or CRM-only venue fields are returned.

## Remaining release validation

These checks have **not** been performed on physical devices or a deployed staging environment:

1. Apply the migration in staging and run simultaneous PostgreSQL weekly-request and duplicate-message submissions. SQLite ignores row locks, so local tests validate the locking structure but cannot prove PostgreSQL concurrency behavior.
2. Use two approved staging test accounts on a physical iPhone and Android. Open the software keyboard, send in both directions, scroll older messages, background/restore the app, interrupt connectivity, and retry an uncertain send.
3. Confirm composer and bottom navigation clearance in portrait/landscape, Safari/Chrome and installed PWA mode. The local reduced-height viewport test does not replace a real keyboard check.
4. Repeat block/expiry during an open chat, request confirmation/cancellation, final onboarding consent and event-origin return, and accepted Coach's Pick reload/reassignment on staging.
5. Check EN/DE/FR and both themes on the devices, then review the staged result before requesting merge/deployment approval.

The original unrelated generated CSS edits remain untouched. The pre-implementation Crush CSS snapshot is retained locally for comparison.
