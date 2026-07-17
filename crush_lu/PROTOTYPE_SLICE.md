# Prototype slice — Crush Connect Event Lobby (agent: claude-q4b3)

Spec: docs/superpowers/specs/2026-07-17-crush-connect-event-lobby-design.md

**Slice: the live Event Lobby, end-to-end** — eligibility gate (§5.1) evaluated
on QR check-in and on mid-event onboarding completion, the photo-only roster
with opaque event-scoped handles and roster-authorized photo serving (§7.2,
§13), the three-signal → mutual first-name reveal loop with quota locking
(§7.3–§7.4, §9.2), exact event-end closure (§7.6), a member WebSocket consumer
with polling fallback (§11.1), lobby page + hub card UI, and tests (§18).

**Why highest value:** this slice concentrates the feature's riskiest
invariants — anonymity before mutual reveal, the server-enforced irrevocable
3-signal quota, phase boundaries from server time, and check-in that must
never depend on the lobby. The recap/People-I've-Met surfaces are mostly
conventional CRUD once these primitives are proven.

**Deliberately stubbed** (marked `# PROTOTYPE-STUB:` in code): the 48-hour
recap grid, meeting confirmations, ConfirmedEncounter/"People I've Met",
coach-reviewed removal, versioned lobby photo-consent (reuses
`photo_share_consent`), "You've already met" tiles, persisted recap/reminder
notifications, 30-day retention cleanup, and analytics.
