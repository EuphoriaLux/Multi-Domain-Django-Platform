# Crush Connect

Current product reference. Historical rollout specs under `docs/superpowers/specs/`
describe earlier experiments and are not the source of truth for the live flow.

## Product promise

Crush Connect is deliberate discovery for verified members, with a separate
human-curated Premium layer. It is not an endless feed and completing a card
does not automatically create a match.

## Current experiences

| Experience | What the member gets | Primary implementation |
| --- | --- | --- |
| In the Mix | Verified, consented catalogue membership | `CrushConnectMembership`; `/crush-connect/catalogue/` |
| Connect Week | Up to three cards per day for seven days, then one-or-none weekly request | `ConnectWeekSession`, `ConnectCycleCard`, `ConnectWeeklyRequest`; `/crush-connect/week/` |
| Read the Photo | Three private yes/no guesses on a Connect Week card | `ConnectCycleCard.answers_json` |
| Your Coach's Pick | One candidate selected personally for an active Premium member | `ConnectCoachPick`; `/crush-connect/coach-pick/` |

## Read-the-Photo privacy contract

- The profile owner selects three questions and records their own answers.
- A Connect Week participant guesses the answers while completing a card.
- Guesses remain on the completed `ConnectCycleCard`; no matching side effect is
  triggered.
- The catalogue status page shows only anonymous totals, for example
  “3 of 5 people answered Yes.”
- It never shows the correct answer, individual voters, or per-voter responses.
- Incomplete cards and questions that are no longer among the owner's current
  three are excluded from the totals.

## Access

- `candidate_access_open()` gates onboarding, the Mix, catalogue, and profile.
- `cycle_access_open(user)` gates Connect Week. During beta it admits staff,
  selected testers, and event-verified members; full launch opens it to every
  otherwise eligible member.
- Connect onboarding requires an approved profile, verified identity (LuxID or
  coach-verified event attendance), a photo, and explicit photo-sharing consent.
- Premium requires an active `PremiumMembership`; coach assignment alone is not
  an entitlement.

## Weekly request flow

Completing the seven-day cycle opens a short review. The member may send one
request or send none. A recipient can accept or silently decline within the
response window. Acceptance opens the temporary-chat and coffee-date flow.

## Premium flow

The Premium product is human curation. The assigned Crush Coach browses the
member's eligible pool, proposes a `ConnectCoachPick`, and can include a personal
note. The member accepts or declines; acceptance tells the coach to confirm the
candidate's interest and arrange the date. There is no automated fallback match.

## Retired legacy experiment

The earlier algorithmic Daily Drop / Curiosity Spark experiment was retired in
August 2026 because production had no completed reads and Connect Week became the
active deliberate-discovery workflow. Its runtime routes, services, templates,
admin surfaces, notifications, and the `ConnectDailyDrop`, `CuriositySpark`, and
`ConnectQuestionAnswer` models were removed. The separate classic post-event
`CrushSpark` Wonderland journey remains a different product and is unaffected.

Old `/crush-connect/today/` bookmarks redirect to the Connect hub.

## Naming rules

- Use “Connect Week”, “In the Mix”, “Read the Photo”, and “Your Coach's Pick”.
- Do not advertise Daily Drop or Curiosity Sparks as Crush Connect features.
- “SparkPrompt” is a legacy model name for onboarding story prompts; avoid
  exposing that implementation name in member-facing copy.
