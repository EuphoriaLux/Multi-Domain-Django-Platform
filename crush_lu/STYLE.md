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

---

## 4. Component partials

Reusable visual primitives live in `crush_lu/templates/crush_lu/components/`.
Reach for these before composing inline:

| Partial | Purpose |
| --- | --- |
| `components/profile_photo.html` | Avatar with `initials` (default) or `icon` fallback. Sizing/shape via `css_class`. Use the `{% profile_photo %}` inclusion_tag from `crush_media` rather than `{% include %}`. |
| `components/form_field.html` | Form field (label + input + errors + help). `{% include "crush_lu/components/form_field.html" with field=form.x %}`. |
| `components/status_badge.html` | `.badge` with tone + icon + label + optional suffix. Prefer the convenience tag below over hand-rolling. |

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
your named Alpine.data component with `Object.assign`. They are not
themselves Alpine.data registrations because the CSP build cannot pass
arguments via `x-data`.

### `makeTabs(initial, names)`

State + `setTab(name)` + `isTabActive(name)`. Used by `eventTabs` (line ~1100).
Extend with template-facing aliases (e.g. `showUpcoming`, `upcomingTabClass`)
in your wrapper.

```js
Alpine.data("myTabs", () =>
    Object.assign(makeTabs("first", ["first", "second"]), {
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
