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

`preview.cjs` renders a local template preview from existing brand assets placed
in `preview-assets/`. It does not call Gemini. Generated previews, local audit
snapshots and bound payloads are gitignored.
