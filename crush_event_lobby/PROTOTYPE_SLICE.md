# Prototype slice: live meet-signal loop

This prototype validates the live Event Lobby from eligible attendance through a mutual signal reveal.
It is the highest-value slice because its photo-only anonymity, three-signal quota, and pairwise reveal are the riskiest product promises.
The slice includes participation, opaque handles, protected photos, blocking, services, HTTP endpoints, and a minimal lobby UI.
It deliberately excludes the 48-hour recap, People I've Met, removal review, and persisted notifications.
QR check-in and mid-event onboarding now enroll eligible members; a separate member Channel emits identity-free refetch hints with polling fallback.
Every deliberate edge stub is marked `PROTOTYPE-STUB` in code and summarized in the final report.
