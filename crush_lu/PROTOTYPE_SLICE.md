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

**Phase C added (recap + People I've Met):** the 48-hour recap grid, unlimited
irreversible meeting confirmations (§7.7, §9.3), reciprocal-confirmation →
`ConfirmedEncounter` (§9.4), the permanent "People I've Met" collection +
full-profile view (§7.8), live-mutual highlighting, and the "You've already
met" non-actionable tiles are now implemented with models, endpoints, UI, and
tests.

**Still deliberately stubbed** (marked `# PROTOTYPE-STUB:` in code):
coach-reviewed encounter removal (`ConfirmedEncounterRemovalRequest` §9.5),
versioned lobby photo-consent (reuses `photo_share_consent`), persisted
recap/24h-reminder in-app notifications (§12), the 30-day retention cleanup
task (§13), and analytics (§15). Recap realtime rides the polling fallback
(the member WebSocket closes at event end per §7.6).
