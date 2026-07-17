# Prototype Slice — Event Lobby & People I've Met

I have chosen to prototype the core Event Lobby loop and transition to confirmed encounters:
1. **Lobby Eligibility and Roster**: Checked-in members with active Connect membership and updated consent enter the photo-only secure lobby.
2. **Signals & Mutual Reveal**: Outgoing signal limits (max 3), aggregate anonymous counts, and mutual first-name reveals.
3. **48-Hour Recap Transition**: Post-event confirmations leading to permanent confirmed encounters.
4. **People I've Met & Moderation**: Chronological list of met people, and Coach-moderated removal requests.

### Highest-Value Rationale
This slice tests the critical consent, privacy, and state transitions that are the core of the product promise (anonymity before consent, quota protection under concurrency, and post-event mutual reveals without opening chats).

### Deliberate Stubs
- WebSocket broadcasts are simplified to polling fallback.
- Member profile view (linked from People I've Met) renders a placeholder detail card simulating the full Connect profile.
- Coach review UI for removals is modeled as a service function call rather than a full admin dashboard.
