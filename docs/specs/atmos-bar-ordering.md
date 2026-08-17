# Atmos — QR Bar Ordering System

**Status:** Draft spec v2 (prototype / MVP)
**Owner:** Tom Scheuer
**Date:** 2026-08-16
**Mount point:** `power_up.atmos` submodule, served at `power-up.lu/atmos/`
**Changes in v2:** per-guest identity with funny aliases (§3), guests split from tabs in the data model (§4), a 2-hour per-guest ordering window with staff *settle-or-extend* (§5), settlement records (§4.9).
**Out of scope:** online payment, venue self-service backoffice — see [Deferred](#12-deferred-post-mvp).

---

## 1. Concept

Guests in a bar scan a QR code glued to their table. No app install, no login. The QR opens a mobile web menu already bound to that table. They add drinks or food to a cart and place the order. Behind the bar, a staff screen shows incoming orders live and lets staff move them through *new → in progress → served*.

Payment stays offline for the MVP: the guest pays the server at the table or at the counter as they do today. This removes PSP onboarding, PCI scope, and refund handling from the prototype while still proving the core value.

**What makes Atmos different from every other QR ordering system:** the delivery moment. When the barkeeper arrives with a tray and says *"two Pilsner for Colonel Pretzel?"*, the table laughs. That laugh is the product. Ordinary QR ordering systems remove a human interaction and give you nothing back; Atmos removes the *waiting* and keeps the *contact*, with a joke attached.

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

The guest UI needs EN/DE/FR — this is Luxembourg. But putting it inside `i18n_patterns` means every QR code encodes a language prefix, and a table's printed QR would be locked to one language forever. Instead: mount language-neutral, ask for the language on the name screen (it is the guest's first interaction anyway), and store the choice on the guest record.

### 2.2 Background work

None. Per `CLAUDE.md`, production runs the `ImmediateBackend` inline and no `db_worker` exists — `.enqueue()` defers nothing. Every Atmos operation is bounded and in-request by design. **Note this specifically for the 2-hour window (§5):** expiry is computed on read, never by a scheduled job, because there is no scheduler to run one.

---

## 3. Guest identity — the funny name

### 3.1 The screen

The first thing a guest sees after scanning. One question, two ways out, both fast.

```
        You're at Table 12.
   What should we shout when
     your drink is ready?

  ┌───────────────────────────┐
  │  🎲  Give me a silly one  │   ← primary, big, thumb-height
  └───────────────────────────┘

     ─────  or type it  ─────

  ┌───────────────────────────┐
  │  Your name                │
  └───────────────────────────┘
          [ That's me → ]

        EN · DE · FR
```

Tapping **"Give me a silly one"** rolls an alias and shows it instantly with the roll button still there:

```
     You are now

   CAPTAIN PRETZEL

  [ 🎲 Roll again ]  [ Let's go → ]
```

Rolling is free and unlimited *before* confirming. This is where the fun is manufactured — a table of four rerolling and reading names out to each other is thirty seconds of entertainment before a single drink is ordered, and it costs one database-free function call per tap.

### 3.2 Anonymous is the default, and that is deliberate

The alias button is visually dominant, listed first, and requires one tap versus a keyboard interaction. Three reasons, in order of importance:

1. **It is faster.** Typing on a phone in a dark, loud bar is the single worst step in the flow.
2. **It is funnier**, which is the point of the product.
3. **It avoids collecting personal data.** A typed first name is personal data under GDPR; "Captain Pretzel" is not. Making the privacy-preserving option also the fun option means we do not have to talk anyone into it. See §8.4 for retention.

### 3.3 Alias structure: stable noun, escalating rank

An alias is **`<Rank> <Noun>`** — e.g. *Captain Pretzel*, *Major Otter*, *Colonel Waffle*.

You asked whether names could rotate to keep things fun. Straight rotation breaks the product: if a guest is *Captain Pretzel* when they order and *Baroness Gherkin* when the tray lands, the barkeeper cannot deliver the drink and the joke becomes an argument. So the rotation is put where it is safe:

- **The noun never changes** for the life of the guest record. It is the delivery key. Staff learn it, guests answer to it.
- **The rank escalates** as the evening goes on, so the same person is *Captain Pretzel* at 9pm and *Supreme Overlord Pretzel* at midnight. Same recognisable name, visibly promoted, and the escalation is itself the running joke.
- **Rerolling is unlimited before confirming**, none after.

Rank ladder (deliberately gender-neutral — an anonymous guest has no gender to assume):

| Tier | Rank | Unlocked at |
|---|---|---|
| 1 | Captain | on join |
| 2 | Major | 30 min at the table |
| 3 | Colonel | 60 min |
| 4 | General | 90 min |
| 5 | Supreme Overlord | 120 min |

**Escalation is tied to time seated, not to drinks ordered.** This is a deliberate reversal of the obvious design. Ranking people up per drink is the more natural gamification and it is the wrong thing to build: it makes the app reward faster drinking, in a bar, where the app also removes the friction of catching a waiter's eye. Time-based escalation produces exactly the same joke, rewards guests for staying, and gives the venue longer table dwell — which is what the venue actually wants anyway. If a future version wants order-linked flourishes, tie them to *variety* (trying a new category) rather than volume.

### 3.4 Word lists

Curated static lists per language, not a free-form generator. Combining unreviewed adjectives with unreviewed nouns eventually produces something that gets the bar in trouble; a fixed list of ~60 nouns can be read once by a human and signed off.

Constraints on every noun, all of which come from the fact that a stressed barkeeper has to **shout it across a noisy room**:

- Maximum 3 syllables, ideally 2. *Bartholomew Quixotic-Fizzwhistle* is unusable.
- Total alias ≤ 20 characters so it fits on a KDS card without wrapping.
- Pronounceable by an EN, DE and FR speaker alike.
- Absurd but affectionate. Nothing that mocks appearance, and nothing drink-related that reads as a comment on how much someone has had.

Starter lists (illustrative, to be finalised):

| Lang | Nouns |
|---|---|
| EN | Pretzel, Waffle, Pickle, Noodle, Muffin, Popcorn, Cashew, Olive, Radish, Truffle, Otter, Puffin, Walrus, Ferret, Hedgehog, Wombat, Gecko, Toucan, Llama, Moose, Penguin, Flamingo, Hamster |
| DE | Brezel, Waffel, Gurke, Nudel, Muffin, Olive, Rettich, Trüffel, Otter, Walross, Frettchen, Igel, Wombat, Gecko, Tukan, Lama, Elch, Pinguin, Flamingo, Hamster |
| FR | Bretzel, Gaufre, Cornichon, Nouille, Muffin, Olive, Radis, Truffe, Loutre, Macareux, Morse, Furet, Hérisson, Wombat, Gecko, Toucan, Lama, Élan, Manchot, Flamant, Hamster |

**Each list needs a native-speaker pass, not a translation.** Innocuous animals are slang insults in one language and not another — French *blaireau* (badger) means "idiot", English *beaver* carries crude slang, and neither problem is visible from the other side. Both are already excluded above. The lists deliberately do not mirror each other one-for-one for this reason.

### 3.5 Uniqueness

Two *Captain Pretzels* in one venue is a delivery failure, so the noun is unique **per venue among non-settled guests**. The generator picks from the unused pool; if a venue somehow exhausts ~60 nouns with concurrent guests, it falls back to appending a digit (*Pretzel II*) rather than failing the scan. A guest scanning in must never see an error because the bar is busy.

### 3.6 Typed names need moderation

The free-text path puts guest-controlled text on a staff screen that gets read aloud. Untreated, someone will type a slur on their first visit and a member of staff will shout it. Minimum handling:

- Length capped at 20 characters, letters/spaces/hyphens/apostrophes only.
- Checked against a blocklist per language. On a hit, no scolding — just *"Let's try a silly one instead"* and a pre-rolled alias. Never explain what was rejected; that turns the filter into a game.
- Staff can override any name from the KDS to a fresh alias in one tap, which is the real backstop. Filters miss things; a barkeeper does not.

### 3.7 Where the alias shows up

- KDS order card, largest text on the card.
- The guest's own status page (*"Colonel Waffle — 2 Pilsner on the way"*).
- The printable table receipt if one is ever added.
- **Not** in analytics, exports, or logs beyond the venue's own order history.

---

## 4. Data model

Single `power_up/atmos/models.py`. All primary keys are `UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`, matching `power_up.crm` — and here it also prevents anyone enumerating other tables' orders from a URL.

### 4.1 Entity overview

The v1 `TableSession` splits in two. A **Tab** is the table's visit; a **Guest** is one person on it. Orders hang off the Guest.

```
Venue ─┬─ Table ──── Tab ──┬── Guest ──── Order ──── OrderItem
       │                   │                │
       │                   └── Settlement ──┘
       └─ MenuCategory ──── MenuItem ────────┘
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
| `accepts_orders_from` / `_until` | TimeField, null | Soft service window |
| `guest_window_minutes` | PositiveSmallIntegerField, default `120` | The 2-hour window, per venue |
| `guest_window_extension_minutes` | PositiveSmallIntegerField, default `120` | What "+time" grants |
| `alias_escalation_enabled` | BooleanField, default `True` | Lets a venue turn the rank ladder off |
| `order_note_enabled` | BooleanField, default `True` | |
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

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | Stored in the guest's signed cookie |
| `tab` | FK → Tab, `related_name="guests"` | |
| `alias_noun` | CharField(24) | Stable. The delivery key |
| `alias_rank` | PositiveSmallIntegerField, default 1 | 1–5, derived from time seated |
| `display_name` | CharField(20), blank | Only if typed. Purged on settle (§8.4) |
| `is_anonymous` | BooleanField, default `True` | False when `display_name` is set |
| `language` | CharField(5), default `"en"` | |
| `joined_at` | DateTimeField, auto_now_add | |
| `expires_at` | DateTimeField | `joined_at + venue.guest_window_minutes` |
| `extension_count` | PositiveSmallIntegerField, default 0 | |
| `last_activity_at` | DateTimeField, auto_now | |
| `status` | CharField: `active`, `expired`, `settled`, `removed` | |
| `settled_at` | DateTimeField, null | |

Constraint: `UniqueConstraint(fields=["tab__venue", "alias_noun"], condition=~Q(status="settled"))` — expressed in practice as a partial unique index on a denormalised `venue` FK, since Django cannot constrain across a join.

```python
@property
def display(self) -> str:
    """What staff see and shout."""
    if self.display_name:
        return self.display_name
    if not self.tab.venue.alias_escalation_enabled:
        return f"Captain {self.alias_noun}"
    return f"{RANKS[self.alias_rank]} {self.alias_noun}"
```

`alias_rank` is **recomputed on read** from `joined_at`, never by a background job — see §2.2.

### 4.8 `Order` / `OrderItem`

`Order` gains a `guest` FK; everything else is unchanged from v1.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `guest` | FK → Guest, `related_name="orders"` | |
| `tab` | FK → Tab, `related_name="orders"` | Denormalised |
| `venue` | FK → Venue, `related_name="orders"` | Denormalised for the KDS query |
| `short_code` | CharField(6), db_index | `T12-04` |
| `alias_snapshot` | CharField(32) | **The alias as displayed when placed** |
| `status` | CharField: `placed`, `accepted`, `preparing`, `served`, `cancelled` | |
| `note` | TextField, blank | |
| `placed_at` / `accepted_at` / `served_at` / `cancelled_at` | DateTimeField | Timestamps are the prototype's metrics |
| `cancel_reason` | CharField(160), blank | |
| `idempotency_key` | UUIDField, unique | §8.2 |
| `total_amount` | DecimalField(9,2) | Snapshot at placement |

`alias_snapshot` exists because rank escalates. An order placed by *Major Otter* must still say *Major Otter* on the KDS ten minutes later, even though the guest is now *Colonel Otter* — otherwise the card the barkeeper picked up no longer matches the tray they are carrying.

Index on `("venue", "status", "placed_at")` — the KDS's only hot query.

`OrderItem`: `order` FK, `menu_item` FK (`on_delete=PROTECT`), `name_snapshot`, `unit_price_snapshot`, `quantity`, `note`, `line_total`. Snapshots because the menu changes during service and an order is a record of what was agreed at that moment; recomputing from live prices silently rewrites last night's receipts.

### 4.9 `Settlement`

Not a payment integration — a record that a human paid a human, so the tab can close cleanly and the window mechanic has a resolution.

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

Settling every active guest closes the Tab.

### 4.10 Status model

```
placed ──→ accepted ──→ preparing ──→ served
   │           │            │
   └───────────┴────────────┴──────→ cancelled
```

Validated in `Order.transition_to(new_status, by_user)` on the model, not the view. Illegal transitions raise `ValidationError`; `served` and `cancelled` are terminal.

---

## 5. The 2-hour window

Each guest can order for `venue.guest_window_minutes` (default 120) from joining. When it runs out, staff are prompted to **settle up** or **add time**. This is the mechanic that turns an unpaid-online system into something a bar can actually run: it forces a payment conversation at a predictable moment instead of hoping someone remembers to ask.

It also quietly solves the photographed-QR problem (§8.6) — a leaked sticker grants at most two hours of nuisance ordering, and only while the venue is open.

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

1. Scan → `GET /atmos/t/<qr_token>/`.
2. Resolve `Table` by token; 404 on unknown or inactive.
3. If `venue.service_open` is false → "ordering closed", stop.
4. No `atmos_guest` cookie → join the table's open Tab or create one, then redirect to `/atmos/join/`.
5. Guest taps **"Give me a silly one"**, rerolls twice, laughs, confirms.
6. `guest_confirm` creates the Guest inside `transaction.atomic()`, sets `expires_at`, sets a signed cookie (`HttpOnly`, `SameSite=Lax`, `Secure` in production, `max_age` 6 h), redirects to the menu.
7. Browse → cart (server-side Django session, **not** `localStorage` — phones get shared and browsers drop storage; a server-side cart is one staff can recover) → `POST /atmos/order/place/`.
8. Order created atomically; totals computed server-side from current prices, then snapshotted along with the alias.
9. Status page polls every 5 s, showing the `short_code` and the alias.

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

Every guest view resolves the Guest from the signed cookie and 403s if absent, settled or removed. Object access always goes *through* the guest: `guest.orders.get(pk=...)`, never `Order.objects.get(pk=...)`. Knowing another order's UUID is not enough to view it. A guest can see their own orders, not their table-mates'.

### 8.2 Double-submission

Bar wifi is bad and guests tap twice. `order_place` carries an `idempotency_key` minted when the cart page renders, stored with a unique constraint; a replay returns the existing order instead of a second round of drinks. Without this the first real service produces duplicates and staff stop trusting the screen.

### 8.3 Alias-roll abuse

`alias_roll` is cheap but unauthenticated. Rate-limit per IP and per Tab (e.g. 60 rolls / 5 min) so it cannot be turned into a load generator, and keep it DB-free so the ceiling is high.

### 8.4 Personal data and retention

- `alias_noun` / `alias_rank` are not personal data. They are kept with the order history as the venue's own record.
- `display_name` **is** personal data. It is purged (set to `""`) when the guest is settled or the tab closes; `alias_snapshot` on past orders keeps history readable without it.
- No IP, device fingerprint, or contact detail is stored against a Guest. The cookie holds a UUID and nothing else.
- Guests are told, in one line on the naming screen: *"We only keep this for tonight."* Say it because it is true and because it makes the alias button feel like the safe choice as well as the fast one.

### 8.5 Alcohol

Luxembourg prohibits selling alcohol to under-16s (under-18 for spirits). A QR web app cannot verify age and should not pretend to. Atmos therefore shows a non-blocking notice on alcohol items, flags alcohol-containing orders on the KDS card so staff **check at delivery**, and records nothing about any guest's age. The legal check stays with the human handing over the glass — which is where it already is, and where it is defensible.

The alias system must not undercut this. Ranks escalate on time, not drinks (§3.3), and no alias, badge or copy anywhere in the product congratulates a guest for volume.

### 8.6 Abuse from a photographed QR

Anyone who photographs a sticker can order to that table from outside. Mitigations: the 2-hour window caps exposure, `service_open` kills all ordering, staff can settle or remove a guest, per-Tab rate limits apply, and `qr_rotate` invalidates a leaked token. Since payment is on delivery, financial exposure is zero — an unserved order costs nothing.

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
5. **Rank escalation on or off for the pilot?** Running it off for the first hour and on for the second is a cheap way to see whether the ladder adds anything over a plain alias.
6. **Which bar is the pilot,** and on what hardware — their till browser or a tablet you supply?
