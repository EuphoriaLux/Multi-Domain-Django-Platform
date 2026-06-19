# Changelog routine — auto-update `/changelog/` on PR merge

This document describes how the public **"What's New"** changelog
(`https://crush.lu/changelog/`) updates **automatically when a PR is merged into
`main`**, using a [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web)
routine plus the secured ingest endpoint added in PR #515.

Production database credentials never leave the server: the routine composes the
user-facing copy in an isolated cloud session and makes a **single authenticated
HTTPS call** to Django, which does the write.

```
PR merged → main
  └─(GitHub trigger)→ Claude Code routine (cloud session, full git clone)
       • reads the merged PR + git log, decides if it is user-facing
       • picks the current release window from release_milestones.json
       • writes warm, plain-language copy and scrubs secrets
       └─ POST (Bearer ADMIN_API_KEY) → /api/admin/changelog/ingest/
             └─ Django upserts PatchRelease + PatchNote(is_published=True)
                   → live on /changelog/
```

---

## 1. The ingest endpoint (already deployed by this PR)

`POST /api/admin/changelog/ingest/` — implemented in
[`crush_lu/api_admin_changelog.py`](../crush_lu/api_admin_changelog.py).

| Property | Value |
| --- | --- |
| **Auth** | `Authorization: Bearer <ADMIN_API_KEY>` (constant-time compare against `settings.ADMIN_API_KEY`). Missing/wrong → `401`. |
| **Method** | `POST` only. Anything else → `405`. |
| **Path** | Language-neutral (outside `i18n_patterns`), so the caller uses a fixed URL with no `/en/` prefix. |
| **Scrubbing** | Every incoming string is passed through `crush_lu.changelog_text.scrub` **server-side** — emails, internal Azure hosts, `Co-Authored-By`/`Signed-off-by` trailers, and `claude.ai/code` session URLs are stripped even if the caller forgets. The caller is never trusted. |
| **Idempotency** | Keyed on the **merge-commit SHA** in `notes[].related_commits`. A note whose SHA is already persisted on the release is skipped, so a re-delivered webhook (or a re-run) never duplicates a note. |
| **Publish** | `release.is_published` defaults to `true` → live immediately. Set it to `false` to stage a draft for admin review. |

### Request body

```jsonc
{
  "release": {
    "version":      "v1.8",                       // ≤ 20 chars, required
    "slug":         "v1-8-crush-connect",          // ≤ 80 chars, valid slug, required (unique key)
    "title":        "Crush Connect, reimagined",   // ≤ 140 chars, required
    "hero_summary": "A warmer, privacy-first ...", // ≤ 280 chars, optional
    "released_on":  "2026-06-19",                  // ISO date, optional (defaults to today)
    "is_published": true                            // optional, defaults true
  },
  "notes": [
    {
      "category":        "improvement",            // one of: feature | improvement | fix | under_hood
      "title":           "Clearer Crush Connect",  // ≤ 160 chars, required
      "body":            "You're in the mix ...",   // plain text, optional
      "related_commits": ["<MERGE_COMMIT_SHA>"],   // REQUIRED for idempotency — include the PR's merge SHA
      "order":           0                          // optional sort key within the release
    }
  ]
}
```

> **Idempotency contract:** always put the PR's **merge commit SHA** in every
> note's `related_commits`. That is what makes a second delivery of the same PR
> a no-op (`notes_added: 0`).

### Response

```jsonc
// 201 Created
{
  "success": true,
  "created": true,            // false if the release slug already existed (fields updated in place)
  "notes_added": 1,           // 0 on a duplicate/re-delivery
  "version": "v1.8",
  "slug": "v1-8-crush-connect",
  "url": "/changelog/v1-8-crush-connect/",
  "is_published": true
}
```

Error responses: `400` (invalid JSON / bad category / oversized field / invalid
slug / missing release), `401` (bad token), `405` (wrong method).

### Release model

A `PatchRelease` is a dated card on the timeline; each `PatchNote` is one line
item (feature / improvement / fix / under-the-hood) inside it. Multiple merged
PRs in the same release window append notes to the **same** release (matched by
`slug`), so the changelog groups a sprint's work under one heading.

The current release windows live in
[`crush_lu/data/release_milestones.json`](../crush_lu/data/release_milestones.json)
(`version`, `slug`, `title`, `hero_summary`, `since`, `until`). The routine
picks the entry whose `[since, until]` window contains the merge date.

---

## 2. Generate and store the API key

The endpoint reuses the existing `settings.ADMIN_API_KEY`
(`azureproject/settings.py` → `ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")`) —
the same key the Azure Function timer triggers already use. No new production
secret is required if one is already set.

If you need a fresh key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set it in **two** places (they must match):

1. **The server** — `ADMIN_API_KEY` app setting on the App Service (and on
   staging, `test.crush.lu`). This is read at request time by Django.
2. **The Claude routine** — as an environment variable the routine reads when
   composing the `Authorization` header (see below). Store it as a routine
   **secret**, never inline in the prompt or committed to the repo.

---

## 3. Configure the Claude Code routine

Routines are configured in the Claude Code web UI
(<https://code.claude.com/docs/en/claude-code-on-the-web>). Create a new routine
on this repository with the settings below.

### Trigger

| Field | Value |
| --- | --- |
| **Event** | `pull_request` → `closed` |
| **Condition** | `base.ref == "main"` **and** `pull_request.merged == true` |
| **Repository** | `EuphoriaLux/Multi-Domain-Django-Platform` |

Filtering on `merged == true` is important: GitHub fires `closed` for PRs that
were *closed without merging* too, and those must not produce a changelog entry.

### Environment

| Variable | Purpose |
| --- | --- |
| `ADMIN_API_KEY` *(secret)* | Bearer token; must equal the server's `ADMIN_API_KEY`. |
| `CHANGELOG_INGEST_URL` | `https://crush.lu/api/admin/changelog/ingest/` (prod) or `https://test.crush.lu/api/admin/changelog/ingest/` (staging). |

**Network policy:** the routine must be allowed outbound HTTPS to `crush.lu`
(and/or `test.crush.lu`). Pick a network policy on the environment that permits
this host; a fully-isolated/no-egress policy will block the POST.

### Routine prompt

Paste the following as the routine's prompt. It is self-contained: it inspects
the merged PR, decides whether it is user-facing, composes the note, and POSTs.

````markdown
A pull request was just merged into `main`. Your job is to update the public
"What's New" changelog at /changelog/ if — and only if — this change is
user-facing.

## 1. Gather context
- Read the merged PR title, body, labels, and the squash/merge commit message.
- Capture the **merge commit SHA** (the SHA of the commit now on `main`). You
  will send it back for idempotency, so a re-run never double-posts.

## 2. Decide if it is user-facing
Skip (do nothing, end the session) when the change is internal-only:
dependency bumps, CI/build, refactors with no behavior change, tests, infra,
docs, or anything a Crush member would never notice. When in doubt about a
borderline case, skip — a missing note is better than noise.

## 3. Choose the release window
- Read `crush_lu/data/release_milestones.json`.
- Pick the entry whose `[since, until]` date window contains today's date.
- Use that entry's `version`, `slug`, `title`, and `hero_summary` for the
  `release` object. (Appending to the same slug groups the sprint's PRs under
  one card.) If no window matches, create a sensible new one: `version` like
  `vX.Y`, a kebab-case `slug`, a short `title`, and `released_on` = today.

## 4. Write the note (warm, plain language)
- `category`: one of `feature`, `improvement`, `fix`, `under_hood`.
- `title`: ≤ 160 chars, benefit-first, no jargon, no internal scope names.
- `body`: 1–3 short sentences describing what changed for the member.
- `related_commits`: `["<MERGE_COMMIT_SHA>"]` — REQUIRED.
- Never include emails, internal hostnames, SHAs in prose, ticket IDs, employee
  names, or `Co-Authored-By` / `claude.ai/code` lines. (The server scrubs these
  too, but write clean copy regardless.)

## 5. Publish
POST the payload to the ingest endpoint. Read the URL and key from the
environment — do not hard-code them:

```bash
curl -sS -X POST "$CHANGELOG_INGEST_URL" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d @payload.json
```

A `201` with `"success": true` means it is live. `notes_added: 0` means this
PR's SHA was already recorded (a duplicate delivery) — that is fine, end the
session. On `4xx`, fix the payload (most often an invalid `category` or an
oversized `title`) and retry once. Do not commit anything to the repo.
````

---

## 4. Test it

### Endpoint (CI runs this automatically)

```bash
pytest crush_lu/tests/test_api_admin_changelog.py crush_lu/tests/test_changelog.py -q
```

### Manual smoke test

```bash
export ADMIN_API_KEY=...            # must match the server
export CHANGELOG_INGEST_URL=https://test.crush.lu/api/admin/changelog/ingest/

curl -sS -X POST "$CHANGELOG_INGEST_URL" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "release": {
          "version": "v1.8",
          "slug": "v1-8-crush-connect",
          "title": "Crush Connect, reimagined",
          "released_on": "2026-06-19"
        },
        "notes": [{
          "category": "improvement",
          "title": "Crush Connect",
          "body": "You'\''re in the mix — clearer, warmer wording across Connect.",
          "related_commits": ["abc123"]
        }]
      }'
# → 201, entry visible on /changelog/.
# Re-POST the same related_commits SHA → "notes_added": 0 (idempotent).
```

### End-to-end

1. Merge a small user-facing PR into `main`.
2. The routine session starts from the GitHub trigger; watch its log.
3. Confirm a `201` POST and that the entry appears on
   `https://crush.lu/changelog/`.
4. (Optional) Re-run the routine on the same PR and confirm `notes_added: 0`.

---

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `401 Unauthorized` | `ADMIN_API_KEY` in the routine ≠ the server's value, or the server's is unset. |
| POST times out / DNS error | Network policy blocks egress to `crush.lu`. Choose a policy that allows it. |
| `400 ... category must be one of [...]` | `category` not in `feature/improvement/fix/under_hood`. |
| `400 ... related_commits must contain at least one commit SHA` | A note omitted the merge SHA (or sent an empty/blank list). It is required so re-deliveries dedupe. |
| `400 ... exceeds N characters` | A field is over its limit (title 160, release.title 140, hero_summary 280). |
| Entry not on `/changelog/` | `is_published` was sent as `false`, or the release window matched a different (unpublished) slug. |
| Note missing after a re-run | Expected — the merge SHA was already recorded (idempotency). |
