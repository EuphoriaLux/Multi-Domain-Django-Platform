# Crush content pipeline tooling

Build the five inactive n8n imports with `python build_workflows.py`. Topic inputs
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

## Luxembourg idea feed

`content-ideas` is an internal authenticated service with a durable SQLite volume.
It reads public sources through the existing self-hosted Firecrawl API on Hermes.
The source registry is `idea-sources.json`; refresh is bounded to two restricted
searches and twelve page reads. It never calls a model, downloads pictures, sends
messages, reads member data or publishes. An n8n collector runs daily at 08:00
Europe/Luxembourg, with manual and authenticated webhook alternatives.

The v2 generators select a sourced idea before generating copy. Input
`posting_date: "YYYY-MM-DD"` chooses the intended posting day (today by default),
up to 90 days ahead; it does not schedule publication. Event sources need a recent
successful read within 48 hours and an event 1–21 days after that posting day.
Places/context expire after 14 days; census context always carries its data period.
Missing dates, expired events and pages outside the registered hosts are excluded.
Only validated title/date/region/provenance fields enter the model, never raw pages.

Selection reserves a source for one hour. Successful review delivery records use;
source cooldown is seven days, exact idea cooldown fourteen days, and recent region
repetition is penalized. Concurrent runs reserve different sources. Replays keep
the same reservation. Failed runs do not consume completed history. No available
idea or an unavailable feed falls back to the six established coaching themes.
An explicit `topic` override, or `use_feed: false`, keeps the original selection.

Reviews retain `posting_date` and `source_idea`; normal Telegram review captions
show the source and check date. Picture-only delivery retains its original behavior.
Run `python -m unittest discover -s tools/n8n_content -p test_idea_feed.py` and the
existing workflow/coordinator checks. Deployment and rollback:
`ai-memory-hub/reviews/2026-10-01-firecrawl-idea-feed.md`.
