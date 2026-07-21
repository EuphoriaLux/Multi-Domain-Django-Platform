# Crush.lu Event Identity Redesign

**Date:** 2026-07-21

**Status:** Draft — product decisions proposed with recommended defaults,
pending Tom's confirmation (§12).

**Scope of this PR:** Planning only. No models, migrations, routes, templates,
JavaScript, tasks, or production behavior are changed by this document.

**Related documents:**
[Crush Connect — Product Overview](../../products/crush-connect.md),
["My Crush!" — Post-Event Connection Flow](2026-07-21-crush-my-crush-post-event-flow.md),
[Crush Connect Event Lobby & "People I've Met"](2026-07-17-crush-connect-event-lobby-design.md),
[Asymmetric Catalogue Transition](2026-06-09-crush-connect-catalogue-transition.md)

> **This spec was split.** It originally also covered reframing the legacy
> post-event `EventConnection` as "My Crush!". That is a coach-operations
> workflow with a different risk profile (SLA, coach capacity, conversion), so
> it now lives in its own spec:
> ["My Crush!" — Post-Event Connection Flow](2026-07-21-crush-my-crush-post-event-flow.md).
> This document is strictly the **profile / taxonomy** redesign.

## 1. Executive summary

Crush.lu now has two dating products with two different profile jobs:

* **Crush Connect** (online-first) owns the *rich personal profile*: 7-step
  onboarding, lifestyle, ideal match, gate questions. It is deliberately
  free-text-free since the Story mechanic was deprecated (M9).

* **The classic event profile** owns *verification + in-person material*:
  photos, contact details, and whatever helps people connect face-to-face at
  events.

Yet the classic profile still carries two online-dating-era free-text fields —
**bio** and **interests** — plus an under-used structured trait picker. This
spec replaces them with a single structured **"Your Event Identity"** section:
*what people will discover about you at events.* No free text anywhere.

## 2. Production data snapshot (2026-07-21)

Measured on production via Django shell. Drives every decision below.

**Funnel — 2,144 profiles:**

| Status     | Count | Share |
| ---------- | ----- | ----- |
| Incomplete | 1,078 | 50%   |
| Pending    | 506   | 24%   |
| Verified   | 522   | 24%   |
| Rejected   | 38    | 2%    |

**Fill rates:**

| Field                  | All profiles | Verified (n=522) |
| ---------------------- | ------------ | ---------------- |
| Photo 1                | 73%          | —                |
| Bio (free text)        | 27%          | **65%**          |
| Interests (free text)  | 25%          | **59%**          |
| Qualities (structured) | 7%           | 28%              |
| Defects (structured)   | 7%           | 25%              |

**Bio length:** avg 182 · median 140 · max 500 chars — members write 2–3
sentences, not essays. (`CrushProfile.bio` is `TextField(max_length=500)`,
`crush_lu/models/profiles.py:553`.)

**Interest content analysis (30-sample manual review):**

* Many entries are verbatim wizard category labels: `Sports & Fitness,
  Outdoors & Travel`, `Arts & Culture, Food & Wine, Outdoors & Travel,
  Business & Tech`. Root cause: the create-profile wizard's 5 category
  checkboxes (`create_profile.html` L656–712) are **UI-only** — their labels
  are flattened into the free-text `interests` string
  (`CrushProfile.interests`, help_text "comma-separated",
  `crush_lu/models/profiles.py:556`). These members already chose structure; a
  pick-list loses nothing for them.

* Free-form entries are keyword lists, not prose: `Yoga, traveling`,
  `Tanzen`, `soccer, music, books, Spain`. The same ~20 themes recur
  (hiking/randonnée/wandern, travel, music/concerts, cooking/gastronomie,
  sports/fitness, yoga, reading, cinema, photography).

* Content is written in 4–5 languages (FR/DE/EN/LU/AR) — untranslatable for
  event slides and coach review. A curated taxonomy translates cleanly via
  modeltranslation.

* A minority write mini-bios inside interests (`Marathonian brains, a cheeky
  smile…`) — self-expression demand that conversation starters must absorb.

**Conclusions:**

1. Bio is the most-loved field (65% of verified) but shallow (median 140
   chars) — structured prompts of similar length can replace it without
   losing expressiveness.
2. Interests are already semi-structured in practice; a curated taxonomy
   absorbs ~90% of observed content, and keyword migration is cheap.
3. Traits (28%/25%) suffer from UX/discoverability, not concept — fix the
   presentation, keep the model.

## 3. The two-product profile architecture

| <br />               | Classic event profile                               | Crush Connect profile                   |
| -------------------- | --------------------------------------------------- | --------------------------------------- |
| Product context      | In-person events                                    | Online-first matching                   |
| Job                  | Verify identity + arm face-to-face conversation     | Curated matching (coach + algorithm)    |
| Depth after redesign | Light: photos + Event Identity chips                | Rich: 7-step onboarding data            |
| Free text            | **None**                                            | None (Story deprecated M9)              |
| Seen by              | Coaches, event attendees (post-event), event slides | Matches (blurred until mutual), coaches |

Principle: **personal depth lives in Connect; the event profile stays light.**
A member who only attends events should complete their profile in under two
minutes; a Connect member invests in the full onboarding once.

## 4. Product decisions

| #  | Decision                                                                                                                                                                           | Status   |
| -- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| D1 | Remove all member-facing free text from the classic profile (bio, interests). No replacement free text is introduced.                                                            | Proposed |
| D2 | Merge "About You", "Your Personality" and "Event Preferences" into one **"Your Event Identity"** edit section.                                                                   | Proposed |
| D3 | Interests adopt the existing `ConnectInterest` curated taxonomy (translated, categorized, `is_active` retirement, `crush_lu/models/crush_connect.py:566`) via a new M2M on `CrushProfile`. One taxonomy serves both products. | Proposed |
| D4 | Bio is replaced by two structured elements: **"Ask me about"** topics and an **"Event vibe"** chip (§5.3).                                                                       | Proposed |
| D5 | Qualities/defects pick-lists stay (model unchanged) as **event-display data**; UX redesigned inside Event Identity. `sought_qualities`/`astro_enabled`/`first_step_preference` are **excluded** — trait matching reads the `CrushConnectMembership` copies (`crush_lu/matching.py` L12–19), so the profile copies are dead writes (§5.1). | Proposed |
| D6 | Legacy `bio`/`interests` columns are kept, deprecated, hidden from all member/attendee surfaces, and remain visible to **coaches only** as a read-only "legacy" block during transition. Sunset date decided later (§12, O6). | Proposed |
| D7 | Free-text interests are keyword-mapped into the taxonomy by a one-off migration command (dry-run report first); unmatched content is left in the legacy field only.               | Proposed |
| D8 | The create-profile wizard step 2 is rebuilt around Event Identity; bio/interests inputs removed.                                                                                 | Proposed |

## 5. "Your Event Identity" section design

Copy framing: *"This is what people will discover about you at our events —
no long bio needed."*

### 5.1 Your vibe (existing model, new UX)

* `qualities` (max 5) and `defects` (max 5) — `Trait` M2M, unchanged
  (`crush_lu/models/profiles.py:587`, `:594`).

* UX: chip grid grouped by `Trait.category`, searchable, with the selected
  chips pinned on top. Mobile-first, one screen.

* Completion nudge: submission requires ≥3 qualities and ≥1 defect only if Tom
  accepts the friction (O2); otherwise keep optional with a progress hint.
  Production shows 28%/25% fill — the redesign must beat this.

* **Not on this card:** `sought_qualities`, `astro_enabled` and
  `first_step_preference`. Trait matching is **Crush Connect-only** and reads
  the copies on `CrushConnectMembership`, not `CrushProfile` — see the
  `crush_lu/matching.py` module docstring (L12–19) and the
  `_member_match_data` adapter (L581–601). The `CrushProfile` copies of these
  three fields are legacy: they only seed Connect onboarding once
  (`views_crush_connect.py:459–461`) and have no live event-side surface.
  Editing them from Event Identity would write dead fields. They stay editable
  where they matter — the Connect profile section
  (`forms_crush_connect.py:202`). During Phase B, audit for any remaining live
  consumer of the profile copies before marking them deprecated alongside
  bio/interests.

* `qualities`/`defects` **do** stay on this card despite the same duplication:
  unlike the three fields above, the profile copies have live event-side
  consumers (coach screens `views_coach.py:1248`, and the §5.3 presentation
  slides will render "top qualities"). They are event-display data here, not
  matching inputs.

### 5.2 Your interests (shared taxonomy)

* New M2M `CrushProfile.interests_new → ConnectInterest` (working name;
  §9.1), min 3 / max 8, same chip-grid UX as Connect onboarding step 3.

* The taxonomy already covers the observed production themes — its
  `Category` choices are Sports, Music, Travel, Food & Drink, Arts & Culture,
  Outdoors, Games, Wellness (`crush_lu/models/crush_connect.py:577`). Audit
  against the §2 sample before migration (O3).

* No custom tags. A "Suggest an interest" link sends free text to admins only
  (never displayed) — feeds taxonomy curation.

* **Retired interests must not break saved profiles.** The Connect form's
  active-only queryset (`forms_crush_connect.py:130–135`) is the pattern to
  *avoid* copying verbatim: once an interest is retired
  (`is_active=False`), an active-only field would reject or silently drop a
  member's existing selection on their next save. The Event Identity form's
  queryset is **active choices ∪ the member's current selections**, with
  retired selections rendered as non-addable "legacy" chips (removable, not
  re-selectable). `ask_me_about` stays valid as long as the item is still in
  `interests_new`, retired or not. Test retirement → profile edit
  round-trip (§13).

* The wizard's 5 hardcoded UI-only checkboxes (sports/arts/food/outdoors/
  business) are removed; the taxonomy supersedes them.

### 5.3 Conversation starters (bio replacement)

Two fully structured elements, both translated and chip-based:

1. **"Ask me about…"** — pick up to 3 items *from your selected interests*.
   Rendered as chips on event slides and attendee cards. Zero new taxonomy: a
   small JSON list of `ConnectInterest` ids on the profile (§9.1).
2. **"My event vibe"** — pick 1 from a curated, translated list, e.g.:

   * "First one on the dance floor"
   * "Quiet corner conversations"
   * "Here to meet everyone"
   * "Dragged along by friends"
   * "I'll be at the bar — come say hi"

   Stored as a choice field. Final list is a copy decision (O4).

Rationale: production bios are median 140 chars of light self-description; the
event presentation slide currently quotes the bio verbatim
(`coach_presentation_control.html` L86–88) and needs *something* to show. Vibe
+ ask-me-about fills that slide with personality while staying structured,
moderated-by-design, and translated. **This is the biggest member-facing bet
in the spec** — bio is the most-loved field (65% of verified), so the
replacement must feel alive on the slide, not like a downgrade.

### 5.4 Event languages (moved, unchanged)

`event_languages` (`CrushProfile.event_languages`, JSONField,
`profiles.py:653`) moves from the dissolved "Event Preferences" section into
Event Identity ("How you'll talk at events"). Model and event-gating logic
unchanged.

### 5.5 Resulting Edit Profile information architecture

Before: Photos · About You · Your Personality · Contact & Location · Event
Preferences · Privacy · Crush Connect Profile · Account · About Crush.lu

After: **Photos · Your Event Identity · Contact & Location · Privacy ·
Crush Connect Profile · Account · About Crush.lu**

The Event Identity card subtitle: "Vibe, interests & conversation starters —
what people discover at events."

## 6. What happens to bio and interests

1. `bio` and `interests` columns remain on `CrushProfile` (no destructive
   migration).
2. Both fields are removed from every form, wizard step, autosave endpoint,
   and member/attendee-facing API payload — **except the authenticated
   data-portability export** (`views_account.export_user_data`,
   `crush_lu/views_account.py:1268–1269`), which keeps returning the retained
   columns until the O6 hard delete: while the data exists and coaches can
   read it, members must be able to download it.
3. Both fields stop rendering on all member-facing and attendee-facing
   surfaces (§7).
4. Coach-facing screens keep a collapsed read-only **"Legacy bio (pre-2026
   redesign)"** block so coaches lose no context on existing members during
   transition.
5. A later retention decision (O6) sets the hard-delete date, per the
   privacy-by-design posture in the lobby spec §13.

## 7. Surface-by-surface changes

| Surface                                                                                                                                | Today                                                           | After                                                                               |
| -------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `create_profile.html` wizard step 2                                                                                                    | bio textarea, interests textarea, 5 UI-only category checkboxes | Event Identity flow: vibe chips → interests chips → ask-me-about + event vibe        |
| Wizard review step (L1080)                                                                                                             | bio excerpt                                                     | Event Identity summary chips                                                        |
| `edit_profile.html` cards                                                                                                              | About You / Your Personality / Event Preferences cards          | single Your Event Identity card                                                     |
| `partials/edit_about.html`, `edit_traits.html`, `edit_event_languages.html`                                                            | three partials                                                  | replaced by `partials/edit_event_identity.html` (one autosaving form, sub-sections) |
| `coach_presentation_control.html`                                                                                                      | quotes bio on the live slide                                    | event vibe + ask-me-about chips + top qualities                                     |
| `event_presentations.html`                                                                                                             | (check during impl.)                                            | same chip rendering                                                                 |
| `event_attendees.html` (own + attendee)                                                                                                | interests free-text snippet                                     | up to 3 interest chips                                                              |
| `coach_review_profile.html`, `coach_member_overview.html`, `coach_verification_channel.html`, `crush_connect/coach_member_detail.html` | bio/interests display                                           | Event Identity + collapsed legacy bio block                                         |
| `crush_connect/_drop_card.html` fallback                                                                                               | `target_profile.interests\|split_interests`                     | taxonomy chips from new M2M                                                         |
| `crush_connect/dev_card_preview.html`                                                                                                  | interests row                                                   | updated                                                                             |
| Check-in toast (`views_checkin.py` `_get_profile_data` L412–428 → rendered by `alpine-components.js`)                                   | JSON ships `profile.interests` free text to event staff         | taxonomy chip labels (or drop the key) — a **JSON producer** the template grep cannot catch |
| `partials/edit_profile_form.html` (legacy partial)                                                                                     | bio/interests inputs                                            | removed/retired                                                                     |

> **Connection-flow surfaces** (`request_connection.html`,
> `connection_detail.html`) also drop bio/interests, but their redesign is
> owned by the ["My Crush!" spec](2026-07-21-crush-my-crush-post-event-flow.md)
> §7 — they change copy *and* mechanics there. This spec only guarantees no
> `bio`/`interests` free text renders on them.

## 8. Model & migration plan

### 8.1 Model changes (proposals; implementation may refine names)

* `CrushProfile.interests_new` — M2M to `crush_lu.ConnectInterest`,
  blank=True. Reuses the existing curated/translated taxonomy
  (`crush_lu/models/crush_connect.py:566`; the model docstring notes it
  "mirrors `Trait`/`SparkPrompt`"); no new taxonomy is created. *(Naming: O5 —
  consider renaming the model to a neutral `Interest` since it becomes
  cross-product.)*

* `CrushProfile.ask_me_about` — JSONField, list of up to 3 `ConnectInterest`
  ids, each required to be in `interests_new`.

* `CrushProfile.event_vibe` — CharField choices, blank (list TBD, O4).

* No changes to `qualities`/`defects`/`sought_qualities`, `event_languages`,
  or any Connect model.

* `bio`, `interests` fields untouched at DB level; help\_text marked
  DEPRECATED.

### 8.2 Data migration command

`migrate_interests_to_taxonomy` (one-off management command):

1. `--dry-run` mode prints per-keyword match report against production data
   (run via Azure SSH shell, same access pattern as the §2 measurement).
2. Keyword map covers the observed corpus, case-insensitive, accent-folded,
   across FR/DE/EN/LU, e.g.: `hiking|randonnée|randonnee|wandern|wanderung`
   → Outdoors/Hiking; `yoga` → Wellness/Yoga; `cinéma|kino|cinema|film` →
   Arts/Cinema; `cuisine|cooking|kochen|backen|baking` → Food/Cooking;
   `voyage|travel|reisen` → Travel; etc. Full map written from the dry-run
   report before execution.
3. Matched ids populate `interests_new`; nothing is deleted from legacy
   fields.
4. Report matched/unmatched counts; unmatched stay coach-visible via the
   legacy block only.

### 8.3 Forms

* New `CrushProfileEventIdentityForm` (qualities, defects, interests\_new,
  ask\_me\_about, event\_vibe, event\_languages) with the existing autosave
  convention (`profileSectionAutosave` + `api_profile_settings_autosave`).
  `sought_qualities`/`astro_enabled`/`first_step_preference` are excluded —
  they are Connect-membership matching inputs, not event-profile data (§5.1).

* `CrushProfileAboutForm` and `CrushProfileEventPrefsForm` retired;
  `CrushProfileTraitsForm` folded into the new form.

* `CrushProfileForm` (wizard): bio/interests fields removed; step 2
  re-pointed at the structured fields.

## 9. Translations

All new copy (section title, framing line, vibe list, ask-me-about prompt,
chip UX, coach "legacy bio" label) requires EN/DE/FR before launch.
`ConnectInterest.label` and `Trait.label` are already modeltranslated.

## 10. Delivery phases

* **Phase A — data readiness:** taxonomy audit vs §2 sample; dry-run keyword
  map on production; report reviewed by Tom.

* **Phase B — models & migration:** §8.1 fields, admin, migration command,
  model/service tests.

* **Phase C — member UI:** Event Identity edit section, edit-profile IA,
  create-profile wizard step 2 + review, autosave wiring.

* **Phase D — surfaces:** §7 table (presentations, attendees, coach screens,
  drop card fallback); legacy bio collapsed block.

* **Phase E — launch readiness:** translations, accessibility, dark/light,
  mobile widths.

The ["My Crush!" spec](2026-07-21-crush-my-crush-post-event-flow.md) owns the
connection-flow phases; it can ship before or after C–D.

## 11. Open decision points (for Tom)

| #  | Question                                                                             | Recommended default                                                                                       |
| -- | ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| O1 | Confirm D1: truly zero free text (incl. no "custom interest tag")?                   | **Yes** — Connect already set the precedent (curated, no moderation burden).                               |
| O2 | Minimum vibe requirements at submission?                                             | **Optional + nudge, not required.** The funnel already loses 50% at incomplete and 54% never finish inscription — do not add a submission gate now. |
| O3 | Taxonomy gaps from the production sample (e.g. Photography, Ski, Languages)?          | These are **sub-interests inside existing categories**, not missing categories — `ConnectInterest.Category` already has the 8 the spec names. Add the specific interests before migration; spoken languages are already handled by `event_languages`. |
| O4 | Final "event vibe" chip list + copy.                                                 | Copy review, EN/DE/FR. Ship the §5.3 starter list unless Tom rewrites it.                                  |
| O5 | Rename `ConnectInterest` → neutral `Interest` now that it's cross-product?           | **Yes, rename now** — it's cross-product the moment the M2M ships; renaming after migration history locks in is the expensive path. |
| O6 | Legacy bio/interests hard-delete date.                                               | **~6 months post-launch**, aligned with the lobby spec's privacy-by-design posture (§13 there).            |

## 12. Non-goals

* No change to the connection flow, `EventConnection` statuses, or coach
  workflow — those live in the
  ["My Crush!" spec](2026-07-21-crush-my-crush-post-event-flow.md).

* No change to Event Lobby mechanics, eligibility, or anonymity rules.

* No chat or messaging anywhere (lobby spec §3 stands).

* No Connect onboarding changes.

## 13. Test plan (implementation PRs)

* Migration command: dry-run accuracy on fixtures, idempotency, accent/case
  folding, no writes to legacy fields.

* Form: min/max selections, ask-me-about ⊆ interests\_new, autosave contract
  unchanged; retiring a selected interest (`is_active=False`) neither rejects
  nor silently drops it on the member's next save, and `ask_me_about`
  entries pointing at a retired-but-still-selected interest stay valid.

* Wizard: step 2 completes without bio/interests; draft\_data round-trip;
  review step renders chips.

* Surfaces: no member/attendee template renders `profile.bio` /
  `profile.interests` outside the coach legacy block. Implement as a CI grep
  guard **scoped to CrushProfile expressions** — match `profile.bio`,
  `profile.interests`, known legacy context variables, and `|split_interests`
  in `crush_lu/templates/crush_lu/**`, with the one coach legacy-bio partial
  whitelisted. Do **not** match bare `.bio` / `.interests`: `coach.bio`
  (`CrushCoach.bio` — dashboard, `profile_submitted.html`,
  `premium/choose_coach.html`, `onboarding/meet_coach.html`) and
  `membership.interests` (the structured `ConnectInterest` M2M —
  `catalogue_status.html`, `coach_member_detail.html`, `_drop_card.html`) are
  legitimate surfaces that must keep working. The guard is **template-only**
  — JSON producers need their own coverage: test that
  `_get_profile_data` (`views_checkin.py:412–428`) no longer returns legacy
  free-text interests to the check-in toast, and that the data-portability
  export (§6) still does.

* i18n: every new string translated EN/DE/FR; vibe list renders in all
  locales.

## 14. Acceptance criteria

* Zero free-text profile inputs outside Crush Connect (and none inside it).

* A member can complete the event profile end-to-end without typing a
  paragraph.

* Event presentation slides and attendee cards show only structured Event
  Identity content.

* Existing members' legacy bio remains visible to coaches during transition.

* Production interests are ≥85% taxonomy-mapped after the migration command
  (per dry-run report target set in Phase A).
