# Crush.lu — Mobile-First UX Improvement Workflow

> **What this document is**
> 1. A **repeatable workflow** for finding mobile UX improvement areas on Crush.lu (Apple + Android, mobile-first).
> 2. An **applied audit** of two flagship pages and the navigation menu, run through that workflow:
>    - Account Settings — `https://crush.lu/de/account/settings/` → `crush_lu/templates/crush_lu/account_settings.html`
>    - Edit Profile — `https://crush.lu/de/profile/edit/` → `crush_lu/templates/crush_lu/edit_profile.html`
>    - The mobile menu — `crush_lu/templates/crush_lu/partials/bottom_nav.html` + `partials/top_bar_mobile.html`
> 3. A **prioritized roadmap** with concrete file references, effort, and impact.
>
> **North-star for this pass:** the two things a member should never struggle to do on a phone are **finding a match** and reaching **Crush Connect**. Everything below is weighed against that.
>
> Companion: [`crush-lu-review-issues.md`](./crush-lu-review-issues.md) (prior code-review findings — bugs/security).

---

## Part 1 — The Workflow (repeatable process)

A lightweight loop you can re-run for any screen. It produces a scored findings list and a fix/verify cycle. One pass ≈ half a day per surface.

```
0. Frame  →  1. Devices  →  2. Capture  →  3. Heuristic sweep  →  4. Score
                                                                      │
        7. Validate  ←  6. Fix & re-test  ←  5. Log findings  ←───────┘
```

### Step 0 — Frame the surface

Before opening DevTools, write down:

| Field | Example for Settings |
|-------|----------------------|
| Primary job-to-be-done | "Manage how I log in and what notifications I get" |
| Persona / state | New social-only user; approved member; coach |
| Success metric | Time-to-toggle a setting; % who reach matching after editing profile |
| Entry points | Bottom-nav **Profile** → Account; standalone `/account/settings/` |
| Must-never-bury actions | Edit profile, find a match, open Crush Connect |

### Step 1 — Device & viewport matrix

Mobile-first means we test the **smallest realistic phone first**, then scale up. Minimum matrix:

| Class | Device | Logical width | Notes |
|-------|--------|---------------|-------|
| Small iOS | iPhone SE (2/3rd gen) | 375 px | Tightest layout; catches truncation |
| Modern iOS | iPhone 15 / 15 Pro | 393 px | Dynamic Island + home-indicator safe areas |
| Large iOS | iPhone 15 Pro Max | 430 px | One-handed reach ceiling |
| Small Android | Galaxy S (compact) | 360 px | Smallest common Android |
| Modern Android | Pixel 7/8 | 412 px | Gesture nav, theme-color |
| Edge cases | Foldable inner / 320 px | 280–344 px | Galaxy Fold, accessibility zoom |

For each: **light + dark**, **portrait**, and a **throttled "Slow 4G / 4× CPU"** run. Always test the **installed PWA** (Add to Home Screen), not just the browser tab — standalone mode changes safe-area and chrome behaviour.

### Step 2 — Capture

| Tool | What it gives you | Where |
|------|-------------------|-------|
| Chrome DevTools device mode | Layout, reach, overflow, touch overlays | local |
| Real device (iOS Safari / Android Chrome) as PWA | True safe-area, keyboard, momentum | staging |
| Lighthouse (mobile preset) | Perf, a11y, best-practices, PWA score | CI or local |
| `axe` DevTools / Lighthouse a11y | Contrast, roles, labels, focus order | local |
| Playwright screenshots | Visual regression baseline | `crush_lu/tests/screenshots/` |

There is already a mobile screenshot/test harness to build on: `crush_lu/tests/test_mobile_screenshots.py` and `crush_lu/tests/test_mobile_ui.py`. Capture a screenshot per device/theme **before** any change so fixes are diffable.

### Step 3 — Heuristic sweep (the checklist)

Walk the screen against this checklist. Each unchecked box is a candidate finding.

**A. Reachability & navigation**
- [ ] Primary actions sit in the bottom **thumb zone**, not the top corners.
- [ ] ≤ 5 destinations in the tab bar (Apple HIG ≤ 5; Material 3–5).
- [ ] The current location is shown (active tab / page title).
- [ ] Back behaviour is predictable (gesture + on-screen) and never dead-ends.
- [ ] The flagship action (find a match / Crush Connect) is visually distinct, not "one of N equal tabs".

**B. Information architecture**
- [ ] Content is grouped and ordered by frequency of use, not by data model.
- [ ] No single endless scroll where a sectioned list / drill-down would do.
- [ ] One canonical home per task (no duplicated screens that can drift).
- [ ] Each row/section hints at its **current value** (iOS-settings convention).

**C. Touch & forms**
- [ ] Every interactive target ≥ **44×44 px** (iOS) / **48×48 dp** (Android).
- [ ] ≥ 8 px spacing between adjacent targets.
- [ ] Native input types + `inputmode` + `autocomplete` (email, tel, etc.).
- [ ] Labels are tappable; toggles have a large hit area.
- [ ] The keyboard never hides the field being edited or the submit button.
- [ ] Validation is inline, specific, and scrolls into view.

**D. Feedback & state**
- [ ] Tap feedback within 100 ms (active state / ripple).
- [ ] Async actions show loading / skeleton, then success/error.
- [ ] No layout shift (CLS) when content loads.

**E. Performance**
- [ ] Above-the-fold renders fast on Slow 4G; no render-blocking CDN for core flows.
- [ ] Images lazy-loaded and correctly sized.

**F. Platform feel & accessibility**
- [ ] Safe-area insets respected (notch/Island top, home-indicator bottom).
- [ ] Dark mode complete (no white flashes, adequate contrast).
- [ ] `prefers-reduced-motion` honoured.
- [ ] Visible focus state; logical focus order; semantic landmarks.
- [ ] Contrast ≥ 4.5:1 for text, 3:1 for UI/large text.

### Step 4 — Score & prioritize

Score each finding with a simple, defensible rubric:

```
Priority = Impact (1–3) × Reach (1–3)  /  Effort (1–3)
```

- **Impact** — how much it helps the user finish the job (3 = blocks/confuses).
- **Reach** — share of sessions affected (3 = everyone, every visit).
- **Effort** — dev cost (1 = CSS-only, 3 = template/IA refactor).

Bucket the result:

| Bucket | Meaning |
|--------|---------|
| **P0** | Broken / blocks the core job — fix now |
| **P1** | High impact, low/medium effort — this sprint |
| **P2** | Worthwhile, larger effort — plan it |
| **P3** | Polish — opportunistic |

### Step 5 — Log findings (template)

```markdown
### [ID] Short title
- Surface: <page / partial>  ·  File: <path:line>
- Priority: P? (Impact ?, Reach ?, Effort ?)
- Heuristic: <A–F + item>
- Observed: <what happens on which device>
- Recommendation: <concrete change>
- Verify: <how we'll confirm it's fixed>
```

### Step 6 — Fix & re-test

Branch → implement the smallest change that satisfies the recommendation → re-run the **same** device matrix → update the screenshot baseline in `crush_lu/tests/screenshots/`. Component styles live in `tailwind-src/crush_lu/tailwind-input.css` and are rebuilt into `crush_lu/static/crush_lu/css/tailwind.css` — **never hand-edit the compiled file** (it's generated). Note the project runs a **CSP Alpine build**, so new interactivity must use the CSP-safe pattern (named Alpine components in `alpine-components.js`, `nonce="{{ csp_nonce }}"` on inline `<script>`), as seen in `bottom_nav.html`/`top_bar_mobile.html`.

### Step 7 — Validate

A finding is "done" when:
- Its **Verify** line passes on the **smallest** device in the matrix (375/360 px) and on a large device.
- Lighthouse mobile a11y/best-practices did not regress.
- The relevant Playwright screenshot diff is reviewed and re-baselined.

> **Optional automation (appendix B):** wire a `lighthouse-ci` + `axe` GitHub Action against `/de/account/settings/` and `/de/profile/edit/` in a mobile viewport so regressions in tap-target/contrast/perf are caught per-PR. Not built here.

---

## Part 2 — Applied audit

Run on `2026-05-30` against branch `claude/peaceful-archimedes-LTJh3`. Findings are grounded in the actual templates and the component CSS in `tailwind-src/crush_lu/tailwind-input.css` (line numbers refer to that source file).

### 2.0 What's already good (keep these)

The mobile foundation is genuinely solid — this audit is about levelling up, not rescuing:

- **Correct primary pattern:** a fixed **bottom nav** (thumb-reachable, `min-height: 56px`, `tailwind-input.css:11035-11049`) plus a sticky **slim top bar** (`:11119-11133`), both hidden at `lg:` so desktop is untouched.
- **Safe areas handled:** `padding-bottom: env(safe-area-inset-bottom)` on the bottom bar (`:11046`), `padding-top: env(safe-area-inset-top)` on the top bar (`:11130`), and content clearance via `main.mobile-main.has-bottom-nav { padding-bottom: 5rem }` (`:11024-11026`).
- **Good base touch targets:** `.bottom-nav-item` already declares `min-height: 44px; min-width: 44px` (`:11070-11071`), and `.top-bar-mobile-back` is 44×44 (`:11165-11166`).
- **Native touches:** `viewport-fit=cover`, `-webkit-tap-highlight-color: transparent`, `overscroll-behavior` tuned to avoid pull-to-refresh conflicts (`:11015-11021`), and a `:active { transform: scale(0.98) }` press feedback on cards (`.card-tappable:active` `:11223`, `.section-edit-card:active` `:11715`). Full dark-mode variants throughout.
- **Edit Profile already uses the right IA:** an iOS-settings-style **card list that drills into sections** via `?section=…` + HTMX (`edit_profile.html:86-197`). This is exactly the pattern Settings should adopt.

### 2.1 Menu — is it in the right place?

**Verdict: the *placement* is right, the *priority* is wrong.** A bottom tab bar is the correct, conventional home for primary navigation on both platforms, and it's implemented well. The problems are (a) too many tabs and (b) the flagship "find a match / Crush Connect" experience is rendered as just another equal tab.

#### [M1] Tab bar can exceed the 5-item ceiling — P1 (I3 R2 E2)
- File: `partials/bottom_nav.html:1-90`
- Observed: tabs are Home, **Coach** (coaches), Events, Connections, **Crush Connect** (launched/onboarded/staff), Profile. A coach with Crush Connect visible sees **6 tabs**; labels render at `font-size: 10px` (`tailwind-input.css:11074`) and crowd/truncate at 360–375 px.
- Recommendation: cap visible tabs at **5**. Options: fold **Coach** into the Profile/avatar menu (it's a role tool, not a daily destination), or introduce a **center "Discover" action** (see M2) and let secondary items live behind it.
- Verify: at 360 px, no label truncates; ≤ 5 tabs for every role.

#### [M2] Crush Connect / "find a match" is not the standout — P1 (I3 R3 E2)
- Files: `partials/bottom_nav.html:65-76`; visibility filter `templatetags/crush_connect_tags.py:36` (`crush_connect_nav_visible`).
- Observed: Crush Connect — the flagship matching surface — is a plain tab, only shown when `CRUSH_CONNECT_LAUNCHED`/onboarded/staff. "Finding a match" as a *job* is split across **Events** (meet), **Connections** (post-event), and **Crush Connect** (daily drop) with no single, obvious entry. Mainstream dating apps (Tinder/Hinge/Bumble) give the core discover/match action a **distinct center position**, not parity with Settings-level items.
- Recommendation: elevate "find a match" to a **visually distinct primary action** — e.g. an accent-filled **center tab or raised pill/FAB** in the bottom bar ("Connect" / "Today"), with the other tabs as supporting destinations. Frame the member's mental model as **Discover → Events → Matches**.
- Verify: 5-second test — a new user can point to "where I find a match" without scrolling; the action is colour/elevation-distinct from neighbours.

#### [M3] Crush Connect tab is missing its active-state + tap binding — P2 (I2 R2 E1) — *concrete bug*
- File: `partials/bottom_nav.html:67-75`
- Observed: every other tab has both `x-bind:class="…ActiveClass"` **and** `@click="handleTap"`; the Crush Connect `<a>` has **neither**. So on the Crush Connect page no tab highlights as active, and the shared tap/transition handler doesn't fire for it.
- Recommendation: add a `connectActiveClass` getter to the `bottomNav` Alpine component (in `alpine-components.js`) and the `@click="handleTap"` handler, matching the other tabs. This becomes more important once M2 elevates it.
- Verify: navigating to Crush Connect highlights its tab; tap transition matches siblings.

#### [M4] Settings has no place in the menu and two competing homes — P1 (I2 R3 E2)
- Files: standalone `account_settings.html`; profile sub-section `partials/edit_account_settings.html` (reached via `edit_profile.html:165` → `?section=account`).
- Observed: Account settings is reachable both at `/account/settings/` **and** inside Edit Profile's "Account" section, and the two share near-identical "Account Information" + "Linked Accounts" cards (compare `account_settings.html:13-112` with `edit_account_settings.html:3-112`). Two sources of truth → drift + "where do I change this?" confusion. There is no direct menu affordance for settings at all.
- Recommendation: pick **one** canonical Settings home (recommend: a single Settings screen reached from the Profile tab/avatar), and have the other route redirect or `{% include %}` the same partial so content can't diverge.
- Verify: one screen owns each setting; both URLs render identical, deduplicated content.

#### [M5] Notification bell points at Connections, not notifications — P2 (I2 R2 E1)
- File: `top_bar_mobile.html:37` — the bell links to `crush_lu:my_connections`, while a dedicated `crush_lu:notifications` page exists (`urls.py:339`).
- Observed: the bell icon (universally "notifications") routes to the Connections list and badges off `pending_requests_count`. Mismatched signifier.
- Recommendation: route the bell to the notifications page (or relabel/repurpose the icon). Confirm intended behaviour with product.
- Verify: the bell opens what its icon promises.

#### [M6] Two sub-44px / tiny targets in the bars — P2 (I2 R3 E1)
- Files: the top-bar bell is `width: 36px; height: 36px` (`tailwind-input.css:11189-11198`); tab labels are 10 px (`:11074`).
- Note: the tab items and the back button are already 44×44 — this finding is specifically the **36px bell** and the **10px label legibility**, not the whole bar.
- Recommendation: grow the bell hit area to ≥ 44×44 (the visual icon can stay 24); consider 11 px labels or leaning on icon emphasis for legibility.
- Verify: every bar target ≥ 44×44 in the device-toolbar overlay; labels legible at 360 px.

### 2.2 Account Settings — `/de/account/settings/`

#### [S1] One long monolithic scroll — P1 (I3 R3 E2)
- File: `account_settings.html` (~1018 lines): nine fully-expanded `rounded-xl shadow-lg` cards stacked — Account Info, Linked Accounts, Social Photos, Password, Profile Link, Email, WhatsApp, Push, Coach Push.
- Observed: at 375 px this is a very long, undifferentiated scroll; finding "turn off event emails" means hunting past login/photo cards.
- Recommendation: reuse the **Edit-Profile pattern** — a compact iOS-settings **list that drills into sub-screens** (Login & security, Notifications, Privacy, Danger zone), or at minimum collapse cards into grouped, accordion sections ordered by frequency of use (Notifications first for existing members).
- Verify: any single setting reachable in ≤ 2 taps; first screen fits ~1.5 viewports at 375 px.

#### [S2] Duplicated cards with Edit-Profile → drift risk — P1 (I2 R3 E2)
- See [M4]. "Account Information" + "Linked Accounts" exist in two templates with copy-pasted markup (including the full provider SVG blocks). Any change must be made twice.
- Recommendation: extract one shared partial each (e.g. `partials/_linked_accounts.html`, `partials/_account_info.html`) and `{% include %}` in both places.
- Verify: editing the partial updates both surfaces; no duplicated provider SVG markup.

#### [S3] Desktop-weight card chrome on mobile — P3 (I1 R3 E1)
- Files: repeated `p-6` / `px-6 py-4` (24 px) padding + `shadow-lg` + `rounded-xl` per card.
- Observed: heavy padding eats horizontal space at 360–375 px; large drop shadows read as "web", not "app".
- Recommendation: lighter, near-edge-to-edge grouped lists on mobile (smaller radius, hairline separators, subtle shadow); keep the rich cards at `sm:`+.
- Verify: more content above the fold; visually closer to native settings.

#### [S4] Small tap targets among the controls — P2 (I2 R3 E1)
- Files: the "Manage" email link is `text-xs` (`account_settings.html:25`); provider connect buttons are `px-4 py-2` (~36 px tall, `:119+`); disconnect is `px-4 py-2.5` (borderline, `:101`).
- Recommendation: raise links/buttons to ≥ 44 px tall with adequate spacing; make whole rows tappable where practical.
- Verify: target overlay shows ≥ 44×44 for every control.

#### [S5] Redundant heading vs. top-bar title — P3 (I1 R2 E1)
- Files: `account_settings.html:11` renders an `h1` "Account Settings" while the mobile top bar already shows the page title "Settings" (set via `{% block mobile_page_title %}` at `account_settings.html:5`).
- Recommendation: on mobile, demote/hide the large in-page `h1` (it duplicates the bar) to reclaim vertical space.
- Verify: no double title stacked at the top on 375 px.

### 2.3 Edit Profile — `/de/profile/edit/`

#### [P1] Strong base — extend, don't rebuild — (keep)
- File: `edit_profile.html:86-197`. The section-card + drill-down + HTMX model is the template for fixing Settings (S1).

#### [P2] Rows don't show current value — P2 (I2 R3 E2)
- File: `edit_profile.html:97-103,148-161`. The Photos row toggles between two strings, but Privacy/Preferences/Account subtitles are static ("Name visibility, age, photo blur") and don't reflect actual state.
- Recommendation: follow the iOS convention — show the current value as the row's secondary/right-side text (e.g. Privacy → "Name hidden · Age shown"). Turns the list into a status dashboard and reduces needless drill-ins.
- Verify: each row communicates its state without opening it.

#### [P3] Tie profile completion to "finding a match" — P2 (I3 R3 E2)
- Files: incomplete banner `edit_profile.html:50-76`; completion data via `profile.get_missing_fields`.
- Observed: an incomplete profile is what blocks matching, but the page frames it as a neutral amber warning, not as the gateway to the north-star job.
- Recommendation: convert the banner into a motivating progress affordance ("2 steps from being matched") with a primary CTA that routes toward Crush Connect / matching once complete. Directly serves the standout goal.
- Verify: from Edit Profile, the path to "get matched" is one obvious tap.

#### [P4] Sticky action bar collides with the bottom nav (and the keyboard) — P2 (I2 R2 E2)
- Files: `.action-buttons-sticky` is defined in the hand-authored `crush_lu/static/crush_lu/css/components/profile-creation.css:943-955` (a `@media (max-width: 767px)` block — *not* in `tailwind-input.css`); applied via `action-buttons-container action-buttons-sticky` on every edit section, e.g. `partials/edit_account_settings.html:332`, `partials/edit_about.html:375`, `partials/edit_photos.html:32`.
- Observed: the rule is `position: sticky; bottom: 0; z-index: 20` with a top `box-shadow`. The fixed bottom nav also sits at `bottom: 0` but at `z-index: 50` (`tailwind-input.css:11036-11040`), so on mobile the nav renders **on top of / flush against** the sticky save bar. With a field focused, the on-screen keyboard compounds it — submit/back can be crowded or obscured on short viewports.
- Recommendation: lift the sticky bar above the nav — `bottom: calc(56px + env(safe-area-inset-bottom))` (the nav's `min-height`, `:11048`) — and/or hide the bottom nav while a field in the section is focused. Add safe-area padding so it clears the home indicator.
- Verify: with the keyboard up at 375/360 px, the save bar sits fully above the bottom nav and both buttons are tappable.

#### [P5] Core form lib loaded from public CDN — P3 (I1 R2 E2)
- Files: `edit_profile.html:36-37,207` load `intl-tel-input` CSS/JS (and `:305` its `utils.js`) from `cdn.jsdelivr.net`.
- Observed: a primary field (phone) depends on a third-party CDN — a perf/availability/CSP/offline-PWA risk on flaky mobile networks.
- Recommendation: self-host `intl-tel-input` under `static/` (also simplifies CSP).
- Verify: no external request for the phone field; works offline-installed.

### 2.4 Cross-cutting (both pages + menu)

- **[X1] Tap-target audit (P1, E1):** sweep every control to 44×44 / 48×48 — covers M6, S4. Mostly Tailwind padding / `min-h` tweaks.
- **[X2] Loading/skeleton on HTMX swaps (P2, E2):** section drill-downs and toggle saves should show a skeleton/spinner then a success/error toast; a `partials/skeleton_dashboard.html` precedent exists.
- **[X3] `prefers-reduced-motion` (P3, E1):** gate the `:active` scale and slide page transitions behind the media query.
- **[X4] Contrast pass (P2, E1):** verify `text-gray-400/500` secondary text and the 10 px nav labels (`#9ca3af`, `tailwind-input.css:11072`) meet 4.5:1 in light **and** dark.

---

## Part 3 — Prioritized roadmap

### Phase 1 — Quick wins (CSS / small template, ~1–2 days)
| ID | Change | Files |
|----|--------|-------|
| M3 | Add active-state + tap handler to Crush Connect tab | `bottom_nav.html`, `bottomNav` in `alpine-components.js` |
| M6 / X1 | Bell + small controls to ≥ 44×44 | `tailwind-input.css`, `account_settings.html` |
| S5 | Hide duplicate in-page `h1` on mobile | `account_settings.html` |
| M5 | Point bell at notifications (confirm w/ product) | `top_bar_mobile.html` |
| X3 | Honour `prefers-reduced-motion` | `tailwind-input.css` |

### Phase 2 — Structure & IA (~3–5 days)
| ID | Change | Files |
|----|--------|-------|
| S1 | Convert Settings to sectioned drill-down (reuse Edit-Profile pattern) | `account_settings.html` (+ new section partials) |
| M4 / S2 | One canonical Settings home; extract shared `_linked_accounts` / `_account_info` partials | both settings templates |
| M1 | Cap tab bar at 5 (relocate Coach) | `bottom_nav.html` |
| P2 | Show current value in profile rows | `edit_profile.html` |

### Phase 3 — North-star & polish (~1 week)
| ID | Change | Files |
|----|--------|-------|
| **M2** | **Elevate "find a match" / Crush Connect to a distinct center action** | `bottom_nav.html`, `tailwind-input.css` |
| P3 | Reframe profile completion as "get matched" progress | `edit_profile.html` |
| P4 | Keyboard/nav collision fix for sticky actions | `tailwind-input.css` |
| P5 | Self-host `intl-tel-input` | `edit_profile.html`, `static/` |
| X2 / X4 | Skeletons + toasts; contrast pass | multiple |

---

## Part 4 — The standout: a "find a match" north-star (design intent for M2/P3)

A member's two most valuable mobile moments are **(1) being told they can find a match now** and **(2) opening Crush Connect to do it.** To make those unmissable:

1. **Give discovery a throne, not a seat.** In the bottom bar, render the match/Connect action as a visually distinct **center element** (accent fill or raised pill) — the established dating-app convention — so it reads as *the* thing to do, while Home/Events/Connections/Profile support it.
2. **Name the journey, not the database.** Group the experience as **Discover → Events → Matches** so "where do I find someone" has one answer (today this is split across three tabs — M2).
3. **Make the profile a launchpad to matching.** Edit Profile should always show the shortest path to being matchable, and on completion point straight at Crush Connect (P3).
4. **Settings should get out of the way.** Demote settings to a calm, well-grouped screen behind the Profile tab (S1/M4) so it never competes for attention with finding a match.

---

## Appendix A — Reusable findings checklist (copy per surface)

```
A Reachability  □ thumb-zone  □ ≤5 tabs  □ current location  □ predictable back  □ flagship distinct
B IA            □ grouped by use  □ no endless scroll  □ one canonical home  □ rows show value
C Touch/forms   □ ≥44/48 targets  □ ≥8px spacing  □ native inputs  □ tappable labels  □ keyboard-safe  □ inline validation
D Feedback      □ <100ms tap  □ loading/skeleton  □ no CLS
E Performance   □ fast on Slow4G  □ no core CDN dep  □ sized/lazy images
F Platform/a11y □ safe areas  □ dark mode  □ reduced-motion  □ focus visible  □ contrast ok
```

## Appendix B — Optional CI audit workflow (not yet implemented)

To make Step 7 automatic, add a GitHub Action running Lighthouse-CI (mobile preset) + `axe` against `/de/account/settings/` and `/de/profile/edit/` on each PR, failing on regressions in performance, accessibility, and tap-target/contrast best-practices. Place under `.github/workflows/` and gate on a staging URL or an ephemeral `runserver`. Ask before adding — it changes CI.
