# Crush Empire — Integration Plan (Django + Azure, with server-side saves & swipe mechanic)

This plan slots the idle game into your existing **Multi-Domain-Django-Platform** as a first-class page of the `crush_lu` app — no new infrastructure, no separate service. It reuses your allauth auth, DRF stack, multi-domain routing, and the Azure App Service you already deploy to. It also adds the requested **swipe-to-earn** twist and **server-side save state** so progress survives logout and syncs across devices.

---

## 1. The concept twist: swipe-to-earn as an in-joke

Crush.lu deliberately has *no* swipe feature — that's a brand position. The game leans into that: a fake, over-the-top "dating app simulator" inside your real, non-swipe platform.

- Core action becomes **swiping cards** instead of a click button. Each card is a tongue-in-cheek "profile" (e.g. *"Loves long walks to the fridge"*, *"Fluent in three dead languages"*). Swipe right = +points, swipe left = a smaller consolation point ("at least you're honest").
- Every ~20 swipes a **meta card** interrupts: *"Tired of swiping? Real people don't. → Join Crush.lu"* — the funnel, delivered as the punchline.
- The idle layer stays: swipe points buy **matchmakers** that auto-generate points, so the joke is "we automated the swiping we don't even believe in."
- Framed on the page as *"Crush Empire — the swipe game by the app that refuses to make you swipe."*

This keeps the mechanic fun, on-brand, and self-aware rather than contradicting the product.

---

## 2. Where it lives in the codebase

Everything goes in the existing `crush_lu` app, following its established split-module conventions:

| Concern | Location | Convention it follows |
|---|---|---|
| Game state model | `crush_lu/models/game.py` (+ export in `models/__init__.py`) | Same as `models/quiz.py`, `models/journey.py` |
| Save/load API | `crush_lu/api_game.py` | Same shape as `api_pwa.py` (`@login_required`, `@csrf_protect`, `JsonResponse`) |
| Page view | `crush_lu/views_game.py` | Same as `views_quiz.py` etc. |
| Routes | add to `azureproject/urls_crush.py` inside the i18n block | Language-prefixed `/en/ /de/ /fr/` like the rest of Crush |
| Template | `crush_lu/templates/crush_lu/game/empire.html` | Extends your existing Crush base template |
| Front-end JS/CSS | `crush_lu/static/crush_lu/js/empire.js`, `.../css/empire.css` | App-level static, collected by `collectstatic` |
| Admin (optional) | `crush_lu/admin/game.py` + register on `crush_admin_site` | Same as your other admin modules |

No new Django app, no new domain entry in `domains.py` — it's a page on `crush.lu`.

---

## 3. Data model (server-side save)

One row per user holds the whole save. Keep the balance/economy definitions in code (a versioned config), and store only the player's *progress* in the DB — this keeps saves small and lets you rebalance without migrating data.

```python
# crush_lu/models/game.py
from django.conf import settings
from django.db import models

class GameState(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE, related_name="game_state")
    # Progress (authoritative-ish; see anti-cheat §7)
    points = models.BigIntegerField(default=0)
    total_earned = models.BigIntegerField(default=0)
    total_swipes = models.IntegerField(default=0)
    hearts = models.IntegerField(default=0)          # prestige currency
    generators = models.JSONField(default=dict)       # {"0": 12, "1": 3, ...}
    upgrades = models.JSONField(default=list)         # [0,1,3]
    # Meta
    schema_version = models.PositiveSmallIntegerField(default=1)
    last_seen = models.DateTimeField(auto_now=True)   # for offline-earnings calc
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["-total_earned"])]  # leaderboard-ready
```

`JSONField` works on both your SQLite dev DB and Postgres prod, so no dialect issues. `total_earned` index gives you a leaderboard for free later.

Migration: `python manage.py makemigrations crush_lu && migrate` — runs as part of your normal deploy (see §6).

---

## 4. Save/load API (reuses your auth + CSRF patterns)

Two endpoints, mirroring `api_pwa.py` exactly so they inherit your session auth, CSRF, and DRF config:

```python
# crush_lu/api_game.py
@login_required
@require_http_methods(["GET"])
def game_load(request):
    state, _ = GameState.objects.get_or_create(user=request.user)
    payload = serialize(state)
    payload["offline_earned"] = compute_offline(state)   # optional idle-while-away bonus
    return JsonResponse({"ok": True, "state": payload})

@login_required
@csrf_protect
@require_http_methods(["POST"])
def game_save(request):
    data = json.loads(request.body)
    state, _ = GameState.objects.get_or_create(user=request.user)
    apply_validated(state, data)   # clamp/validate, bump schema, save
    return JsonResponse({"ok": True, "saved_at": state.last_seen.isoformat()})
```

Client save cadence: autosave every ~15s and on `visibilitychange`/`beforeunload` (you already do device-fingerprint POSTs in the PWA code, so the pattern is proven). Debounce to avoid hammering the DB.

**Auth flow already solved by your stack:**
- Logged-in users → session cookie from allauth; API calls just work with the CSRF token.
- Logged-out users → game runs on `localStorage` (as the prototype does today). On login, do a **one-time merge**: if the server state is empty, push the local save up; otherwise let the user pick "keep cloud" vs "keep this device." This is the standard idle-game reconciliation and avoids clobbering progress.

---

## 5. Front-end: porting the prototype into a Django page

The current `crush-empire.html` becomes a template + external JS:

1. Move the `<style>` into `static/crush_lu/css/empire.css` and the `<script>` into `static/crush_lu/js/empire.js`; load with `{% static %}`.
2. Template `empire.html` extends your Crush base layout so it gets the nav, i18n, and PWA shell for free.
3. Replace the round click button with a **swipe card stack** (touch + mouse drag; a ~100-line vanilla gesture handler, no library needed — or Hammer.js if you prefer). Right/left swipe calls the same `addPoints()` the click did.
4. Swap `localStorage`-only persistence for a small `Store` layer: authenticated → hit `game_load`/`game_save`; anonymous → `localStorage`. Same interface either way.
5. Wire copy through your i18n (`{% trans %}` / `gettext`) so the joke lands in EN/DE/FR like the rest of Crush.

Because it's server-rendered and uses your existing static pipeline, it's also automatically inside the PWA — installable, works the same as your other Crush pages.

---

## 6. Azure hosting & deploy (nothing new to provision)

It ships through your current App Service + deploy flow:

- **Compute:** the game is just more Django views/static on the App Service already serving `crush.lu`. No new plan, container, or Static Web App needed.
- **Static files:** `empire.js/.css` are collected by `collectstatic` in your existing `startup.sh` and served the way your other Crush static assets are (WhiteNoise / Azure Blob via `django-storages`, whichever you're on). No CDN change required.
- **Database:** the one `GameState` migration applies during deploy. If your `startup.sh` runs `migrate`, it's automatic; if not, run it once via the `azure-django` path (`az webapp ssh` / `manage.py migrate` in the container).
- **Config:** no new env vars, secrets, or Key Vault entries. It uses the same DB and auth already configured.
- **Rollout:** merge to your deploy branch → GitHub Actions (`.github/`) builds and deploys as usual. Feature-flag it behind a settings flag (you already use kill-switches like the Coach Review one) so you can dark-launch and enable when ready.

Practical sequence: build the model + API + page locally against `db.sqlite3`, run the dev server, verify save/load and swipe, commit, deploy, run the migration on prod, flip the flag.

---

## 7. Anti-cheat / integrity (because points now live server-side)

Client-authoritative idle games are trivially cheatable (edit a JS variable → infinite points). Since these saves are on your servers and may back a leaderboard, add light server validation — full server simulation is overkill for a joke game, so aim for "good enough to stop casual tampering":

- **Rate-clamp on save:** reject deltas that exceed the theoretical max production since `last_seen` (you know owned generators × rates × elapsed time + a swipe cap). Clamp instead of hard-reject to stay friendly.
- **Server-owned time:** compute offline earnings server-side from `last_seen`, never trust a client timestamp.
- **Sane bounds:** cap per-request swipe counts and point jumps; log outliers rather than banning.
- Treat the leaderboard (if you add one) as "for fun" and moderatable. Don't attach any real reward to scores.

This is proportional: enough to keep it fair, not so much that it becomes a real-money-game backend.

---

## 8. Suggested build order (small, shippable steps)

1. **Model + migration** for `GameState` (behind a `CRUSH_GAME_ENABLED` flag, default off).
2. **API** `game_load` / `game_save` with validation, tested against SQLite.
3. **Port the prototype** into template + static, still `localStorage`, to confirm it renders inside Crush's layout and i18n.
4. **Wire the Store layer** to the API for logged-in users; add the login-merge reconciliation.
5. **Swap in the swipe card stack** + the meta funnel cards.
6. **Deploy to a `test.crush.lu` alias** (you already have test aliases in `domains.py`), migrate, QA on mobile.
7. **Flip the flag** on prod; add UTM on the "Join Crush.lu" cards to measure funnel lift.
8. *(Optional later)* leaderboard from the `total_earned` index; seasonal card packs; achievements.

Each step is independently testable and reversible via the flag.

---

## 9. Effort estimate

| Phase | Rough effort |
|---|---|
| Model + API + validation | ~half a day |
| Port prototype to template/static + i18n | ~half a day |
| Swipe card stack + funnel cards | ~1 day (gesture polish is the variable) |
| Cloud save + login merge | ~half a day |
| Deploy, migrate, QA, flag rollout | ~half a day |
| **Total to a shippable v1** | **~3 days of focused work** |

The heavy lifting — auth, multi-domain routing, i18n, static pipeline, Azure deploy — already exists. You're adding one model, two endpoints, one page, and a gesture handler.

---

### One open decision for you
Do you want anonymous play at all, or **login-gated** (must be signed in to play)? Login-gating is simpler (no localStorage merge, every save is server-side, and it doubles as a sign-up incentive — *"log in to save your empire"*), and it reinforces the funnel. Anonymous play maximizes reach but needs the merge logic in §4. My recommendation: launch **login-gated** for v1, add anonymous later if you want top-of-funnel reach.
