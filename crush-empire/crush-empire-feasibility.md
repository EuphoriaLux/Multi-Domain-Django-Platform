# Crush Empire — Side-Hustle Feasibility Note

**Question:** Can I easily build a Crush.lu-themed incremental game as a side hustle?
**Short answer:** Yes. This is one of the most achievable game genres, and you already have the skills. A working prototype is in `crush-empire.html`.

## Why it's a good fit

Idle/incremental games are almost pure logic and UI — no physics, no real-time multiplayer, no art pipeline required. The whole prototype is a single HTML file with vanilla JavaScript: currency, exponential upgrade costs, auto-generators, prestige, and local save. For a Django/Azure developer this is a weekend, not a project.

The dating theme also gives you something most idle games lack: a real funnel. The game is free entertainment that carries the Crush.lu brand and nudges players toward the actual platform at natural milestones.

## What the prototype already does

- Click "Send a Wink" to earn Crushes 💘
- 8 tiers of auto-generators (Dating Profile → Coffee Date → Matchmaker → ... → Love Rocket)
- Click and global multiplier upgrades that unlock as you progress
- Prestige ("Fall in Love") — reset for Hearts 💖 that permanently boost production
- Local save/autosave, stats, milestone popups linking to crush.lu
- Balanced economy (verified: ~90 min to 1M crushes, first prestige after a few hours)

## Monetization / funnel angle (your chosen intent)

The game is the top of the funnel, not the revenue itself:

- Brand exposure — every session shows Crush.lu; the persistent CTA and milestone nudges drive click-throughs.
- Soft conversion — "You've matched 1M crushes in the game... ready for a real one?" is a warmer pitch than a cold ad.
- Zero marginal cost — it's a static file; host it free on Azure Static Web Apps or as a page under crush.lu.
- Measurable — add a UTM link (`crush.lu/?src=game`) and you can track sign-ups the game actually generates.

## Effort to ship (realistic)

| Stage | Effort | Notes |
|---|---|---|
| Playable prototype | **Done** | `crush-empire.html` |
| Polish (art, sound, mobile tuning) | 1–2 weekends | Emoji works; custom art optional |
| Hosting under crush.lu | ~1 hour | Static file; Azure Static Web Apps or a Django template/route |
| Analytics + UTM funnel tracking | ~1 hour | Tie into whatever you already use |
| Ongoing content (new tiers, events) | occasional | Idle players love updates |

## Recommended next steps

1. Open `crush-empire.html` and play 10 minutes — confirm the *feel* is right for your brand.
2. Decide theme direction: keep it cheeky (winks, coffee dates, wingmen) or make it more on-brand to Crush.lu's actual features.
3. Host it at `crush.lu/game` with a UTM'd sign-up CTA and watch whether it converts.
4. If early numbers are promising, invest a weekend in polish (custom icons, a light sound, seasonal events) before promoting it.

## Honest risks

- **Discovery is the hard part**, not building. Idle games are cheap to make and plentiful; yours wins by riding Crush.lu's existing audience, not by competing in app stores.
- **Funnel ≠ guaranteed conversions.** Treat it as brand/engagement first; measure before assuming sign-up lift.
- Keep it tasteful — a dating brand's game should feel warm and fun, not grindy or spammy with the CTA.
