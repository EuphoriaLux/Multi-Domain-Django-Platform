# Atmos — QR Bar Ordering System

**Status:** Draft spec v2 (prototype / MVP)
**Owner:** Tom Scheuer
**Date:** 2026-08-16
**Mount point:** `power_up.atmos` submodule, served at `power-up.lu/atmos/`
**Changes in v2:** per-guest identity with funny aliases (§3), guests split from tabs in the data model (§4), a 2-hour per-guest ordering window with staff *settle-or-extend* (§5), settlement records (§4.9).
**Out of scope:** online payment, venue self-service backoffice — see [Deferred](#12-deferred-post-mvp).

> **Implementation status (synced 2026-08-20):** Guests, Tabs, Orders, the KDS, the alias/persona system, and thermal-ticket printing shipped in PR [#862](https://github.com/EuphoriaLux/Multi-Domain-Django-Platform/pull/862) / [#865](https://github.com/EuphoriaLux/Multi-Domain-Django-Platform/pull/865). Three pieces of this v2 draft were **never built** and this doc has been updated in place to describe what actually shipped instead of what was drafted: the rank-escalating alias (§3.3 — replaced by a single stable persona), the `Settlement` model (§4.9 — not built), and the 2-hour guest window (§5 — not enforced). `power_up/atmos/models.py`'s own module docstring is the authoritative deferred list; every "shipped vs. spec" note below traces back to it. §12 (Remote Treat) is a **later, separate proposal that was never built at all** — see its banner.

---

## 1. Concept

Guests in a bar scan a QR code glued to their table. No app install, no login. The QR opens a mobile web menu already bound to that table. They add drinks or food to a cart and place the order. Behind the bar, a staff screen shows incoming orders live and lets staff move them through *new → in progress → served*.

Payment stays offline for the MVP: the guest pays the server at the table or at the counter as they do today. This removes PSP onboarding, PCI scope, and refund handling from the prototype while still proving the core value.

**What makes Atmos different from every other QR ordering system:** the delivery moment. When the barkeeper arrives with a tray and says *"two Pilsner for The Whispering Gambler?"*, the table laughs. That laugh is the product. Ordinary QR ordering systems remove a human interaction and give you nothing back; Atmos removes the *waiting* and keeps the *contact*, with a joke attached.

Each person at the table gets their own identity — scanned individually from the same table QR — so the barkeeper knows whose drink is whose without asking, and every guest gets their own moment.

**Why it is worth prototyping:** the ordering loop plus the delivery gag is the whole product. If guests won't scan, or the joke lands flat, or staff won't watch a second screen, no amount of payment integration saves it. Build the loop, put it in one real bar for one evening, and watch faces.

### 1.1 Primary users

| Actor | Device | Authenticated? |
|---|---|---|
| Guest | Own phone, mobile browser | No — anonymous, cookie-bound to a personal guest record |
| Bar staff | Tablet or till browser behind the bar | Yes — Django staff user |
| Venue manager | Desktop | Yes — Django admin for now |

### 1.2 Success criteria for the prototype

- A guest goes from scan to placed order in under 60 seconds without instructions.
- **Over half of guests keep the alias** rather than typing a real name — the fast path should also be the appealing one.
- **Staff report the delivery moment as fun, not friction** — this is the qualitative signal that decides whether the concept has legs.
- Staff see a new order on the KDS within 5 seconds.
- Zero orders lost, duplicated, or delivered to the wrong person across a two-hour service.

---

## 2. Where it lives in the platform

Atmos follows the existing `power_up` submodule pattern already used by `crm`, `finops`, and `onboarding`. Nothing new is invented.

| Concern | Decision |
|---|---|
| Python package | `power_up/atmos/` |
| App label | `atmos` (`AtmosConfig.name = "power_up.atmos"`, `label = "atmos"`) |
| `INSTALLED_APPS` | Add `"power_up.atmos"` after `"power_up.onboarding"` |
| URL mount | `path("atmos/", include("power_up.atmos.urls"))` in `azureproject/urls_power_up.py`, **language-neutral** block |
| Templates | `power_up/templates/atmos/` |
| Static | `power_up/static/atmos/` |
| Media (menu photos) | `ImageField(upload_to=powerup_upload_path("atmos/menu"), storage=powerup_media_storage)` from `power_up/storage.py` — the platform never relies on `STORAGES["default"]` |
| Domain config | None. `power-up.lu` already routes to `azureproject.urls_power_up`. Local dev: `power-up.localhost:8000/atmos/` |
| Realtime | HTMX polling. No Channels, no Redis, no ASGI requirement |

### 2.1 Why language-neutral rather than `i18n_patterns`

The guest UI needs EN/DE/FR — this is Luxembourg. But putting it inside `i18n_patterns` means every QR code encodes a language prefix, and a table's printed QR would be locked to one language forever. Instead: mount language-neutral, ask for the language on the name screen (it is the guest's first interaction anyway), and store the choice on the guest record. **Shipped vs. spec:** the language-neutral mount happened; the ask-and-store half never did — there is no language field on `Guest` and no language prompt on the join screen (§3.1). The guest UI is English-only today.

### 2.2 Background work

None. Per `CLAUDE.md`, production runs the `ImmediateBackend` inline and no `db_worker` exists — `.enqueue()` defers nothing. Every Atmos operation is bounded and in-request by design. **Note this specifically for the 2-hour window (§5):** expiry is computed on read, never by a scheduled job, because there is no scheduler to run one.

---

## 3. Guest identity — the funny name

### 3.1 The screen

**Shipped vs. spec:** the two-step mock below (blank screen → tap to roll → confirm) was the pre-build draft. What shipped (`power_up/templates/atmos/join.html`) skips the blank first step: a persona is already rolled and shown the moment the join page renders, so the guest's first sight of the screen already has a name to react to.

```
        📍 Table 12
   Welcome to The Velvet Hour
 What should we shout when
   your drinks are ready?

  ┌───────────────────────────┐
  │   🎲 Tonight's Persona    │
  │                           │
  │  THE WHISPERING GAMBLER   │
  │                           │
  │ [ That's me, enter bar → ]│
  │ [ 🎲 Roll another persona ]│
  └───────────────────────────┘

    ── or enter your name ──

  ┌───────────────────────────┐
  │  Your name or nickname    │
  └───────────────────────────┘
       [ Use this name ]

  🔒 Anonymous & temporary
```

"Roll another persona" is a plain link back to the same `guest_join` view (a full page reload with a freshly rolled persona), not an HTMX partial or a separate `/join/roll/` endpoint (see §6.1) — but it is still free, unlimited, and writes nothing to the database before confirming, so the "thirty seconds of rerolling before a drink is ordered" reasoning below still holds.

There is **no EN · DE · FR language toggle** on this screen or anywhere in the guest flow. `/atmos/` is mounted language-neutral per §2.1, but no per-guest language field or `set_language` endpoint was built (`Guest` has no `language` column) — the guest UI is English-only today.

Rolling is free and unlimited *before* confirming. This is where the fun is manufactured — a table of four rerolling and reading names out to each other is thirty seconds of entertainment before a single drink is ordered.

### 3.2 Anonymous is the default, and that is deliberate

The alias button is visually dominant, listed first, and requires one tap versus a keyboard interaction. Three reasons, in order of importance:

1. **It is faster.** Typing on a phone in a dark, loud bar is the single worst step in the flow.
2. **It is funnier**, which is the point of the product.
3. **It avoids collecting personal data.** A typed first name is personal data under GDPR; "The Whispering Gambler" is not. Making the privacy-preserving option also the fun option means we do not have to talk anyone into it. See §8.4 for retention.

### 3.3 Alias structure: a single stable persona (rank ladder was never built)

**Shipped vs. spec:** the rank ladder below (`<Rank> <Noun>`, escalating *Captain Pretzel* → *Supreme Overlord Pretzel*) was the pre-build design and was **never built**. What shipped instead: `Guest.alias` is one plain string, rolled once from a fixed catalog of hand-written noir personas — *The Whispering Gambler*, *The Velvet Silhouette*, *The Midnight Chemist* — and held unchanged for the life of the guest record (`power_up/atmos/lore/personas.py`). There is no noun/rank split, no `RANKS` table, no per-venue `alias_escalation_enabled` toggle, and no rank field on `Guest` at all — see §4.7.

The core reasoning that motivated the rank ladder still holds, and the shipped design satisfies it more simply: an order must still say the same name it was placed under when the tray lands, or the barkeeper can't deliver it and the joke becomes an argument (§4.8's `alias_snapshot` exists for exactly this). A single alias that never changes gets that guarantee for free, without needing a time-based promotion mechanic to keep the name "the same but visibly different" — because there is no window mechanic (§5) to escalate against in the first place.

- **The alias never changes** for the life of the guest record. It is the delivery key. Staff learn it, guests answer to it.
- **Rerolling is unlimited before confirming**, none after — this part of the original design shipped as written.

If a future pass wants a running joke that develops over the course of the evening, the original "reward dwell time, not drink volume" constraint (§8.5 still enforces this principle for the shipped alcohol-safety copy) is the right one to design against — it just has nothing to hook into today, since nothing tracks how long a guest has been seated.

### 3.4 The persona catalog (shipped)

**Shipped vs. spec:** a single curated, human-reviewed, **English-only** catalog of ~48 full noir personas lives in `power_up/atmos/lore/personas.py` (`NOIR_PERSONAS`) — not the per-language EN/DE/FR animal-noun lists sketched below in the original draft. Since there is no separate rank word (§3.3), there is nothing to combine the noun with either — each catalog entry is already the complete, final alias.

Constraints every entry obeys — the underlying reasoning is unchanged from the original draft, plus one shipped-specific addition:

- ≤ 26 characters, so a persona centres on a 58mm thermal ticket (32 columns) without wrapping — the shipped equivalent of the original's "≤ 20 characters for a KDS card".
- Pronounceable by an EN, DE and FR speaker alike — still the design goal, even though the catalog itself is English-only today.
- **Encodable in CP858**, the thermal printer's native code page (`power_up/atmos/printing/escpos.py`) — a constraint the original draft couldn't have anticipated, since printing (§3.7) wasn't designed yet when this section was first written.
- Absurd or mysterious rather than "silly" — a tonal shift from the original's animal-noun humour to noir atmosphere, but the same underlying rule: nothing that mocks appearance, and nothing that reads as a comment on how much someone has had.

Examples actually in the shipped catalog: *The Whispering Gambler, The Velvet Silhouette, The Midnight Chemist, The Cloakroom Oracle, The Hatcheck Ghost, The Last Tram Home.*

**A native-speaker DE/FR (or Luxembourgish) pass remains open work**, same caution as the original draft: an innocuous word or image can be a slang insult in one language and not visible as one from the other side. The original per-language animal-noun starter lists below were never built and are kept here only as a record of the pre-build direction:

| Lang | Nouns (never shipped — superseded by the noir persona catalog above) |
|---|---|
| EN | Pretzel, Waffle, Pickle, Noodle, Muffin, Popcorn, Cashew, Olive, Radish, Truffle, Otter, Puffin, Walrus, Ferret, Hedgehog, Wombat, Gecko, Toucan, Llama, Moose, Penguin, Flamingo, Hamster |
| DE | Brezel, Waffel, Gurke, Nudel, Muffin, Olive, Rettich, Trüffel, Otter, Walross, Frettchen, Igel, Wombat, Gecko, Tukan, Lama, Elch, Pinguin, Flamingo, Hamster |
| FR | Bretzel, Gaufre, Cornichon, Nouille, Muffin, Olive, Radis, Truffe, Loutre, Macareux, Morse, Furet, Hérisson, Wombat, Gecko, Toucan, Lama, Élan, Manchot, Flamant, Hamster |

### 3.5 Uniqueness

Two guests both called *The Whispering Gambler* at one venue is a delivery failure, so the alias is unique **per venue among `status="active"` guests** (`random_persona(exclude=...)` in `lore/personas.py`, backed by the DB-level `uniq_active_alias_per_venue` constraint on `Guest`). This is narrower than the original draft's "non-settled" framing: a guest staff mark **`removed`** also releases their persona for immediate reuse, not just a `settled` one — there's no state where an alias is reserved without an active guest holding it.

The generator picks from the unused pool; if a venue somehow exhausts the catalog with concurrent guests, it falls back to appending a roman numeral (*The Whispering Gambler II*, *III*, …) and, if even those collide, a random two-digit suffix — rather than failing the scan. A guest scanning in must never see an error because the bar is busy, which was the original goal and still holds exactly as drafted.

### 3.6 Typed names need moderation

The free-text path puts guest-controlled text on a staff screen that gets read aloud — and, in the shipped design, also into an LLM prompt for the printed vignette (§3.7). Untreated, someone will type a slur or a prompt-injection attempt on their first visit. **Shipped, this is a mixed picture against the original draft** — stronger against prompt injection, slightly weaker on charset, and missing the staff-side backstop entirely:

- Length capped at 20 characters (`Guest.display_name`'s `max_length`), enforced by `sanitize_persona()` in `power_up/atmos/lore/safety.py` — matches the original draft's number.
- Charset is letters plus space, hyphen, apostrophe, **and period** — one character wider than the draft's "letters/spaces/hyphens/apostrophes only".
- Checked against a blocklist **and** a set of prompt-injection markers (`"ignore previous"`, `"system prompt"`, …) and AI-assistant-voice tells — a defence the original draft couldn't have anticipated, since it predates the vignette/chronicle feature. The blocklist itself is a single English placeholder list today, **not** the per-language lists the draft called for.
- On rejection: no scolding — the guest sees *"Let's try a noir one instead"* and a pre-rolled persona. The message wording changed, the never-explain-the-rule policy did not.
- **Not shipped:** the staff KDS override ("rename any guest to a fresh alias in one tap") described as the real backstop below. There is no `guest_rename` endpoint or equivalent staff action (§6.2) — the only staff mutation route that ships is `order_set_status`. A typed name that gets past `sanitize_persona()` today has no staff-side undo short of the Django admin.

### 3.7 Where the alias shows up

- KDS order card, largest text on the card.
- The guest's own status page (*"The Whispering Gambler — 2 Pilsner on the way"*).
- **The printed thermal ticket — shipped, not hypothetical.** `power_up/atmos/printing/` (`escpos.py`, `layout.py`, `art.py`, `transport.py`) is real, wired into order placement in `views.py`, and prints the alias on 58mm ESC/POS paper. The original draft's "if one is ever added" is stale in the other direction: this shipped before some of the mechanics (§5, §4.9) that were drafted alongside it.
- **Not** in analytics, exports, or logs beyond the venue's own order history — unchanged from the original draft.

---

## 4. Data model

Single `power_up/atmos/models.py`. All primary keys are `UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`, matching `power_up.crm` — and here it also prevents anyone enumerating other tables' orders from a URL.

### 4.1 Entity overview

The v1 `TableSession` splits in two. A **Tab** is the table's visit; a **Guest** is one person on it. Orders hang off the Guest.

**Shipped vs. spec:** the `Settlement` branch below was never built — see §4.9. Closing a `Tab` settles its guests directly (a status flip + timestamp on `Guest`, no separate record of amount or payment method).

```
Venue ─┬─ Table ──── Tab ──── Guest ──── Order ──── OrderItem
       └─ MenuCategory ──── MenuItem
```

### 4.2 `Venue`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | CharField(255) | |
| `slug` | SlugField, unique | Staff URLs |
| `address` | CharField(255), blank | |
| `currency` | CharField(3), default `"EUR"` | |
| `service_open` | BooleanField, default `True` | Master switch |
| `accepts_orders_from` / `_until` | TimeField, null | Soft service window — **not shipped**, no such fields on `Venue` |
| `guest_window_minutes` | PositiveSmallIntegerField, default `120` | Shipped, but **not enforced for ordering** — see §5. Its only reader is `purge_stale_guest_names`'s cutoff (§8.4), not the order-placement path |
| `guest_window_extension_minutes` | PositiveSmallIntegerField, default `120` | **Not shipped** — there's no extend action to grant time (§5, §6.2) |
| `alias_escalation_enabled` | BooleanField, default `True` | **Not shipped** — nothing to toggle, since rank escalation itself was never built (§3.3) |
| `order_note_enabled` | BooleanField, default `True` | **Not shipped** |
| `next_order_number` | PositiveIntegerField, default `1` | Shipped, not in the original draft — a monotonic per-venue counter for `short_code` (§4.8) that survives admin deletions, where a plain `count() + 1` wouldn't |
| `created_at` / `updated_at` | DateTimeField | |

### 4.3 `Table`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `venue` | FK → Venue, `related_name="tables"` | |
| `label` | CharField(32) | "12", "Terrace 3" |
| `qr_token` | CharField(22), unique, db_index | `secrets.token_urlsafe(16)` |
| `seats` | PositiveSmallIntegerField, null | |
| `is_active` | BooleanField, default `True` | |

`unique_together = ("venue", "label")`.

The QR encodes `qr_token`, not the PK, so a token can be **rotated** without touching table identity or order history. If a sticker is photographed and abused, staff regenerate, reprint one sticker, and every scraped URL dies.

### 4.4 `MenuCategory`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `venue` | FK → Venue, `related_name="menu_categories"` | |
| `name` | CharField(120) | |
| `sort_order` | PositiveSmallIntegerField, default 0 | |
| `is_visible` | BooleanField, default `True` | |

### 4.5 `MenuItem`

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `category` | FK → MenuCategory, `related_name="items"` | |
| `name` | CharField(160) | |
| `description` | TextField, blank | |
| `price` | **DecimalField(7,2)** | Never a float |
| `image` | ImageField(`powerup_upload_path("atmos/menu")`, `storage=powerup_media_storage`), blank | |
| `is_available` | BooleanField, default `True` | The 86-button |
| `contains_alcohol` | BooleanField, default `False` | Drives the KDS flag; §8.5 |
| `allergens` | CharField(255), blank | Comma-separated for the prototype |
| `sort_order` | PositiveSmallIntegerField, default 0 | |

### 4.6 `Tab`

The table's current visit. Container only — it holds no identity and places no orders.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `table` | FK → Table, `related_name="tabs"` | |
| `venue` | FK → Venue | Denormalised for staff queries |
| `opened_at` | DateTimeField, auto_now_add | |
| `closed_at` | DateTimeField, null | |
| `status` | CharField: `open`, `closed` | |

One open Tab per table, enforced by `UniqueConstraint(fields=["table"], condition=Q(status="open"))`. A scan joins the open Tab or opens one.

### 4.7 `Guest`

One person. Created on scan, bound to that phone by cookie.

**Shipped vs. spec:** the table below is the actual `power_up/atmos/models.py` field set, not the original draft — this is the section that changed the most. There is no `alias_noun` / `alias_rank` split (§3.3), no `is_anonymous` flag (`display_name` being empty already tells you that), no `language` field (§3.1), no `expires_at` / `extension_count` (§5), and no `expired` status (only `active`, `settled`, `removed`).

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | Stored in the guest's signed cookie |
| `tab` | FK → Tab, `related_name="guests"` | |
| `venue` | FK → Venue, `related_name="guests"` | Denormalised — shipped-beyond-spec, mirrors `Tab.venue` and `Order.venue`'s same reasoning: Django can't constrain uniqueness across the `tab → venue` join, so the FK is duplicated and always re-derived in `save()`, never independently settable |
| `alias` | CharField(32) | The rolled noir persona (§3.3, §3.4). Stable for the life of the record — the delivery key |
| `display_name` | CharField(20), blank | Only if typed. Purged on tab close, not on a per-guest "settle" moment alone (§8.4) |
| `joined_at` | DateTimeField, auto_now_add | |
| `last_activity_at` | DateTimeField, auto_now | |
| `status` | CharField: `active`, `settled`, `removed` | No `expired` state — see §5 |
| `settled_at` | DateTimeField, null | |

Constraint: `UniqueConstraint(fields=["venue", "alias"], condition=Q(status="active"), name="uniq_active_alias_per_venue")` — narrower than the draft's `~Q(status="settled")`: a `removed` guest's alias frees up immediately too (§3.5).

```python
@property
def display(self) -> str:
    """What staff see and shout — spec §3.7."""
    return self.display_name or self.alias
```

No rank to recompute on read (§2.2's "expiry is computed on read, never a background job" reasoning still applies to nothing here, since there is no time-based mechanic left to compute — see §5).

### 4.8 `Order` / `OrderItem`

`Order` gains a `guest` FK; everything else is unchanged from v1.

**Shipped vs. spec:** `short_code` is `CharField(12)`, not 6 — a fixed 8-char format (`"T" + label[:3] + "-" + order_number`) turned out to need room for `order_number` to grow past 3 digits over a venue's lifetime. `cancel_reason` and `idempotency_key` (§8.2) were **never built** — there is no idempotent-replay guard on order placement today. Two fields ship **beyond** the original draft:

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `guest` | FK → Guest, `related_name="orders"` | |
| `tab` | FK → Tab, `related_name="orders"` | Denormalised |
| `venue` | FK → Venue, `related_name="orders"` | Denormalised for the KDS query |
| `short_code` | **CharField(12)**, db_index | `T12-04` — width fixed up from the draft's 6, see above |
| `alias_snapshot` | CharField(32) | **The alias as displayed when placed** |
| `status` | CharField: `placed`, `accepted`, `preparing`, `served`, `cancelled` | |
| `note` | TextField, blank | |
| `placed_at` / `accepted_at` / `served_at` / `cancelled_at` | DateTimeField | Timestamps are the prototype's metrics |
| ~~`cancel_reason`~~ | — | **Not shipped** |
| ~~`idempotency_key`~~ | — | **Not shipped** — §8.2's double-submit guard doesn't exist |
| `total_amount` | DecimalField(9,2) | Snapshot at placement |
| `currency` | CharField(3), default `"EUR"` | **Shipped-beyond-spec.** Snapshots `venue.currency` at placement, same reasoning as every other `*_snapshot` field: a venue's currency changing later must not silently relabel a historical ticket |
| `vignette` / `vignette_source` | TextField / CharField(16), blank | **Shipped-beyond-spec.** Holds the AI-generated (or procedural-fallback) noir vignette text printed on the ticket (§3.7) — the original draft's data model had nowhere to store this, since the chronicle feature postdates it |

`alias_snapshot` exists **not** because rank escalates (it doesn't — §3.3), but because `display_name` gets purged when the tab closes (§8.4): an order placed under a typed name must still show that name on the KDS and the printed ticket after the purge clears it from the live `Guest` row, or the card the barkeeper picked up no longer matches what they're carrying.

Index on `("venue", "status", "placed_at")` — the KDS's only hot query.

`OrderItem`: `order` FK, `menu_item` FK (`on_delete=PROTECT`), `name_snapshot`, `unit_price_snapshot`, `contains_alcohol_snapshot` (shipped-beyond-spec — backs the `Order.contains_alcohol` property used by §8.5), `quantity`, `note`, `line_total`. Snapshots because the menu changes during service and an order is a record of what was agreed at that moment; recomputing from live prices silently rewrites last night's receipts.

### 4.9 `Settlement` — **not built**

Not a payment integration — a record that a human paid a human, so the tab can close cleanly and the window mechanic has a resolution. This model **does not exist in the shipped code** (`power_up/atmos/models.py`'s own docstring lists it first among things "deliberately deferred… to get a vertical slice live quickly"). No amount, payment method, or settling-staff-member is ever recorded anywhere.

What shipped instead is much thinner: closing a `Tab` (staff flip its `status` to `closed`, e.g. via the Django admin) runs `Tab.save()`, which — inside one `transaction.atomic()` block —

1. purges every guest's `display_name` back to `""` (with their past orders' `alias_snapshot` reset to `alias` first, so no typed name survives as the sole remaining copy — §8.4), and
2. flips every still-`active` guest on that tab to `status="settled"` with a `settled_at` timestamp.

There is no concept of "settle just this guest" (`Guest.status` can still be set to `settled` or `removed` directly, e.g. from the admin, but no staff-facing action does this), no amount pre-filled from unsettled orders, and no cash/card/other method captured. If a bar needs a record of how much was actually paid and by what method, it currently lives nowhere in Atmos — it's on the till, same as before Atmos existed.

The original draft's table is kept below as the design target if this gets built:

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tab` | FK → Tab, `related_name="settlements"` | |
| `guest` | FK → Guest, null | Null = whole table settled at once |
| `amount` | DecimalField(9,2) | Pre-filled from unsettled orders, staff-editable |
| `method` | CharField: `cash`, `card`, `other` | |
| `settled_by` | FK → User, `on_delete=PROTECT` | |
| `settled_at` | DateTimeField, auto_now_add | |
| `note` | CharField(160), blank | |

### 4.10 Status model

```
placed ──→ accepted ──→ preparing ──→ served
   │           │            │
   └───────────┴────────────┴──────→ cancelled
```

Validated in `Order.transition_to(new_status)` on the model, not the view — shipped without the draft's `by_user` argument; the model records no audit trail of which staff member made a transition. Illegal transitions raise `ValidationError`; `served` and `cancelled` are terminal.

---

## 5. The 2-hour window — **not enforced**

**Shipped vs. spec:** none of the ordering-time mechanic below is built. `Venue.guest_window_minutes` exists as a field (default 120, per §4.2) and is read by exactly one thing — `purge_stale_guest_names`'s cutoff (§8.4) — but nothing in the ordering path checks it: no `expires_at` on `Guest`, no `active` / `warning` / `expired` / `settled` state machine, no staff extend-or-settle prompt, no "Needs attention" tray. A guest can order for as long as three things all remain true: their signed cookie hasn't expired, their `Guest.status` is still `active`, and their `Tab` is still `open` on an active `Table`.

The only functional time bound today is the guest's **signed cookie's own `max_age`** — 6 hours (`GUEST_COOKIE_MAX_AGE` in `views.py`, matching what §7.1 already specified for the cookie itself), after which `signing.loads()` raises and the guest can no longer be resolved from it. That's an incidental side effect of the cookie's signing parameters, not a designed "window" mechanic — there's no warning state before it lapses, and nothing distinguishes "cookie expired" from "never had a cookie" once it does.

Each guest was meant to be able to order for `venue.guest_window_minutes` (default 120) from joining, with staff prompted to **settle up** or **add time** once it ran out. This was the mechanic meant to turn an unpaid-online system into something a bar can actually run: it forces a payment conversation at a predictable moment instead of hoping someone remembers to ask. It was also meant to quietly solve the photographed-QR problem (§8.6) — a leaked sticker granting at most two hours of nuisance ordering, and only while the venue is open. Neither benefit is realised today: a table's tab stays orderable for as long as staff leave it open, with no automatic prompt to resolve it.

### 5.1 States

| State | When | Guest sees | KDS shows |
|---|---|---|---|
| `active` | `now < expires_at - 15min` | Normal ordering | Normal |
| `active` (warning) | within 15 min of expiry | Banner: *"Colonel Waffle, your round's nearly up — catch a barkeeper to keep going"* | Amber chip on the table |
| `expired` | `now ≥ expires_at` | Menu is browsable, **ordering blocked**, cart preserved | Red, in the **Needs attention** tray |
| `settled` | Staff took payment | *"Thanks — scan again to start a new tab"* | Gone from the board |

**Expiry is soft, not destructive.** A guest who hits the wall mid-cart keeps the cart; they just cannot submit until extended. Losing someone's carefully built round because a timer ticked over would be an unforced error, and they would have to rebuild it while a barkeeper watches.

The 15-minute warning matters for the same reason: without it, the first anyone learns of the window is a refusal, which reads as a bug rather than a rule.

### 5.2 Staff resolution

Expiry surfaces on the KDS **grouped by table, not by person** — staff walk to a table, not to an individual. The card reads:

```
  TABLE 12 — 3 guests, time's up
  Colonel Pretzel   ·  4 items  ·  €23.50
  Major Otter       ·  2 items  ·  €11.00
  Captain Waffle    ·  0 items  ·  €0.00

  [ + 2 hours (all) ]   [ Settle table ]   [ … per guest ]
```

Two taps handle the common cases; the overflow menu covers one person leaving early or one person extending. `[ + 2 hours ]` pushes `expires_at` out and increments `extension_count`; `[ Settle table ]` opens a total, a cash/card toggle, and confirm.

### 5.3 What the guest is never asked

Guests are never shown a countdown clock, an amount owed, or a payment prompt. The window is a staff-side tool. A running bill on a phone screen changes the mood of a table, and a prototype that has not solved payment should not imply that it has.

---

## 6. URL surface

Two zones, cleanly separated — this separation is the security boundary of the whole app.

### 6.1 Guest zone — anonymous, cookie-scoped

> **Shipped routes** (`power_up/atmos/urls.py`): `guest_scan`, `guest_join`, `guest_menu`, `cart_add`, `cart_update`, `cart_detail`, `order_place`, `order_status` — eight of the twelve below. `guest_join` folds the drafted `guest_name` / `alias_roll` / `guest_confirm` split into one view (§3.1): there is no separate DB-free `/join/roll/` endpoint, and no `/atmos/lang/` / `set_language` route (no per-guest language field exists — §3.1). `order_status_poll` isn't a separate route either; see §5 for why "409 if guest expired" doesn't apply — nothing expires a guest anymore.

| Method | Path | Name | Purpose |
|---|---|---|---|
| GET | `/atmos/t/<qr_token>/` | `guest_scan` | QR landing. Resolves table, joins/opens Tab, recognises returning cookie, else redirects to naming |
| GET | `/atmos/join/` | `guest_name` | The funny-name screen |
| POST | `/atmos/join/roll/` | `alias_roll` | HTMX — returns a fresh alias, no DB write |
| POST | `/atmos/join/confirm/` | `guest_confirm` | Creates the Guest, sets the cookie |
| GET | `/atmos/menu/` | `guest_menu` | Menu for the guest's venue |
| POST | `/atmos/cart/add/` | `cart_add` | HTMX — updated cart badge |
| POST | `/atmos/cart/update/` | `cart_update` | Quantity / remove |
| GET | `/atmos/cart/` | `cart_detail` | Review |
| POST | `/atmos/order/place/` | `order_place` | Idempotent; 409 if guest expired |
| GET | `/atmos/order/<uuid:pk>/` | `order_status` | Guest-facing status |
| GET | `/atmos/order/<uuid:pk>/poll/` | `order_status_poll` | HTMX partial, 5 s |
| POST | `/atmos/lang/` | `set_language` | Stores language on the Guest |

After the initial scan, guest views take **no** table, tab or guest parameter. Everything is derived server-side from the signed cookie. A guest cannot address another table by editing a URL because there is no table in the URL to edit.

`alias_roll` writes nothing and reserves nothing — a name is only claimed at `guest_confirm`, which re-checks uniqueness inside the transaction and silently rerolls on collision.

### 6.2 Staff zone — `@staff_member_required`

Matching `power_up.crm`, which uses `django.contrib.admin.views.decorators.staff_member_required` throughout.

> **Shipped routes:** `staff_home`, `kds`, `order_set_status` — three of the twelve below. None of the window/settlement staff actions (`guest_extend`, `tab_extend`, `guest_rename`, `tab_settle`, `guest_settle`) exist, consistent with §4.9 and §5 not being built. `kds_poll` isn't a separate route. `item_toggle` (the 86-button) and `qr_sheet` (printable QR sheet) aren't standalone staff URLs either — menu/table/venue management runs entirely through Django admin today, further than the "MVP" scoping below already conceded. `qr_rotate` exists, but as a **`TableAdmin` admin action**, not this staff URL (§8.6).

| Method | Path | Name | Purpose |
|---|---|---|---|
| GET | `/atmos/staff/` | `staff_home` | Venue picker |
| GET | `/atmos/staff/<slug:venue_slug>/kds/` | `kds` | The order screen |
| GET | `/atmos/staff/<slug:venue_slug>/kds/poll/` | `kds_poll` | HTMX partial, 4 s |
| POST | `/atmos/staff/order/<uuid:pk>/status/` | `order_set_status` | Transition |
| POST | `/atmos/staff/guest/<uuid:pk>/extend/` | `guest_extend` | + window |
| POST | `/atmos/staff/tab/<uuid:pk>/extend/` | `tab_extend` | + window, all guests |
| POST | `/atmos/staff/guest/<uuid:pk>/rename/` | `guest_rename` | Force a fresh alias (§3.6 backstop) |
| POST | `/atmos/staff/tab/<uuid:pk>/settle/` | `tab_settle` | Settlement, whole table |
| POST | `/atmos/staff/guest/<uuid:pk>/settle/` | `guest_settle` | Settlement, one person |
| POST | `/atmos/staff/item/<uuid:pk>/availability/` | `item_toggle` | The 86-button |
| GET | `/atmos/staff/<slug:venue_slug>/qr/` | `qr_sheet` | Printable A4 QR sheet |
| POST | `/atmos/staff/table/<uuid:pk>/rotate-qr/` | `qr_rotate` | New token, kills old stickers |

Menu, table and venue CRUD run through **Django admin** for the MVP, registered on the existing `power_up_admin_site` as well as the default site.

---

## 7. Flows

### 7.1 First guest at a table

**Shipped vs. spec:** the flow below is materially unchanged except step 6 — there is no `expires_at` to set (§5), and step 5's "taps Give me a silly one" is really "sees a persona already rolled and taps confirm, or rerolls first" (§3.1).

1. Scan → `GET /atmos/t/<qr_token>/`.
2. Resolve `Table` by token; 404 on unknown or inactive.
3. If `venue.service_open` is false → "ordering closed", stop.
4. No `atmos_guest` cookie → join the table's open Tab or create one, then redirect to `/atmos/join/`.
5. Guest sees a rolled persona, optionally rerolls (a page reload, not an HTMX call — §3.1), laughs, confirms — or types a name instead.
6. `guest_join`'s POST handler creates the Guest inside `transaction.atomic()`, sets a signed cookie (`HttpOnly`, `SameSite=Lax`, `Secure` in production, `max_age` 6 h — this part shipped exactly as drafted), redirects to the menu.
7. Browse → cart (server-side Django session, **not** `localStorage` — phones get shared and browsers drop storage; a server-side cart is one staff can recover) → `POST /atmos/order/place/`.
8. Order created atomically; totals computed server-side from current prices, then snapshotted along with the alias — and a thermal ticket prints (§3.7).
9. Status page shows the `short_code` and the alias.

### 7.2 Second person at the same table

Scans the same sticker. No cookie → step 4 finds the **existing open Tab** and adds a second Guest to it. They roll their own name. Both guests order independently; the KDS groups them under Table 12.

### 7.3 Same person rescanning

Cookie present and the Guest is `active` → straight to the menu, no naming screen, no duplicate Guest. Cookie present but the Guest is `settled` (table turned over) → treat as new: fresh Guest, fresh roll. Getting this wrong in either direction is the most likely source of ghost guests on the board.

### 7.4 Staff KDS

1. Open `/atmos/staff/<venue>/kds/` on a tablet.
2. Columns: **New** (`placed`), **In progress** (`accepted`, `preparing`), **Done** (`served`, last 30 min), plus a **Needs attention** tray for expired guests (§5.2).
3. Polls `kds_poll` every 4 s via `hx-get` + `hx-trigger="every 4s"`, swapping only the board container.
4. Card shows: **alias in the largest type on the card**, table label, short code, age in minutes (amber past 5, red past 10), item lines with notes, an alcohol flag, one primary action button.
5. Actions POST and return the re-rendered board — no full reload, no lost scroll.
6. A chime on new arrivals, behind a one-time "enable sound" tap because browsers block autoplay.

**On polling frequency:** 4 s across three staff tablets is 45 requests/minute against one indexed query. Do not reach for WebSockets at this size — Channels would add Redis and an ASGI requirement to a prototype that must deploy on the existing WSGI App Service with no new infrastructure.

---

## 8. Security and correctness

### 8.1 Guest authorisation

**Shipped vs. spec:** the security boundary holds, but the mechanics differ from "403s if absent, settled or removed." Most guest views use `_get_guest()`, which requires `status="active"` — an absent, settled, or removed guest resolves to `None` and gets a normal `no_session.html` page, not a 403. `order_status` deliberately uses a more permissive resolver, `_get_guest_for_viewing()`, that drops the `active`/tab-open requirement — a **settled** guest can still open their own already-placed order's status page after the tab closes, on purpose (otherwise the guest loses visibility into an order the bar is still fulfilling the moment staff close the tab). Object access still always goes *through* the guest either way: `guest.orders.get(pk=...)`, never `Order.objects.get(pk=...)`. Knowing another order's UUID is not enough to view it. A guest can see their own orders, not their table-mates' — that boundary is intact even where the resolver is looser.

### 8.2 Double-submission — **not built**

Bar wifi is bad and guests tap twice. The design below (`order_place` carrying an `idempotency_key` minted when the cart page renders, stored with a unique constraint, so a replay returns the existing order instead of a second round of drinks) was never implemented — `Order` has no `idempotency_key` field (§4.8). A double-tap on `POST /atmos/order/place/` today places two orders. This is the one open correctness gap the doc sync (2026-08-20) didn't have an existing mitigation to point to instead of the drafted one — worth prioritising if a real bar runs on this.

### 8.3 Alias-roll abuse — shipped, stricter than drafted

Rerolling (§3.1) is cheap but unauthenticated, and there's no separate `alias_roll` endpoint to target (§6.1) — the whole `guest_join` view is rate-limited instead, keyed on (client IP, tab, HTTP method) via `_join_rate_limited()`. Shipped limit is **60 requests / 60 seconds** per key, tighter than the draft's "60 rolls / 5 min." Fails open (returns not-limited) on a cache/Redis outage rather than 500ing the join page.

### 8.4 Personal data and retention

- `alias` is not personal data (no `alias_noun` / `alias_rank` split — §3.3, §4.7). It is kept with the order history as the venue's own record.
- `display_name` **is** personal data. It is purged (set to `""`, with its guest's past `Order.alias_snapshot` rows reset first) when the **tab closes** (`Tab.save()`, §4.9) — not on a per-guest "settle" moment in isolation, since there is no such staff action (§4.9). A guest an admin marks `removed` before the tab closes keeps its `display_name` until the tab-close purge runs, not the other way round.
- A separate management command, `purge_stale_guest_names`, exists for a cutoff-based sweep independent of tab closure — but per its own code comment it is **dry-run by default and has no scheduled invocation on this platform** (no cron/Function calls it). The tab-close purge above is what actually protects the "we only keep this for tonight" promise in production today; the standalone sweep is a manual/future backstop, not a running safeguard.
- No IP, device fingerprint, or contact detail is stored against a Guest. The cookie holds a UUID and nothing else.
- Guests are told, in one line on the naming screen: *"Anonymous & temporary."* Wording shipped slightly differently from the draft's *"We only keep this for tonight"*, same intent.

### 8.5 Alcohol

Luxembourg prohibits selling alcohol to under-16s (under-18 for spirits). A QR web app cannot verify age and should not pretend to. Atmos therefore shows a non-blocking notice on alcohol items, flags alcohol-containing orders on the KDS card so staff **check at delivery**, and records nothing about any guest's age. The legal check stays with the human handing over the glass — which is where it already is, and where it is defensible.

The alias system must not undercut this. There is no rank or escalation to tie to drinks in the first place (§3.3), and no alias, badge or copy anywhere in the product congratulates a guest for volume.

### 8.6 Abuse from a photographed QR

Anyone who photographs a sticker can order to that table from outside. Of the drafted mitigations, the **2-hour window does not cap exposure today** — it isn't enforced (§5), so a leaked sticker is good for as long as the tab stays open, not two hours. What does hold: `service_open` kills all ordering venue-wide, staff can remove or settle a guest via the admin (§4.7/§4.9), per-Tab join rate limits apply (§8.3), and `qr_rotate` invalidates a leaked token — shipped as a **Django admin action** on `TableAdmin`, not the `/atmos/staff/table/<uuid:pk>/rotate-qr/` staff URL drafted in §6.2. Since payment is on delivery, financial exposure is zero either way — an unserved order costs nothing.

### 8.7 CSRF, CSP

- All mutations are POST with CSRF tokens. `CSRF_COOKIE_HTTPONLY` is on, so reuse the platform's existing helper `crush_lu/static/crush_lu/js/htmx-csrf.js`, which reads `#csrf-token-input` and sets `hx-headers` on `<body>`.
- HTMX 2.0.4 is CDN-loaded in existing templates and **must carry `nonce="{{ csp_nonce }}"`** — Django 6.0's native `ContentSecurityPolicyMiddleware` is enabled, so an unnonced script tag is blocked.
- Avoid inline handlers. If Alpine is needed, follow `crush_lu/STYLE.md` and its `mixin()` helper rather than `Object.assign`.

---

## 9. Testing

`pytest.ini` sets `testpaths` to `crush_lu/tests hub/tests power_up/finops/tests`. Atmos tests go in `power_up/atmos/tests/` with `test_*.py` names, and **`testpaths` must be extended** or they will silently never run. Note `-x` (stop on first failure) and `--reuse-db`; run `pytest --create-db` once after the first migration lands.

| Area | Cases |
|---|---|
| Scan | New Tab on first scan; second person joins same Tab; returning cookie skips naming; settled cookie starts fresh; 404 on bad/inactive token |
| Alias | Roll writes nothing; collision on confirm rerolls silently; noun unique per venue among unsettled; rank derived correctly at 0/29/31/61/91/121 min; escalation off → always "Captain" |
| Names | Over-length rejected; blocklist hit returns an alias with no explanation; staff rename issues a fresh unused noun |
| Window | `expires_at` set from venue config; warning state at T-15; ordering blocked at T-0 with cart intact; extend moves the boundary and increments count; settle purges `display_name` |
| Cart | Add/update/remove; unavailable item rejected; cross-venue item rejected |
| Order | Totals and snapshots correct; `alias_snapshot` frozen across a rank change; idempotency replay returns the same order; atomic rollback on failure |
| Status | Legal transitions succeed, illegal raise, terminals are terminal |
| Authorisation | Guest cannot read a table-mate's order; anonymous gets 302/403 on every staff URL; KDS poll returns only its own venue |

---

## 10. Build plan

| # | Step | Output |
|---|---|---|
| 1 | Scaffold | `power_up/atmos/` package, `AtmosConfig`, `INSTALLED_APPS`, URL include |
| 2 | Models + migration | Ten models, constraints, indexes, `transition_to()`, `Guest.display` |
| 3 | Alias engine | Word lists (EN/DE/FR), rank ladder, uniqueness, blocklist — pure functions, unit-tested first |
| 4 | Admin | Default + `power_up_admin_site`; inlines for categories/items/tables |
| 5 | Seed command | `manage.py seed_atmos_demo` — one venue, 12 tables, ~25 items at realistic Luxembourg prices |
| 6 | Naming screen | Scan → roll → confirm, the whole first-impression flow |
| 7 | Guest flow | Menu, cart, place, status |
| 8 | Staff KDS | Board, polling partial, transitions, 86-button |
| 9 | Window + settle | Warning/expired states, extend, settle, Needs-attention tray |
| 10 | QR sheet | Printable A4 — `qrcode[pil]==8.2` and `reportlab` are already in `requirements.txt` |
| 11 | Tests | Per §9; extend `testpaths` |
| 12 | Polish | Empty states, EN/DE/FR strings, mobile pass at 375 px |

Steps 1–5 make the model real and demoable through admin alone. **Step 6 is the one to show people first** — the naming screen is what the concept lives or dies on, and it can be user-tested on a phone before the menu even works.

---

## 11. Open questions

1. **Luxembourgish.** The platform runs EN/DE/FR, but funny names in a Luxembourg bar land hardest in LB. Adding a fourth locale for the alias list only — not the whole UI — is cheap and might be the single highest-value tweak. Worth it?
2. **Is 2 hours right,** and should the clock start on join or on first order? A guest who scans, gets distracted, and orders 40 minutes later loses a third of their window for no reason.
3. **Does the bar want an *accept* step at all,** or should `placed` go straight to `preparing`? An extra tap per order is real friction on a busy night.
4. **One KDS for the venue, or split bar/kitchen queues?** Drinks and food have very different timings and mixing them hides the slow item.
5. **Rank escalation on or off for the pilot?** Running it off for the first hour and on for the second is a cheap way to see whether the ladder adds anything over a plain alias. *Resolved by omission: the pilot shipped with the plain single-alias design and no ladder at all (§3.3) — this remains open only if a future pass wants to revisit it.*
6. **Which bar is the pilot,** and on what hardware — their till browser or a tablet you supply?

---

## 12. Remote Treat & Social Drink Gifting (Spec Extension)

> ⚠️ **NOT YET BUILT.** Everything in this section is a proposal, not a shipped feature. There is no `TreatLink` or `TreatPayment` model, no `/atmos/treat/<token>/` route, and no gift-related field on `Order` (`is_gift`, `payment_status`, or otherwise) anywhere in `power_up/atmos/` — confirmed by a repo-wide search: these names appear only in this document. The thermal-ticket printing infrastructure this section assumes (§12.2 step 3) *is* real and shipped (§3.7), but nothing wires a paid remote gift into it yet. Treat this section as a design record for future work, not a description of current behavior.

### 12.1 Concept & Value Proposition
A guest at a table (e.g. Table 4 with persona *"The Whispering Gambler"*) can share a link over social media (Instagram Story, WhatsApp, Discord, X/Twitter, SMS) allowing anyone—friends, dates, admirers, followers, or remote family—to **buy them a drink at their table in real-time**.

Unlike regular orders (which are settled at the table by default), **remote treat orders require 100% advance online payment** before reaching the staff KDS or printing on the thermal ticket.

### 12.2 User Journey
1. **In-Venue Guest**:
   - Taps *"🎁 Ask for a Drink / Share Table"* on the menu or order status screen.
   - Generates a unique, expiring treat link: `https://power-up.lu/atmos/treat/<token>/`.
   - Native share sheet / 1-tap copy with pre-filled noir caption:
     *"I'm at Table 4 at The Velvet Hour as 'The Whispering Gambler'. Buy me a drink! 🍸 [link]"*
2. **Remote Gifter**:
   - Opens the link in any mobile/desktop browser.
   - Sees the venue, table, recipient persona, and available drinks.
   - Chooses a drink from the menu.
   - Adds an optional Gifter Name / Handle (e.g., *"Sarah from London"*) and a personal message (e.g., *"Cheers from afar! Have one on me."*).
   - Pays online via SumUp (Apple Pay, Google Pay, Credit Card).
3. **Fulfillment & In-Venue Experience**:
   - On payment confirmation (`PAID` webhook/return):
     - An `Order` record is created on the guest's active `Tab` with `is_gift=True` and `payment_status="paid_online"`.
     - A thermal ticket immediately prints on the bar's `POS-80C` printer with a prominent `🎁 REMOTE GIFT / TREAT (PAID ONLINE)` header, the gifter's name, and the personal note.
     - The AI chronicle weaves the remote gift into the noir lore.
     - The Staff KDS displays the order with a green `GIFT (PAID)` badge (staff knows not to charge the guest).
     - The in-venue guest receives a live toast alert on their screen: *"🎉 Sarah from London just bought you an Old Fashioned!"*

### 12.3 Data Model Additions
- **`TreatLink`**:
  - `id`: UUID primary key
  - `guest`: FK to `Guest` (cascade delete)
  - `token`: urlsafe unique token (indexed)
  - `is_active`: Boolean (invalidated when tab is closed or guest leaves)
  - `created_at`: DateTime
- **`TreatPayment`**:
  - `treat_link`: FK to `TreatLink`
  - `order`: FK to `Order` (null until fulfilled)
  - `gifter_name`: CharField(64)
  - `gifter_message`: CharField(255)
  - `menu_item`: FK to `MenuItem`
  - `amount`: Decimal(6, 2)
  - `sumup_checkout_id`: CharField(128, unique)
  - `status`: `pending` | `paid` | `refunded` | `failed`

### 12.4 Abuse & Safety Mitigations
- **Table Active Guard**: If staff close the tab or deactivate the table before the remote payment completes, the payment cannot place an order and is refunded/rejected.
- **Message Moderation**: `gifter_message` and `gifter_name` pass through sanitization / blocklist checks before rendering on paper or screens.
- **Alcohol Duty of Care**: Tickets for remote-gifted alcoholic drinks retain the prominent `ALCOHOL - CHECK AT TABLE` banner so bartenders still verify recipient age and sobriety.

