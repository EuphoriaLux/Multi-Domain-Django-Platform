# Crush Connect Event Lobby — integrated implementation notes

> Originally the claude-q4b3 bake-off prototype; now the integrated feature
> branch (PR #637) combining the best of the three agent entries: this base,
> the codex removal-review workflow, and a token-compliant rebuild of the
> gemini visual design.

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

**Removal review added (§9.5, ported from PR #633):** members privately
request removal of a confirmed encounter (immediate two-sided hiding); a
staff-only queue resolves it to approved / kept hidden / restored. The queue
stays staff-only until requests can be scoped to an assigned coach.

**Retention cleanup added (§13):** `manage.py cleanup_event_lobby` hard-deletes
signal / confirmation / participation rows 30 days after an event's recap
closes. Permanent encounters are never touched — they keep only their
`created_from_event` provenance. Idempotent, `--dry-run` supported.

**Confirmed-encounter notification added (§12):** the first confirmer gets a
persisted in-app bell row ("{name} was added to People I've Met."), written
directly to `Notification` so MVP still emits no push/email/APNS/SMS (§19).

**Still deliberately stubbed** (marked `# PROTOTYPE-STUB:` in code):
versioned lobby photo-consent (reuses `photo_share_consent`), the remaining
§12 notifications (recap-opens + 24h reminder), WebSocket force-disconnect
on mid-session eligibility loss
(§11.1 — HTTP re-authorizes every fetch meanwhile), the coach-scoped review
queue (§9.5), and analytics (§15). Recap realtime rides the polling fallback
(the member WebSocket closes at event end per §7.6).
