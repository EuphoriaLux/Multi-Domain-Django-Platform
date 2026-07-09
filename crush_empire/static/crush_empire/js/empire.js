/*
 * Crush Empire — the swipe deck and shop.
 *
 * Deliberately vanilla, not Alpine. The prototype was vanilla, the hot path is a
 * 250ms tick plus a pointer-drag, and Alpine's CSP build cannot take arguments in
 * expressions — every `buyGen(3)` would need a data-attribute round trip anyway.
 * No inline handlers anywhere: everything is addEventListener, so the page needs
 * no CSP exceptions.
 *
 * The server owns every number. This file keeps a local mirror so the counter
 * ticks smoothly between requests, but any response from the API overwrites it.
 * Nothing here is ever sent back as a balance — only intents ("I swiped right").
 */
(function () {
  "use strict";

  const root = document.getElementById("empire");
  if (!root) return;

  const CSRF = root.dataset.csrf;
  const T = JSON.parse(root.dataset.i18n);
  const DECK = JSON.parse(document.getElementById("empire-deck").textContent);

  /** Authoritative state, replaced wholesale by every API response. */
  let S = JSON.parse(document.getElementById("empire-state").textContent);

  /* ── Formatting ──────────────────────────────────────────────────────── */

  const UNITS = ["", "K", "M", "B", "T", "Qa", "Qi", "Sx"];

  function fmt(n) {
    n = Math.floor(n);
    if (n < 1000) return String(n);
    let u = 0;
    let x = n;
    while (x >= 1000 && u < UNITS.length - 1) {
      x /= 1000;
      u++;
    }
    const s = x < 10 ? x.toFixed(2) : x < 100 ? x.toFixed(1) : String(Math.floor(x));
    return s + UNITS[u];
  }

  /**
   * Rates, not totals. fmt() floors, which renders the first Ghost Account's
   * 0.1/sec as a demoralising "0" — the one number a new player is watching to
   * see whether their purchase did anything.
   */
  function fmtRate(n) {
    if (n > 0 && n < 10) return String(Math.round(n * 10) / 10);
    return fmt(n);
  }

  /* ── Transport ───────────────────────────────────────────────────────── */

  let inFlight = false;

  async function post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
      body: JSON.stringify(body || {}),
    });

    if (res.status === 429) {
      // Rate limited. Not an error worth shouting about — the player is just fast.
      return null;
    }

    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      toast(T.error);
      return null;
    }

    if (!res.ok || !data.success) {
      if (data && data.reauth) {
        toast(T.signedOut);
      } else if (data && data.error === "insufficient") {
        toast(T.tooPoor);
      } else if (data && data.error) {
        toast(T.error);
      }
      return null;
    }

    S = data.state;
    return data;
  }

  /* ── The deck ────────────────────────────────────────────────────────── */

  const deckEl = document.getElementById("deck");
  let currentCard = null;
  let currentIsMeta = false;
  let sinceMeta = 0;
  let busy = false;

  function pick() {
    if (sinceMeta >= DECK.swipesBetweenMeta) {
      sinceMeta = 0;
      return { meta: DECK.meta[Math.floor(Math.random() * DECK.meta.length)] };
    }
    return { profile: DECK.profiles[Math.floor(Math.random() * DECK.profiles.length)] };
  }

  function stamp(kind, label) {
    const el = document.createElement("div");
    el.className =
      "stamp pointer-events-none absolute top-6 rounded-lg border-4 px-3 py-1 text-2xl font-black uppercase tracking-wider opacity-0 " +
      (kind === "like"
        ? "left-4 -rotate-12 border-empire-green text-empire-green"
        : "right-4 rotate-12 border-empire-red text-empire-red");
    el.textContent = label;
    el.dataset.stamp = kind;
    return el;
  }

  function buildCard() {
    const data = pick();
    currentIsMeta = Boolean(data.meta);

    const card = document.createElement("div");
    card.className =
      "swipecard absolute inset-0 flex cursor-grab flex-col overflow-hidden rounded-3xl " +
      "border border-empire-line shadow-2xl will-change-transform";

    if (data.meta) {
      card.innerHTML =
        '<div class="flex flex-1 flex-col items-center justify-center gap-2.5 bg-gradient-to-br from-empire-pink to-orange-400 p-5 text-center">' +
        '<div class="text-lg font-extrabold leading-snug text-white"></div>' +
        '<div class="text-sm font-medium text-white"></div>' +
        '<a class="rounded-full bg-white px-4 py-2 text-sm font-extrabold text-empire-pink no-underline" target="_blank" rel="noopener"></a>' +
        "</div>" +
        '<div class="bg-black/25 px-3.5 py-3">' +
        '<div class="text-lg font-extrabold">Crush.lu</div>' +
        '<div class="mt-0.5 min-h-[34px] text-xs text-empire-pink2"></div>' +
        "</div>";
      const [title, subtitle, cta] = card.querySelectorAll(".flex-1 > *");
      title.textContent = data.meta.title;
      subtitle.textContent = data.meta.subtitle;
      cta.textContent = data.meta.cta;
      cta.href = DECK.crushLuUrl;
      card.querySelector(".bg-black\\/25 > div:last-child").textContent =
        data.meta.subtitle;
    } else {
      const p = data.profile;
      card.innerHTML =
        '<div class="avatar flex flex-1 items-center justify-center bg-[radial-gradient(circle_at_50%_35%,#5a2a63,#33163b)] text-8xl"></div>' +
        '<div class="bg-black/25 px-3.5 py-3">' +
        '<div class="text-lg font-extrabold"><span class="nm"></span> <small class="text-sm font-normal text-empire-muted"></small></div>' +
        '<div class="bio mt-0.5 min-h-[34px] text-xs text-empire-pink2"></div>' +
        "</div>";
      card.querySelector(".avatar").textContent = p.emoji;
      card.querySelector(".nm").textContent = p.name;
      card.querySelector("small").textContent = p.age;
      card.querySelector(".bio").textContent = p.bio;
    }

    card.appendChild(stamp("like", "Like"));
    card.appendChild(stamp("nope", "Nope"));
    card.style.background = "linear-gradient(180deg,#48214f,#2c1233)";

    deckEl.appendChild(card);
    currentCard = card;
    attachDrag(card);
  }

  function floatText(x, y, text, color) {
    const el = document.createElement("div");
    el.className = "pointer-events-none fixed z-50 font-extrabold empire-rise";
    el.textContent = text;
    el.style.left = x + "px";
    el.style.top = y + "px";
    el.style.color = color;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 800);
  }

  async function resolveSwipe(dir) {
    if (!currentCard || busy) return;
    busy = true;

    const card = currentCard;
    currentCard = null;
    sinceMeta++;

    // Fly the card out immediately; the server call rides along behind it.
    card.style.transition = "transform .35s ease, opacity .35s";
    card.style.transform = `translateX(${dir * 500}px) rotate(${dir * 25}deg)`;
    card.style.opacity = "0";
    setTimeout(() => card.remove(), 340);

    const rect = card.getBoundingClientRect();
    const data = await post("/api/game/swipe/", {
      direction: dir > 0 ? "like" : "nope",
    });

    if (data) {
      floatText(
        rect.left + rect.width / 2 - 20,
        rect.top + 40,
        "+" + fmt(data.gained) + " 💘",
        dir > 0 ? "var(--color-empire-green)" : "var(--color-empire-pink2)"
      );
    }

    busy = false;
    buildCard();
    render();
  }

  function attachDrag(card) {
    let startX = 0;
    let startY = 0;
    let dx = 0;
    let down = false;
    const like = card.querySelector('[data-stamp="like"]');
    const nope = card.querySelector('[data-stamp="nope"]');

    const begin = (x, y) => {
      down = true;
      startX = x;
      startY = y;
      card.style.transition = "none";
    };
    const move = (x, y) => {
      if (!down) return;
      dx = x - startX;
      const dy = y - startY;
      card.style.transform = `translate(${dx}px, ${dy * 0.25}px) rotate(${dx * 0.06}deg)`;
      const t = Math.min(Math.abs(dx) / 120, 1);
      like.style.opacity = dx > 0 ? t : 0;
      nope.style.opacity = dx < 0 ? t : 0;
    };
    const end = () => {
      if (!down) return;
      down = false;
      if (Math.abs(dx) > 110) {
        resolveSwipe(dx > 0 ? 1 : -1);
      } else {
        card.style.transition = "transform .2s";
        card.style.transform = "";
        like.style.opacity = 0;
        nope.style.opacity = 0;
      }
      dx = 0;
    };

    // Pointer events cover mouse, touch and pen in one path.
    card.addEventListener("pointerdown", (e) => {
      card.setPointerCapture(e.pointerId);
      begin(e.clientX, e.clientY);
    });
    card.addEventListener("pointermove", (e) => move(e.clientX, e.clientY));
    card.addEventListener("pointerup", end);
    card.addEventListener("pointercancel", end);
  }

  /* ── Shop ────────────────────────────────────────────────────────────── */

  const listGen = document.getElementById("list-gen");
  const listUp = document.getElementById("list-up");

  function itemRow(opts) {
    const { emoji, name, desc, cost, right, disabled, affordable, dataset } = opts;
    const row = document.createElement("button");
    row.type = "button";
    row.disabled = Boolean(disabled);
    Object.assign(row.dataset, dataset);
    row.className =
      "mb-2 flex w-full items-center gap-3 rounded-xl border border-empire-line bg-white/5 px-3 py-2.5 text-left transition " +
      (disabled
        ? "cursor-not-allowed opacity-40"
        : "hover:border-empire-pink hover:bg-empire-pink/10");
    row.innerHTML =
      '<div class="w-10 text-center text-2xl"></div>' +
      '<div class="flex-1"><div class="font-bold"></div><div class="text-xs text-empire-muted"></div></div>' +
      '<div class="min-w-[92px] text-right"><div class="text-sm font-bold"></div><div class="text-xs text-empire-muted"></div></div>';
    const [emojiEl, info, priceBox] = row.children;
    emojiEl.textContent = emoji;
    info.children[0].textContent = name;
    info.children[1].textContent = desc;
    priceBox.children[0].textContent = fmt(cost) + " 💘";
    priceBox.children[0].className =
      "text-sm font-bold " + (affordable ? "text-empire-green" : "text-empire-gold");
    priceBox.children[1].textContent = right || "";
    return row;
  }

  function renderShop() {
    listGen.replaceChildren();
    S.generators.forEach((g) => {
      if (!g.unlocked) return;
      listGen.appendChild(
        itemRow({
          emoji: g.emoji,
          name: g.name,
          desc: `${g.desc} · +${fmtRate(g.cps)}/s`,
          cost: g.cost,
          right: `${T.owned} ${g.owned}`,
          affordable: S.points >= g.cost,
          dataset: { buy: "generator", id: g.id },
        })
      );
    });

    listUp.replaceChildren();
    const pending = S.upgrades.filter((u) => !u.owned);
    if (!pending.length) {
      const p = document.createElement("p");
      p.className = "p-5 text-center text-sm text-empire-muted";
      p.textContent = T.allBought;
      listUp.appendChild(p);
      return;
    }
    pending.forEach((u) => {
      listUp.appendChild(
        itemRow({
          emoji: u.emoji,
          name: u.name,
          desc: u.unlocked ? u.desc : "🔒 " + T.locked,
          cost: u.cost,
          affordable: u.unlocked && S.points >= u.cost,
          disabled: !u.unlocked,
          dataset: { buy: "upgrade", id: u.id },
        })
      );
    });
  }

  /* ── Render ──────────────────────────────────────────────────────────── */

  const $ = (id) => document.getElementById(id);

  function render() {
    $("points").textContent = fmt(S.points);
    $("cps").textContent = fmtRate(S.cps);
    $("swipes").textContent = S.swipes;
    $("perlike").textContent = fmt(S.per_like);
    $("pernope").textContent = fmt(S.per_nope);
    $("hearts").textContent = S.hearts;
    $("lovebonus").textContent = "+" + S.heart_bonus_pct + "%";
    $("total-earned").textContent = fmt(S.total_earned);
    $("likes").textContent = S.likes;
    $("nopes").textContent = S.nopes;
    $("pending-hearts").textContent = S.pending_hearts;
    $("btn-prestige").disabled = S.pending_hearts < 1;
    renderShop();
  }

  document.querySelectorAll("[data-t]").forEach((el) => {
    el.textContent = T[el.dataset.t];
  });

  /* ── Toast ───────────────────────────────────────────────────────────── */

  const toastEl = document.getElementById("toast");
  let toastTimer = null;

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.style.opacity = "1";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toastEl.style.opacity = "0"), 2600);
  }

  /* ── Events ──────────────────────────────────────────────────────────── */

  $("btn-like").addEventListener("click", () => resolveSwipe(1));
  $("btn-nope").addEventListener("click", () => resolveSwipe(-1));

  window.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") resolveSwipe(1);
    if (e.key === "ArrowLeft") resolveSwipe(-1);
  });

  // One delegated listener for the whole shop: rows are rebuilt on every render,
  // so per-row listeners would leak.
  document.getElementById("empire").addEventListener("click", async (e) => {
    const row = e.target.closest("[data-buy]");
    if (!row || row.disabled || inFlight) return;
    inFlight = true;
    const before = S.upgrades.filter((u) => u.owned).length;
    const data = await post("/api/game/buy/", {
      kind: row.dataset.buy,
      id: Number(row.dataset.id),
    });
    inFlight = false;
    if (data) {
      if (row.dataset.buy === "upgrade") {
        const after = S.upgrades.filter((u) => u.owned).length;
        if (after > before) toast(`${row.children[0].textContent} ${T.unlocked}`);
      }
      render();
    }
  });

  $("btn-prestige").addEventListener("click", async () => {
    const pending = S.pending_hearts;
    if (pending < 1) return;
    if (!window.confirm(T.prestigeConfirm.replace("%s", pending))) return;
    const data = await post("/api/game/prestige/");
    if (data) {
      toast(T.prestigeDone.replace("%s", data.hearts_gained));
      render();
    }
  });

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => {
        const on = t === tab;
        t.classList.toggle("bg-empire-pink", on);
        t.classList.toggle("text-empire-bg", on);
        t.classList.toggle("font-bold", on);
        t.classList.toggle("bg-white/5", !on);
      });
      listGen.hidden = tab.dataset.tab !== "gen";
      listUp.hidden = tab.dataset.tab !== "up";
    });
  });
  document.querySelector('.tab[data-tab="gen"]').click();

  /* ── Loops ───────────────────────────────────────────────────────────── */

  // Local simulation, purely cosmetic: the counter should not sit still between
  // heartbeats. Overwritten by the next authoritative response.
  let last = performance.now();
  setInterval(() => {
    const now = performance.now();
    const dt = (now - last) / 1000;
    last = now;
    const earned = S.cps * dt;
    S.points += earned;
    S.total_earned += earned;
    $("points").textContent = fmt(S.points);
  }, 250);

  // Heartbeat. The server banks idle production from its own clock; this just
  // asks it to, and re-syncs the mirror.
  setInterval(async () => {
    if (busy || inFlight) return;
    const data = await post("/api/game/sync/");
    if (data) render();
  }, 15000);

  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState !== "visible") return;
    const data = await post("/api/game/sync/");
    if (data) {
      if (data.offline_earned > 0) {
        toast(T.offline.replace("%s", fmt(data.offline_earned)));
      }
      render();
    }
  });

  /* ── Boot ────────────────────────────────────────────────────────────── */

  render();
  buildCard();
})();
