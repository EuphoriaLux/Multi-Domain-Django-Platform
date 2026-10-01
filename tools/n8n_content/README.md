# Crush content pipeline tooling

Build the four inactive n8n imports with `python build_workflows.py`. Topic inputs
are versioned under `selectors/`; no production export or credentials are needed
to rebuild the definitions.

Install the locked renderer dependencies with `npm ci`, then run `npm test`.
Run coordinator checks with `python -m unittest discover -p test_review_service.py`.
The renderer needs DejaVu Sans fonts; its Dockerfile installs them.

Workflow imports reference credential names. Resolve their IDs and the error
workflow ID in n8n, or use `bind_workflows.py --mapping <file> --output <directory>`
to generate public-API payloads. The API uses `X-N8N-API-KEY`.

The sidecar Compose file joins the existing `n8n_default` network. Keep deployment
secrets outside source control and configure an exclusively owned Telegram bot,
reviewer IDs and the Hub machine credential. Publishing defaults to disabled.

Audit, deployment order, verification and rollback:
`ai-memory-hub/reviews/2026-10-01-n8n-content-pipeline.md`.
Deploy the backend migration before running the new workflow definitions.
For LAN verification before the backend rollout, set `PREVIEW_ONLY=true` and
`ENABLE_PUBLISH=false`. This stores the complete deck on the review volume and
uploads it directly to Telegram. Preview reviews can offer regeneration; they
create no Hub posts and cannot publish, even if publishing is otherwise enabled.
Set `POLL_CALLBACKS=false` for send-only Telegram reviews without action buttons.
This allows the existing bot to keep its current update consumer; error alerts
are still sent. Hermes currently uses this setting at the user's request.
For picture-only comparisons, authenticated generation accepts `visual_only: true`
and a short `comparison_label`. In preview mode this sends only the image album,
without the social captions or action buttons; normal runs remain unchanged.

`preview.cjs` renders a local template preview from existing brand assets placed
in `preview-assets/`. It does not call Gemini. Generated previews, local audit
snapshots and bound payloads are gitignored.
