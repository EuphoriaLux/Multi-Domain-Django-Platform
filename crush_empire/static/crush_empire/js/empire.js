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
  const T = JSON.parse(document.getElementById("empire-i18n").textContent);
  const DECK = JSON.parse(document.getElementById("empire-deck").textContent);

  /**
   * The meta card's CTA target.
   *
   * A literal, not read from the page. Assigning DOM-derived text to `.href` is
   * how a `javascript:` URL gets executed, and nothing here needs the target to
   * be dynamic. Everything else from DECK reaches the DOM through .textContent,
   * which cannot execute.
   */
  const CRUSH_LU_URL = "https://crush.lu/?src=game";

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

    // /deck/draw/ returns a card, not a state. Overwriting unconditionally would
    // blank the mirror and render NaN across the whole page.
    if (data.state) S = data.state;
    return data;
  }

  /* ── The deck ────────────────────────────────────────────────────────── */

  const deckEl = document.getElementById("deck");
  let currentCard = null;
  let currentChallenge = null; // null on meta cards — they have no right answer
  let sinceMeta = 0;
  let busy = false;

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

  /**
   * Deal the next card.
   *
   * Meta cards are local furniture — an advert, no right answer, no API call.
   * Real cards come from the server, which alone knows whether the profile is a
   * scam. The payload deliberately carries no `is_scam` and no red-flag marks;
   * they arrive only in the resolve() response, after the player has committed.
   */
  async function buildCard(attempt = 0) {
    let data;

    if (sinceMeta >= DECK.swipesBetweenMeta) {
      sinceMeta = 0;
      currentChallenge = null;
      data = { meta: DECK.meta[Math.floor(Math.random() * DECK.meta.length)] };
    } else {
      const response = await post("/api/game/deck/draw/");
      if (!response) {
        // A 429 or a blip. Without a retry the deck stays empty and the game is
        // dead until the player reloads — and draw() is idempotent, so asking
        // again is free: it returns the same open card.
        const delay = Math.min(4000, 400 * 2 ** attempt);
        setTimeout(() => buildCard(attempt + 1), delay);
        return;
      }
      currentChallenge = response.card.challenge_id;

      // A tier-2 card never reaches the deck: it opens the puzzle directly.
      // Nothing about the card says which it is until the server says so, and
      // the server deals tier-2 from the genuine pool just as often — otherwise
      // "the modal opened" would be the answer.
      if (response.card.tier === 2) {
        openSpot(response.card);
        return;
      }
      data = { profile: response.card };
    }

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
      cta.href = CRUSH_LU_URL;
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
      // Segments render as one sentence here. Tier 2 will make each tappable —
      // which is exactly why the server sends them apart rather than joined.
      card.querySelector(".bio").textContent = p.segments
        .map((s) => s.text)
        .join(" ");
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

  const FLY = { like: 500, nope: -500, report: 0 };

  /** action is "like" | "nope" | "report". */
  async function act(action) {
    if (!currentCard || busy) return;
    busy = true;

    const card = currentCard;
    const challenge = currentChallenge;
    currentCard = null;
    sinceMeta++;

    // Fly the card out immediately; the server call rides along behind it.
    card.style.transition = "transform .35s ease, opacity .35s";
    card.style.transform =
      action === "report"
        ? "translateY(-460px) scale(.9)"
        : `translateX(${FLY[action]}px) rotate(${action === "like" ? 25 : -25}deg)`;
    card.style.opacity = "0";
    setTimeout(() => card.remove(), 340);

    // Meta cards are adverts. Swiping one is not an answer to anything.
    if (!challenge) {
      busy = false;
      await buildCard();
      render();
      return;
    }

    const rect = card.getBoundingClientRect();
    const data = await post("/api/game/deck/resolve/", {
      challenge_id: challenge,
      action,
    });

    if (data) {
      const r = data.result;
      if (r.points > 0) {
        floatText(rect.left + rect.width / 2 - 20, rect.top + 40,
          "+" + fmt(r.points) + " 💘",
          action === "like" ? "var(--color-empire-green)" : "var(--color-empire-pink2)");
      } else if (r.points < 0) {
        floatText(rect.left + rect.width / 2 - 30, rect.top + 40,
          fmt(r.points) + " 💘", "var(--color-empire-red)");
      }
      if (r.flags > 0) {
        floatText(rect.left + rect.width / 2 - 20, rect.top + 90,
          "+" + r.flags + " 🚩", "var(--color-empire-gold)");
      }
      announce(r);
    }

    busy = false;
    await buildCard();
    render();
  }

  /* ── Tier 2: spot the red flag ───────────────────────────────────────── */

  const spotEl = document.getElementById("spot");
  const spotSegments = document.getElementById("spot-segments");
  const spotTimer = document.getElementById("spot-timer");
  const spotBar = document.getElementById("spot-bar");
  const spotClear = document.getElementById("spot-clear");
  const spotReport = document.getElementById("spot-report");

  let tapped = new Set();
  let spotDeadline = 0;
  let spotTicker = null;

  function segmentChip(segment) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.dataset.segment = segment.id;
    chip.className =
      "block w-full rounded-xl border border-empire-line bg-white/5 px-3 py-2 text-left text-sm transition";
    chip.textContent = segment.text;
    return chip;
  }

  function paintChip(chip) {
    const on = tapped.has(Number(chip.dataset.segment));
    chip.classList.toggle("border-empire-gold", on);
    chip.classList.toggle("bg-empire-gold/15", on);
    chip.classList.toggle("text-empire-gold", on);
  }

  function updateSpotButtons() {
    spotClear.textContent = T.itsFine;
    spotClear.disabled = tapped.size > 0;
    spotReport.textContent = T.reportTapped.replace("%s", tapped.size);
    spotReport.disabled = tapped.size === 0;
  }

  function openSpot(card) {
    tapped = new Set();
    document.getElementById("spot-avatar").textContent = card.emoji;
    document.getElementById("spot-name").textContent = `${card.name}, ${card.age}`;

    spotSegments.replaceChildren();
    card.segments.forEach((s) => spotSegments.appendChild(segmentChip(s)));

    // The server's deadline, not ours. If the clocks disagree the bar is wrong;
    // the verdict still isn't.
    spotDeadline = new Date(card.deadline).getTime();
    const total = card.seconds * 1000;

    clearInterval(spotTicker);
    spotTicker = setInterval(() => {
      const left = Math.max(0, spotDeadline - Date.now());
      const secs = Math.ceil(left / 1000);
      spotTimer.textContent = "0:" + String(secs).padStart(2, "0");
      spotBar.style.width = (left / total) * 100 + "%";
      spotBar.classList.toggle("bg-empire-red", left < 5000);
      if (left <= 0) submitSpot("clear"); // let the server call it a timeout
    }, 200);

    updateSpotButtons();
    spotEl.hidden = false;
  }

  function closeSpot() {
    clearInterval(spotTicker);
    spotTicker = null;
    spotEl.hidden = true;
  }

  spotSegments.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-segment]");
    if (!chip) return;
    const id = Number(chip.dataset.segment);
    tapped.has(id) ? tapped.delete(id) : tapped.add(id);
    paintChip(chip);
    updateSpotButtons();
  });

  async function submitSpot(action) {
    if (!currentChallenge || busy) return;
    busy = true;
    clearInterval(spotTicker);
    spotTicker = null;

    const challenge = currentChallenge;
    currentChallenge = null;
    sinceMeta++;

    const data = await post("/api/game/deck/resolve/", {
      challenge_id: challenge,
      action,
      tapped: [...tapped],
    });

    closeSpot();
    if (data) announce(data.result);

    busy = false;
    await buildCard();
    render();
  }

  spotClear.addEventListener("click", () => submitSpot("clear"));
  spotReport.addEventListener("click", () => submitSpot("report"));

  /* ── The teaching moment ─────────────────────────────────────────────── */

  const revealEl = document.getElementById("reveal");
  const revealTitle = document.getElementById("reveal-title");
  const revealBody = document.getElementById("reveal-body");

  /**
   * Only interrupt when there is something to learn.
   *
   * A correct nope on a genuine profile teaches nothing and must not cost a tap.
   * A scam — caught, dodged or fallen for — always explains itself, because the
   * explanation is the entire reason this mechanic exists.
   */
  function announce(r) {
    // Correctly clearing a genuine card is the common case and must not cost a
    // tap to dismiss. A toast; nothing to learn.
    if (r.outcome === "neutral") {
      if (r.tier === 2) toast(T.clearedRight);
      return;
    }
    if (r.outcome === "false_report") {
      toast(r.tier === 2 ? T.falseTap : T.falseReport);
      return;
    }
    if (r.outcome === "timeout") {
      toast(T.timeoutGenuine);
      return;
    }

    const titles = {
      correct: "🚩 " + T.niceCatch,
      partial: "🫤 " + T.partial,
      missed: "😬 " + T.missed,
      catfished: "💔 " + T.catfished,
    };
    revealTitle.textContent = titles[r.outcome] || "";
    revealTitle.className =
      "text-lg font-extrabold " +
      (r.outcome === "correct" ? "text-empire-green"
        : r.outcome === "partial" ? "text-empire-gold"
        : "text-empire-red");

    revealBody.replaceChildren();

    // On a tier-2 card, show the actual line — the player just stared at it, and
    // "which one was it?" is the entire lesson.
    r.reveal.flags.forEach((f) => {
      const wrap = document.createElement("div");
      const caught = (r.reveal.tapped || []).includes(f.segment_id);

      if (r.tier === 2 && f.text) {
        const line = document.createElement("p");
        line.className =
          "font-semibold " + (caught ? "text-empire-green" : "text-empire-red");
        line.textContent = (caught ? "✓ " : "✗ ") + f.text;
        wrap.appendChild(line);
      }
      const why = document.createElement("p");
      why.textContent = "🚩 " + f.explanation;
      wrap.appendChild(why);
      revealBody.appendChild(wrap);
    });

    if (r.debuffed) {
      const p = document.createElement("p");
      p.className = "font-bold text-empire-red";
      p.textContent = T.compromised;
      revealBody.appendChild(p);
    }
    revealEl.hidden = false;
  }

  document.getElementById("reveal-close").addEventListener("click", () => {
    revealEl.hidden = true;
  });

  function attachDrag(card) {
    let startX = 0;
    let startY = 0;
    let dx = 0;
    let dy = 0;
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
      dy = y - startY;
      // Up-swipe is a report. Once the drag is clearly vertical, stop rotating —
      // the card should read as being lifted out, not thrown aside.
      const vertical = dy < -60 && Math.abs(dy) > Math.abs(dx);
      card.style.transform = vertical
        ? `translate(${dx * 0.3}px, ${dy}px) scale(${1 + dy / 2000})`
        : `translate(${dx}px, ${dy * 0.25}px) rotate(${dx * 0.06}deg)`;

      const t = vertical ? 0 : Math.min(Math.abs(dx) / 120, 1);
      like.style.opacity = dx > 0 ? t : 0;
      nope.style.opacity = dx < 0 ? t : 0;
      card.style.borderColor = vertical ? "var(--color-empire-gold)" : "";
    };
    const end = () => {
      if (!down) return;
      down = false;
      if (dy < -110 && Math.abs(dy) > Math.abs(dx)) {
        act("report");
      } else if (Math.abs(dx) > 110) {
        act(dx > 0 ? "like" : "nope");
      } else {
        card.style.transition = "transform .2s";
        card.style.transform = "";
        card.style.borderColor = "";
        like.style.opacity = 0;
        nope.style.opacity = 0;
      }
      dx = 0;
      dy = 0;
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

  function renderDebuff() {
    const banner = $("debuff");
    if (!S.debuff_until) {
      banner.hidden = true;
      return;
    }
    const remaining = Math.max(0, new Date(S.debuff_until) - Date.now());
    if (remaining <= 0) {
      banner.hidden = true;
      // The server is authoritative on expiry; ask it rather than guessing.
      if (S.debuffed) post("/api/game/sync/").then((d) => d && render());
      return;
    }
    banner.hidden = false;
    const secs = Math.ceil(remaining / 1000);
    $("debuff-remaining").textContent =
      Math.floor(secs / 60) + ":" + String(secs % 60).padStart(2, "0");

    const btn = $("btn-clear-debuff");
    btn.textContent = T.clearDebuff.replace("%s", S.debuff_clear_cost);
    btn.disabled = S.flags < S.debuff_clear_cost;
  }

  function render() {
    $("points").textContent = fmt(S.points);
    $("cps").textContent = fmtRate(S.cps);
    $("swipes").textContent = S.swipes;
    $("flags").textContent = S.flags;
    $("streak").textContent = S.streak;
    $("perlike").textContent = fmt(S.per_like);
    $("pernope").textContent = fmt(S.per_nope);
    $("hearts").textContent = S.hearts;
    $("lovebonus").textContent = "+" + S.heart_bonus_pct + "%";
    $("total-earned").textContent = fmt(S.total_earned);
    $("likes").textContent = S.likes;
    $("nopes").textContent = S.nopes;
    $("pending-hearts").textContent = S.pending_hearts;
    $("btn-prestige").disabled = S.pending_hearts < 1;
    renderDebuff();
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

  $("btn-like").addEventListener("click", () => act("like"));
  $("btn-nope").addEventListener("click", () => act("nope"));
  $("btn-report").addEventListener("click", () => act("report"));

  window.addEventListener("keydown", (e) => {
    if (!revealEl.hidden) {
      if (e.key === "Enter" || e.key === "Escape") revealEl.hidden = true;
      return;
    }
    if (e.key === "ArrowRight") act("like");
    if (e.key === "ArrowLeft") act("nope");
    if (e.key === "ArrowUp") act("report");
  });

  $("btn-clear-debuff").addEventListener("click", async () => {
    const data = await post("/api/game/clear-debuff/");
    if (data) {
      toast(T.debuffCleared);
      render();
    }
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
    if (S.debuff_until) renderDebuff(); // tick the ACCOUNT COMPROMISED clock
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
