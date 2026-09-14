# Crush Connect mobile implementation — validation handoff

Implemented on the latest verified base `68810bb8`, in five sequential draft PRs.

| Phase | Branch | Result |
| --- | --- | --- |
| 1 | `codex/connect-mobile-1-hub` | Action-first hub, real readiness, Today / Requests / Chats, mobile spacing |
| 2 | `codex/connect-mobile-2-discovery` | One expanded daily card, actual progress, compact review, deliberate send/decline confirmation |
| 3 | `codex/connect-mobile-3-chat` | Incremental messages, history, retry deduplication, read acknowledgement, real unread counts |
| 4 | `codex/connect-mobile-4-onboarding` | Seven-step resume flow, compact controls, search and selection summaries, profile preview |
| 5 | `codex/connect-mobile-5-polish` | Consistent localized explanations, accepted Coach's Pick persistence, lobby feedback, final eligibility and concurrency fixes |

The PRs are stacked: merge and validate them in order. They are drafts pending final staging/device validation. No merge or deployment was performed.

## Local evidence

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
