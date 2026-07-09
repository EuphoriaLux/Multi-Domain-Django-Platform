# Crush Connect — Product Overview

**Status:** Living document — keep in sync with copy changes.
**Last updated:** 2026-07-02
**Related spec:** [Asymmetric Catalogue Transition (2026-06-09)](../superpowers/specs/2026-06-09-crush-connect-catalogue-transition.md)

This is the canonical inventory of the Crush Connect product family: what each
product is called, what it does, its status, and where it lives in code. Edit
copy anywhere in the Connect area? Check the naming rules (§9) first.

## 1. What Crush Connect is

Crush Connect is Crush.lu's online-first, coach-curated matching product.
Tagline: **"Curated, never swiped."** Voice pillars: human-curated,
privacy-first, Luxembourg (LuxID-verified, real people only).

The **two-level promise**:

1. **Headline (Premium):** one match a week, hand-picked by a personal Crush
   Coach who explains the pick and personally arranges the date when it's
   mutual.
2. **Secondary (Premium):** *Today's Drop* — up to three fresh, algorithmically
   curated faces every morning at 06:00. Never an endless feed.

Around both sits the **Read the Photo** mechanic (guess someone's 3 private
yes/no answers from their photo) and the **free "In the Mix" track** (anyone
LuxID-verified is discoverable without paying).

## 2. Product family

| Product (marketing) | UI label | Internal / code name | Status | Code entry point |
|---|---|---|---|---|
| Your Coach's Pick | "Your Coach's Pick" | coach pick | Live (M7) | `ConnectCoachPick`, `crush_lu/models/crush_connect.py` |
| Today's Drop | "Today's Drop" (subnav + H1) | daily drop | Live | `ConnectDailyDrop`; `get_or_create_daily_drop` in `crush_lu/services/crush_connect.py` |
| Read the Photo | "Read the Photo" | gate questions | Live (M8/M9) | `crush_lu/models/crush_connect_questions.py`; `submit_gate_answers` |
| Sparks | "Your Sparks" / "Sparks" (subnav) | curiosity spark | Live | `CuriositySpark`; `crush_lu/views_crush_connect.py` |
| In the Mix (free track) | "In the Mix" (subnav) / "You're in the mix." (H1) | candidate / catalogue | Live | `catalogue_status` view; `is_catalogue_eligible` |
| Crush Coach | "Crush Coach" / "Curation" (coach subnav) | coach | Live | `coach_connect_members`, `coach_connect_member` |

One-line descriptions (use these as the source of truth for blurbs):

- **Your Coach's Pick** — One match a week, hand-picked by your Crush Coach —
  who tells you why and arranges the date when it's mutual.
- **Today's Drop** — Up to three fresh faces every morning at 06:00, curated —
  never an endless feed.
- **Read the Photo** — Everyone picks 3 yes/no questions, answered privately;
  you guess from the photo — read someone right (2 of 3) and a Spark is sent.
- **Sparks** — People who read your photo well — read them back; mutual = your
  coach arranges the date. Passing is silent.
- **In the Mix** — Verify with LuxID, answer your 3 questions, and you're
  discoverable — free, forever.
- **Crush Coach** — The human behind every pick — curates, verifies, arranges
  dates.

### Experience landing pages

Each of the five experiences has its own member-facing explainer page at
`/crush-connect/experiences/<slug>/` (slugs: `coach-pick`, `todays-drop`,
`read-the-photo`, `sparks`, `in-the-mix`). View: `crush_connect_experience` +
`CONNECT_EXPERIENCES` registry in `crush_lu/views_crush_connect.py`; templates
in `crush_lu/templates/crush_lu/crush_connect/experiences/`. Deliberately
softer-gated than the live surfaces: any logged-in member may read them (no
onboarding required) once `candidate_access_open()` is true (staff bypass).
Each page: name + tagline + description, a 3-step "How it works", a privacy
note, a state-aware CTA (receiver / onboarded candidate / not onboarded), and
sibling cross-links (`experiences/_more_experiences.html`). Linked from the
teaser's "The five experiences" grid and the hub's "Get to know Crush
Connect" row.

## 3. The two tracks

| | Receiver (Premium) | Candidate ("In the Mix", free) |
|---|---|---|
| Requirements | Approved profile + assigned coach + onboarded | Approved profile + LuxID linked + onboarded |
| Gets | Coach Pick, Today's Drop, can send Sparks | Discoverable in pools, receives/answers Sparks, "How people read you" stats |
| Landing page | `/crush-connect/today/` (`crush_connect_home`) | `/crush-connect/catalogue/` (`crush_connect_catalogue_status`) |
| Eligibility code | `_user_is_connect_receiver_eligible` (`views_crush_connect.py`) | `_user_is_connect_candidate_eligible`; `is_catalogue_eligible` (service) |

Routing matrix (who lands where) — implemented by `_hub_access_blocker` /
`_connect_access_blocker` in `crush_lu/views_crush_connect.py` and the teaser
fast-path in `crush_lu/views_static.py` (`crush_connect_teaser`):

| User state | Lands on |
|---|---|
| Candidate track closed (`candidate_access_open()` false) or excluded by coach | Teaser `/crush-connect/` |
| Eligible, not onboarded | Onboarding wizard `/crush-connect/onboarding/` |
| Onboarded receiver | Hub → Today's Drop |
| Onboarded candidate | Hub → In the Mix |
| Guest | Teaser (marketing) |

During BETA the teaser fast-path redirects every approved + LuxID member off
the teaser, so the waitlist join lives on the **catalogue status page** as well
(`crush_connect/_waitlist_join.html`). Without that, a beta candidate clicking
"Discover Premium" would loop premium → teaser → catalogue with no way to join
the very waitlist the receiver gate reads.

Because the hub self-routes safely for every state, **all logged-in entry
points should target `crush_lu:crush_connect_hub`**; only guest marketing
surfaces link the teaser.

## 4. Mechanics

- **Today's Drop:** `DAILY_DROP_SIZE = 3`, seeded weighted pick, pinned per
  calendar day, unlocks 06:00 local. A live Coach Pick *replaces* the Drop as
  the hero card. (`services/crush_connect.py`)
- **Coach Picks:** coach browses a member's eligible pool, proposes ONE
  candidate with a personal note; member accepts ("Yes — arrange a date") or
  declines. Accept → coach contacts both to arrange the date.
- **Read the Photo:** `GATE_QUESTION_COUNT = 3`, `GATE_ALIGN_MIN = 2`,
  `WEEKLY_CATALOGUE_SIZE = 12`. Members pick 3 questions from a weekly-rotating
  catalogue (onboarding step 7, editable from Connect profile). Guessing ≥2/3
  correct = Spark sent (`submit_gate_answers` → `miss`/`sent`/`matched`).
  Misses are never revealed. Weekly rotation: `rotate_connect_questions`
  management command, triggered Mondays by an Azure Function via
  `/api/admin/rotate-connect-questions/`.
- **Sparks:** fused with the gate — answering someone's 3 questions IS the
  spark. First mover must have their own 3 questions set. Declines are silent.
  Mutual read = match → coach arranges the date.
- **Onboarding:** 7-step resumable wizard (`crush_lu/onboarding_connect.py`):
  Intention → Lifestyle → Languages & interests → Life basics → Family &
  future → Ideal match → Your 3 questions.

## 5. Safety & privacy invariants

Never weaken these in copy or code:

- First name and **age range** only — never full name or exact age.
- Photos stay **blurred until both sides have Sparked** (mutual read).
- `photo_share_consent` (membership flag): members onboarded under the old
  blurred model are not surfaced with a clear photo until they re-consent.
- `excluded_by_coach` panic flag removes a member from every pool immediately
  (audit: `excluded_by`, `excluded_at`, `exclusion_reason`).
- No public browsing, no search — a card is only shown inside a Drop, a Coach
  Pick, or a Spark.
- Owner answers to gate questions are private; only anonymous aggregates are
  shown ("How people read you").
- Leaving Crush Connect is always possible from settings.

## 6. Pricing & tiers

| Tier | Price | Includes |
|---|---|---|
| In the Mix | Free | LuxID verification, discoverable in pools, receive & answer Sparks, "How people read you" |
| Premium | **€15/month** | Personal Crush Coach, weekly Coach's Pick, daily Drop, dates arranged for you |

Pricing appears on: teaser pricing section, catalogue-status upsell,
`partials/_products.html` Premium card, dashboard upsell blurb.

## 7. Lifecycle / status

| Piece | Status | Notes |
|---|---|---|
| Coach Pick | Live | M7 |
| Today's Drop | Live | daily, 06:00 |
| Read the Photo + Sparks | Live | M8/M9 |
| In the Mix (candidate track) | Live | see catalogue-transition spec |
| Coach Curation UI | Live | `coach_connect_members` |
| 7-step onboarding | Live | step 7 = questions |
| **Story mechanic** | **Deprecated (M9)** | `_step7_story.html` unused (live flow uses `_step7_questions.html`); story fields remain on `CrushConnectMembership`; `dev_card_preview.html` still renders story rows (dev-only) |
| **Beta waitlist** | **Load-bearing again — do not remove** | `CrushConnectWaitlist.selected_as_tester` is the receiver gate during the BETA phase (`connect_phase.receiver_access_open`). The model, the `join_waitlist`/`waitlist_status` API (`crush_lu/api_crush_connect.py`) and the `crushConnectWaitlist` Alpine component are all live. Only the "20 testers" framing is retired — the count is no longer fixed |
| €10/month beta pricing | Retired | superseded by €15/month Premium |
| `CRUSH_CONNECT_LAUNCHED` flag | Active | full public launch; opens the receiver track to every Premium member |
| `CRUSH_CONNECT_CANDIDATE_OPEN` flag | Active | BETA phase: opens the candidate track only. Slot-sticky |
| `PREMIUM_REDIRECTS_TO_BETA` flag | Active | defaults **True** — Go-Premium funnels to the waitlist. Slot-sticky |

## 8. Entry-point map

| Surface | File | Target |
|---|---|---|
| Mobile bottom nav "Connect" | `partials/bottom_nav.html` | hub |
| Desktop/mobile user dropdowns | `base.html` (4 branches) | hub |
| Guest top nav / mobile nav / footer | `base.html` | teaser (guests — correct) |
| Dashboard Premium hero card | `dashboard.html` | hub |
| Dashboard LuxID prompt | `dashboard.html` | LuxID connect URL |
| Event attendees "Try Crush Connect" | `event_attendees.html` | hub |
| Connection-window-closed card | `_connection_window_closed.html` | hub |
| Profile edit "about Crush.lu" | `partials/edit_about_crushlu.html` | hub |
| Verification options Premium teaser | `partials/_verification_options.html` | `premium_choose_coach` |
| Tier comparison | `partials/_products.html` | `premium_choose_coach` |
| Coach nav "Coach Picks" | `base.html` | `coach_connect_members` |
| Teaser "The five experiences" grid | `crush_connect.html` | experience explainers (5×) |
| Hub "Get to know Crush Connect" row | `crush_connect/hub.html` | experience explainers (5×) |

Rule: logged-in surfaces → **hub** (it self-routes); guest surfaces → teaser.

## 9. Copy glossary & naming rules

- Page H1s use product names verbatim: "Your Coach's Pick" / "Today's Drop" /
  "You're in the mix." / "Your Sparks".
- Subnav short forms: Home · Today's Drop (receiver) / In the Mix (candidate) ·
  Sparks · Profile · Curation (coach).
- Marketing always presents the Coach Pick **before** the Drop and never merges
  them into "weekly matches".
- **Cadence rule:** "week/weekly" may only ever modify the Coach Pick;
  "today/daily/every morning" may only ever modify the Drop.
- "In the Mix" always carries "free" in marketing.
- **Banned vocabulary:** "beta", "tester", "waitlist" (Connect context),
  "Story" (deprecated mechanic), "people you've met at events" (stale claim —
  Connect is online-first).
