# Crush.lu Style Guide

The canonical reference for visual choices on Crush.lu. Read this before
adding a new page, partial, or CSS class. If you see something on a screen
that contradicts this document, the screen is wrong — fix it or open an
issue, don't add a new variant.

This file is **scoped to Crush.lu**. Other platforms in the multi-domain
project (Vinsdelux, Entreprinder, Power-up, etc.) are not bound by it,
though they share `azureproject/` infrastructure.

---

## 1. Design tokens

### Colors

Brand palette lives in `tailwind-src/crush_lu/tailwind-input.css` `@theme`
block. Two parallel naming systems exist for valid reasons — use the right
one for the context:

| You're writing… | Use… | Where it lives |
| --- | --- | --- |
| Tailwind utility classes (`bg-crush-purple`, `text-crush-purple-dark`) | `--color-crush-*` from `@theme` | Generated into utilities at build time. |
| Inline `style="…"` in a request-rendered HTML template (dark-mode-aware) | `var(--crush-purple)` | Declared inline in `crush_lu/base.html:130` (`:root` + `html.dark`). |
| Inline `style="…"` in an email template or the Django admin context | `{{ brand.purple }}` via `{% load crush_brand %}` | `crush_lu/templatetags/crush_brand.py`. |
| Plain CSS inside `crush_lu/static/crush_lu/css/admin-custom.css` | `var(--crush-purple)` | Re-declared in the file's `:root`. Admin pages do NOT load `tailwind.css`. |

When you change a brand color, update **all four** of those locations.

### Border radius

Prefer Tailwind defaults. Only reach for the brand custom scale when the
shape genuinely needs it.

| Element | Class | Value |
| --- | --- | --- |
| Cards, modals, prominent panels | `rounded-2xl` | 16 px |
| Inputs, buttons (non-pill), small panels | `rounded-xl` | 12 px |
| Subtle/nested boxes | `rounded-lg` | 8 px |
| Avatars | `rounded-full` | — |
| Status badges | `rounded-full` | — |
| Hero "soft card" brand-feel surfaces | `rounded-crush-lg` | 15 px |
| Pill-shaped CTA on full-bleed gradient hero | `rounded-crush-pill` | 50 px |

`rounded-crush-sm`, `rounded-crush-md`, `rounded-crush-xl`, `rounded-crush-2xl`
were removed in 2026 as dead code — don't reintroduce them.

### Shadows

| Use | Class |
| --- | --- |
| Resting card | `shadow-crush-sm` |
| Hover-lifted card | `shadow-crush-md` |
| Floating panel, sticky drawer | `shadow-crush-lg` |
| Brand-tinted purple glow (rare; spotlight CTA) | `shadow-crush-purple` |
| Brand-tinted pink glow (rare; spotlight CTA) | `shadow-crush-pink` |

Don't reach for arbitrary `shadow-[…]`. If a needed shadow isn't here,
add it to `@theme` and document it in this table.

### Typography

`h1`–`h6` defaults are set in `tailwind-input.css` `@layer base`. Use semantic
heading levels and trust the defaults. Override only when:

- The page is a **hero/marketing surface** — then bump h1 with the editorial
  `font-display` (Fraunces) per existing landing-page patterns.
- A **card title** needs to be slightly smaller — use `.card-title` (defined in
  `tailwind-input.css`).

Don't sprinkle `text-2xl`/`text-3xl`/`text-4xl` on every page heading. The
defaults already give you a consistent scale.

---

## 2. Buttons

Four canonical variants. Pick by **role**, not by color preference.

| Variant | Class | When |
| --- | --- | --- |
| **Primary CTA (gradient)** | `.btn-crush-primary` | The single hero conversion CTA per page — "Submit profile", "Send spark", "Join event". Exactly one per page. |
| **Solid** | `.btn-crush-solid` | Any other primary action on the page: "Save", "Confirm", "Continue". Same shape/size as `.btn-crush-primary` without the gradient, so the gradient stays meaningful. |
| **Outline** | `.btn-crush-outline` | Secondary actions: "Cancel", "View details", "Edit". |
| **Destructive** | `.btn-danger` | Irreversible/destructive actions: "Delete", "Remove", "Block". |

Modifiers:

| Modifier | Class | Use |
| --- | --- | --- |
| Small | `.btn-sm` | Inline buttons, table rows, dense UI. |
| Large | `.btn-lg` | Onboarding moments where the CTA needs visual weight. |
| Full-width | `.btn-block` | Mobile forms, modals. |
| Link-styled | `.btn-link` | "Forgot password?", "Use a different account" — actions that read as inline links. |

### Legacy button classes (do not use in new code)

These still exist for backwards compatibility with coach and admin templates
but are **deprecated** for new user-facing work:

- `.btn-primary`, `.btn-secondary` — old, smaller sizing than the `crush-*` variants.
- `.btn-outline-primary`, `.btn-outline-secondary`, `.btn-outline-danger` — superseded by `.btn-crush-outline` (and inline status overrides where necessary).
- `.btn-success`, `.btn-warning`, `.btn-info` — superseded by tone-tinted `.btn-crush-solid` + an inline `bg-*` override when you genuinely need a non-purple solid CTA (rare).

If you find yourself reaching for one of these in a new file, ask whether
the new file should be migrated to the canonical four.

---

## 2b. Crush Connect wizard components (`.connect-*`)

The Crush Connect onboarding wizard and its shared step partials (also reused
by the Connect profile editor) follow a dedicated, product-designed component
set defined in `tailwind-input.css`. These are the **sanctioned** classes for
that surface — use them there rather than re-deriving inline utilities, and
don't spread them to unrelated pages.

| Class | Role | Notes |
| --- | --- | --- |
| `.connect-card` | The focused step card | True-white (`--color-surface-card`) on the lavender wizard canvas. Deliberately `rounded-3xl` (24 px) + `shadow-crush-md` — a documented exception to the resting-card defaults (`rounded-2xl` / `shadow-crush-sm`), because the wizard is a full-screen, single-focus onboarding surface, not an in-page card. |
| `.connect-card-title` | Step heading | `font-display` (Fraunces) 22 px. The card-title of this surface; don't hand-roll `text-[22px]`. |
| `.connect-selectable` | Shared option state | Drives default/hover/checked/disabled for peer-checked options via the general-sibling (`~`) combinator. Compose with a layout class below. |
| `.connect-tile` | Emoji option tile | 3-per-row grids; `rounded-[14px]` (a design-specified radius unique to these tiles). |
| `.connect-chip` | Trait / interest pill | `rounded-full`, `min-h-10` (40 px tap target). |
| `.connect-btn-secondary` | Wizard "Back" pill | The neutral white/gray-bordered secondary paired with `.btn-crush-primary` Continue in the wizard footer. The **only** sanctioned secondary outside `.btn-crush-outline`, scoped to the Connect wizard where the design calls for a neutral (not purple-outline) back action. |

Tokens introduced for this surface (in `tailwind-input.css`):

- `--color-surface-card: #fff` — true white for cards on the lavender canvas.
  Use it (not `bg-white`, which is remapped to the tinted `#f0ecf5`).
- `--bottom-nav-height` — the mobile tab-bar height; consumed by the bar and
  by anything that must clear it (bottom-anchored toasts).

---

## 3. Forms

Use the shared form-field partial. It renders `<label>` + input + errors +
help text with a consistent visual structure, includes the required-marker,
and respects dark mode.

```django
{% trans "Be as specific as possible." as field_help %}
{% include "crush_lu/components/form_field.html" with field=form.sender_description help=field_help %}
```

Args (see the partial's docstring for the full contract):

- `field` *(required)* — bound Django form field.
- `label` — override the field's label; pass `""` to suppress.
- `help` — extra help text (in addition to `field.help_text` if set).
- `label_class`, `field_class` — extra classes appended.

Don't manually compose `<label>` + `{{ field }}` + error markup in new
templates. If the partial doesn't fit, extend it — don't fork it.

For hand-rolled inputs where the partial genuinely can't work (e.g. Alpine
bindings on the element), use `.input-crush` for inputs/textareas and
`.form-select` for selects. Never write `form-control` in a template —
`test_tailwind_migration` bans it as a legacy Bootstrap class.

---

## 4. Component partials

Reusable visual primitives live in `crush_lu/templates/crush_lu/components/`.
Reach for these before composing inline:

| Partial | Purpose |
| --- | --- |
| `components/profile_photo.html` | Avatar with `initials` (default) or `icon` fallback. Sizing/shape via `css_class`. Use the `{% profile_photo %}` inclusion_tag from `crush_media` rather than `{% include %}`. |
| `components/form_field.html` | Form field (label + input + errors + help). `{% include "crush_lu/components/form_field.html" with field=form.x %}`. |
| `components/status_badge.html` | `.badge` with tone + icon + label + optional suffix. Prefer the convenience tag below over hand-rolling. |
| `components/htmx_spinner.html` | The shared `<span class="htmx-indicator">` + loading icon. Pair with a sibling `<span class="htmx-hide-on-request">` carrying the resting label. |

### Status-badge mapping (connections)

Use the `connection_status` inclusion_tag rather than re-writing if/elif
chains in templates:

```django
{% load connection_status %}
{% connection_status_badge connection %}
{% connection_status_badge connection show_coach=True %}
```

The (label, tone, icon) mapping lives in
`crush_lu/templatetags/connection_status.py` — extend that file when adding
a new EventConnection status, not the templates.

### Naming convention

`crush_lu/templates/crush_lu/components/<thing>.html`. No leading underscore
(consistent with the existing `profile_photo.html`). Always `{% load i18n %}`
at the top — never assume the caller has loaded it.

---

## 5. The "do-not-edit" list

Some files are legacy / parked and should NOT be touched casually:

- `crush_lu/static/crush_lu/css/components/profile-creation.css` — onboarding
  wizard. References several undefined CSS variables. Listed in its file
  header. Will be modernised when the onboarding flow comes back in scope.
- `crush_lu/components/` (Python files) — abandoned `django_components`
  scaffolding (`Card`, `Button`, `Alert`). Zero call sites. Do NOT extend it;
  use `{% include %}` partials instead.
- `crush_lu/static/crush_lu/js/photoUpload` (the Alpine component) — to be
  marked deprecated in Phase 5; new photo uploads should use `photoPicker`.

---

## 6. Brand-related Python helpers

- **`crush_lu.templatetags.crush_brand`** — `{% brand_colors as brand %}`.
  Returns the brand color dict for inline `style="…"` usage in templates
  rendered via `render_to_string()` (emails, allauth pages — places where
  Django context processors don't fire and CSS vars aren't reliable).

- **`crush_lu.templatetags.connection_status`** — `{% connection_status_badge connection %}`.
  Single source of truth for the EventConnection.status → badge mapping.
  Extend the dict in `connection_status.py` to add new statuses; don't add
  more if/elif chains in templates.

---

## 7. Alpine.js primitives

Three shared mixin factories live at the top of
`crush_lu/static/crush_lu/js/alpine-components.js`. Compose them inside
your named Alpine.data component with the **`mixin`** helper (also in
that file). They are not themselves Alpine.data registrations because
the CSP build cannot pass arguments via `x-data`.

**Do not use `Object.assign` or spread (`...`)** to compose with these
— both evaluate the source's getters during the copy (with the wrong
`this`), turning live `get isFoo()` accessors into dead `undefined`
data properties. Use `mixin(target, source)` — it copies descriptors
via `Object.defineProperties` so accessors stay live and resolve
against the final composed `this`.

### `makeTabs(initial, names)`

State + `setTab(name)` + `isTabActive(name)`. Used by `eventTabs` (line ~1100).
Extend with template-facing aliases (e.g. `showUpcoming`, `upcomingTabClass`)
in your wrapper.

```js
Alpine.data("myTabs", () =>
    mixin(makeTabs("first", ["first", "second"]), {
        get isFirst() { return this.isTabActive("first"); },
        showFirst() { this.setTab("first"); },
        showSecond() { this.setTab("second"); },
    }),
);
```

### `makeConfirm({ autoSubmit })`

Two-state idle/confirming flow. `request()` to enter the confirming state,
`cancelConfirm()` to back out, `proceed()` to finalize. When
`autoSubmit !== false`, `proceed()` submits the enclosing `<form>`. Used by
`sparkConfirm` (line ~10920) with `autoSubmit: false` because the panel
makes its own HTMX call instead.

### `makeModal(initiallyOpen)`

`showModal()` / `hideModal()` / `toggleModal()` plus `isModalOpen` /
`isModalClosed` getters. Reach for this rather than rolling a one-off
`x-data="{ open: false }"`.

### Deprecated Alpine components (do not use in new code)

| Component | Replace with | Reason |
| --- | --- | --- |
| `photoUpload` | `photoPicker` | 3-slot hardcoding, no HTMX upload. Console.warn at runtime. Kept alive for the onboarding wizard while that flow is parked. |

---

## 8. Notifications

Two systems exist today; the long-term direction is one.

- **`Alpine.store('toasts')`** — corner toast UI, used by HTMX responses
  (`HX-Trigger: showToast` and `window.dispatchEvent('show-toast', …)`)
  and any new JS that needs to surface a message. Source:
  `crush_lu/static/crush_lu/js/toast-component.js`. Container:
  `crush_lu/templates/crush_lu/shared/toast.html`.
- **Inline Django messages banner** — a `{% if messages %}` block in
  `base.html` (~line 1150) that renders prominent top-of-page banners
  for `messages.success(...)` etc. Kept as-is for now because removing
  it visually changes every page that uses `messages.success`. Phase 6
  did not consolidate the two channels — see "Pending consolidation"
  below.

Pick the toast store for any NEW notification surface. Only call
`messages.success/error/warning(...)` if you genuinely want the
top-of-page banner treatment, and prefer the toast store if you don't.

### Pending consolidation

When the team is ready to make the toast store the single notification
channel, the migration is small:

1. In `base.html` (~line 1150–1185), replace the `{% if messages %}`
   banner block with a small script that pushes each `{{ message }}`
   into `Alpine.store('toasts')` on page load.
2. Delete the dismissible-banner styling block.
3. Update STYLE.md §8 to remove the dual-system note.

This is intentionally NOT in the current refactor — the banner is a
prominent UX pattern many users rely on, and consolidating it touches
every page on the site (including coach/admin which are out of scope).

---

## 9. Linter

The script `crush_lu/scripts/lint_design_tokens.py` enforces this guide
mechanically. It flags:

- Hardcoded brand-purple / indigo hexes (`#7c3aed`, `#4f46e5`, `#6366f1`)
  in non-email, non-decorative templates.
- Deprecated button classes (`.btn-primary`, `.btn-secondary`,
  `.btn-success`, etc.) in non-exempt directories.

Exempt: `admin/`, anything coach-named, `journey/`, `gift/`, `advent/`,
`wonderland/`, `pre_screening/`, `welcome.html`, `onboarding/`,
`create_profile/edit_profile`, email templates (`emails/`, `email/`),
ghost-story SVG decoration files, and `test_*.html` scratch templates.

Run manually:

```
python crush_lu/scripts/lint_design_tokens.py
python crush_lu/scripts/lint_design_tokens.py path/to/file.html
```

Pre-commit hook: `.pre-commit-config.yaml` wires the linter to run on
`*.html` files inside `crush_lu/templates/crush_lu/` that appear in a
commit. Existing debt elsewhere in the tree only fires when someone
touches those files — so the linter doesn't block unrelated work, but
does catch new drift and nudges every modification toward the canonical
classes.

## 10. Ghost logo & mascot

All ghost artwork shares one set of shapes. The path data (body, eyes,
mouth, heart, sparkles) lives in exactly three fragments — **never copy
a `d="…"` string into another template**:

- `includes/ghost-core.html` — body + eyes + mouth, parameterized by `variant`
- `includes/ghost-heart.html` — the pink heart (`pulse=True` adds the fill pulse)
- `includes/ghost-sparkles.html` — the four sparkle circles

The ghost family is grandfathered in `includes/` (not `components/`)
because the ~30 `ghost-story-*` decorations already live there.

### Variants

| Include | Look | Use for | Default size |
|---|---|---|---|
| `ghost-logo.html` | Static body, orbiting heart, eyes follow it | Navbar / footer / inline brand | `w-7 h-7` |
| `ghost-logo-animated.html` | Float, blink, heartbeat, sparkles (SMIL) | Loading overlay, celebrations on gradient | `w-7 h-7` |
| `ghost-logo-hero.html` | Animated + cursor-tracked eyes/heart | Home hero **only** | `w-7 h-7` |
| `ghost-logo-icon.html` | No animation, outline baked in | Tiny badges (`w-4`/`w-8`) — the only variant for icon sizes | `w-4 h-4` |
| `ghost-logo-mono.html` | Single color via `currentColor`, mask-punched face | Single-color contexts (emails, watermarks) | `w-7 h-7` |

`ghost-logo-hero.html` has a JS contract: the `ghostEyes` Alpine
component (`alpine-components.js`) queries `.ghost-eye` / `.ghost-heart`
inside the hero section and rewrites their transforms per frame. That
markup — and the heartbeat's `additive="sum"` — is load-bearing.

### Params of `ghost-core.html`

- `variant` (required): `icon | static | animated | hero | mono`
- `body_fill` (default `#FFFFFF`)
- `no_outline`: truthy disables the baked outline. The flag is inverted
  on purpose — Django's `|default:` replaces *falsy* values, so an
  `outline=False` param would silently flip back to the default.
- `uid`: id suffix for the mono mask; pass a unique value when rendering
  more than one mono ghost per page (inline SVG ids are document-global).

### Outline & glow (visibility on light surfaces)

The body path carries a baked brand-purple outline
(`stroke="#9b59b6" stroke-opacity=".45" paint-order="stroke fill"`) so
the white ghost reads on white cards; on the purple-pink gradients it
blends in. The hex is hardcoded (not `var(--crush-purple)`) because
standalone pages (`oauth_landing.html`, `ghost_showcase.html`) load no
brand CSS variables. Never add `vector-effect="non-scaling-stroke"` —
it renders 6 *screen* pixels and swallows the icon sizes.

For **large ghosts on white/light cards** (empty states, feature cards)
additionally apply the `.ghost-glow` utility (purple drop-shadow, has a
dark-mode variant). Never put it on gradient surfaces (navbar, hero,
loading overlay, footer). The `ghost-story-*` decoration files stay
untouched — glow is applied at the call site.

### Do / don't

- Never reference an SVG that must recolor via `<img>` — external
  images take no page CSS (that's why `ghost-logo-icon.html` replaced
  `static/crush_lu/svg/ghost-logo-animated.svg`, since deleted).
- `/en/ghost-showcase/` renders every variant, including a
  "Logo Variants & Light Surfaces" strip with white test cards.

### Brand asset inventory

- Favicon: `static/crush_lu/favicon.svg` (preferred) + `crush_favicon.ico`
  (fallback), both linked in `base.html`. The SVG mirrors `ghost-core.html`
  shapes — keep in sync; no SMIL (Firefox animates favicons).
- PWA: maskable 192/512 icons + apple-touch set, served via the
  `pwa_manifest` view (`views_pwa.py`).
- Social/OG: `static/crush_lu/crush_social_preview.jpg` (1200×630), wired
  through `SOCIAL_PREVIEW_IMAGE_URL` (settings → context processor →
  `base.html`). To refresh, replace the jpg in place or point the env var
  at a new image — no code change needed.
