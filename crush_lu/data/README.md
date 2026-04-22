# crush.lu changelog — data & generator

This folder holds the seed data for the `/changelog/` page.

## Files

- `release_milestones.json` — ordered list of named milestone releases.
  Each entry has:
  - `version` — semantic version string (e.g. `v1.2`)
  - `slug` — URL slug (unique across all releases)
  - `title` — headline shown on the release card
  - `hero_summary` — one-line lede
  - `released_on` — public release date
  - `since` / `until` — commit window (inclusive / exclusive) for the
    generator to assign commits to this milestone

## Regenerating drafts

```bash
# Dry-run (prints the plan, writes nothing)
python manage.py generate_patch_notes --since 2025-10-01 --dry-run

# Write draft releases + notes to the database (all is_published=False)
python manage.py generate_patch_notes --since 2025-10-01

# Skip monthly catch-up releases (only named milestones)
python manage.py generate_patch_notes --milestones-only

# Start over: delete all unpublished drafts first
python manage.py generate_patch_notes --since 2025-10-01 --purge-drafts
```

## Editorial flow

1. Run the generator (above).
2. Open Django admin → **Patch Releases**.
3. Review each draft: polish the copy, fix `TODO: clarify` placeholders,
   add FR / DE translations (or use the **Auto-translate** admin action
   powered by Azure AI Translator).
4. Toggle `is_published = True` when ready.
5. The `/changelog/` page updates immediately — no redeploy needed.

## What the generator does

- Pulls the Git log since `--since`.
- Keeps commits that touch `crush_lu/` paths **or** whose subjects
  mention crush.lu features (quiz, matching, trait, luxid, …).
- Drops noise (merge commits, dependabot bumps, CI config, typo-only
  commits, Tailwind rebuilds).
- Classifies each commit into one of four categories (feature,
  improvement, fix, under-the-hood) based on the conventional-commit
  type prefix.
- Groups commits by scope (quiz, matching, wallet, …) inside each
  release so related work becomes a single, coherent note.
- Strips author emails, internal URLs, and co-author session links
  before writing anything to the database.
