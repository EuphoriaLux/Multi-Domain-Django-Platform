# Schueberfouer thermal ticket acquisition campaign

- Status: draft brief, not approved for production
- Campaign state: NO-GO for 2026
- Prepared: 2026-08-29
- Decision source: Kanban `t_98963758`, completed 2026-08-25
- Repository evidence snapshot: `830f67db`

## 1. Operating decision

The 2026 Schueberfouer street activation is cancelled. The product-owner decision
also withdrew the ticket-only fallback. This document records a campaign draft so
that a future review starts from verified product and consent constraints instead
of the original concept.

For 2026, the instruction is exact:

- do not publish a campaign page or QR code;
- do not print or hand out acquisition tickets;
- do not send campaign messages;
- do not buy campaign hardware or paid media;
- do not collect prospect data for this initiative;
- do not alter the live event check-in ticket.

The copy and measurement contract below are candidate material for a separately
approved future pilot. They are not launch authorization.

## 2. Evidence and constraints

### Product position to preserve

Current Crush.lu copy positions the service as a Luxembourg dating platform for
real-life meetings, privacy, verified members, hosted events, and an alternative
to endless swiping. The campaign should feel like a playful way into that existing
promise. It should not make Crush.lu sound like an instant-match game or a voucher
promotion.

Sources:

- `crush_lu/templates/crush_lu/home.html`
- `crush_lu/templates/crush_lu/about.html`
- `crush_lu/templates/crush_lu/how_it_works.html`

### What exists

- The application supports English, German, and French through language-prefixed
  routes and gettext/modeltranslation.
- The Campaign Dashboard can contact existing members by consented email,
  WhatsApp, and web push. It adds `utm_source=crush.lu`, channel-specific
  `utm_medium`, and the campaign slug as `utm_campaign`.
- Campaign click records contain the link, an optional attributed user, and a
  timestamp. They do not store an IP address or user agent.
- Google Consent Mode v2 defaults analytics and marketing storage to denied until
  the visitor grants the relevant consent.
- The event check-in renderer produces an 80 mm, 48-column ESC/POS ticket through
  RawBT. It is live for registered event attendees.

Sources:

- `docs/specs/campaign-dashboard.md`
- `crush_lu/models/campaigns.py`
- `crush_lu/services/campaigns.py`
- `crush_lu/views_campaign_click.py`
- `azureproject/templatetags/analytics.py`
- `crush_lu/services/ticket_printer.py`

### What does not exist

The inspected repository has no public `/fouer/` route, anonymous thermal-ticket
activation record, public phone signup, session-creating WhatsApp magic link,
Glacis pairing radar, campaign chat, or partner reward flow.

The existing paths cannot substitute for those capabilities:

- `event_register` requires authentication and an existing Crush profile.
- The personalised thermal ticket is built from an `EventRegistration`.
- `event_lobby` requires authentication and an attended registration.
- WhatsApp OTP views require a logged-in user. They verify that user's phone; they
  do not create an account or session.
- The Campaign Dashboard targets existing users. It is not an anonymous street
  acquisition system.

Sources:

- `crush_lu/views_events.py`
- `crush_lu/views_event_lobby.py`
- `crush_lu/views_phone_verification.py`
- `azureproject/urls_crush.py`

These are funnel gaps. A different printer, a new headline, or a redirect to the
signup page would still leave a visitor at an unsupported journey.

## 3. Campaign objective

### 2026 objective

Keep the cancelled campaign closed. The intended measurable result is zero
campaign activity, spend, visitor data collection, and change to the event ticket.

### Conditional future objective

If a newer product-owner decision reopens the initiative, run a small,
staffed pilot that turns a voluntary conversation with an adult Schueberfouer
visitor into activation of a physical introduction ticket. The visitor must be able
to understand the experience before signup, retain their chosen language, and
stop participating without contacting an engineer.

The pilot should test whether a physical prompt creates qualified activations. It
must not promise a match, compatibility, identity guarantee, chat, or reward that
the product cannot provide.

## 4. Audience

### Primary audience

Adults aged 18 or over who:

- are at the Schueberfouer and voluntarily engage with the Crush.lu team;
- are single and open to a local, real-life dating introduction;
- can read the participation and data-use notice in French, German, or English;
- explicitly choose to participate.

### Secondary audience

Existing Crush.lu members may use a future campaign flow, but they must not count
as newly acquired members. Their normal event check-in ticket remains separate.

### Exclusions

- Anyone under 18.
- Anyone unable to give informed consent.
- People approached after they decline or walk away.
- Automatic enrolment from a scan, conversation, or printed ticket.
- Ambassador-entered consent on behalf of a visitor.
- Sensitive information on paper, including phone, email, date of birth, age,
  orientation, preferences, verification state, or another participant's data.

## 5. Value proposition and offer

### Candidate value proposition

French source:

> Une introduction locale, ludique et volontaire, activée depuis un ticket
> physique.

German:

> Eine spielerische, freiwillige Begegnung vor Ort, aktiviert über ein physisches
> Ticket.

English:

> A playful, voluntary local introduction, activated from a physical ticket.

### Offer framing

The offer is participation in a bounded introduction experience. It is not a
purchase, prize, discount, ride token, drink voucher, or guaranteed match. The
paper ticket is an activation key and explanation surface, not proof that a match
or reward exists.

No price or incentive should appear unless a future owner decision approves the
commercial terms and the product can fulfil them. Paid media remains out of scope
under the current EUR 0 marketing budget.

### Claims that are not allowed

Do not use:

- "0% fake", "no catfish", or "100% verified";
- "100% compatible" or any compatibility percentage;
- "match garanti", "rencontre garantie", or equivalent wording;
- "activation en 30 secondes" until timed usability evidence supports it;
- "connexion sans mot de passe" until the public authentication path exists and
  passes security review;
- "chat instantané", "radar live", or reward language until each feature is live
  and tested;
- `verification_method="coach_event"` as an identity guarantee.

## 6. CTA and destination

### 2026

There is no external CTA, destination, QR code, redirect token, short link, or UTM
campaign for 2026.

### Conditional future CTA

The paper CTA is:

`SCANNE POUR ACTIVER TON TICKET`

The landing-page primary CTA is:

`Activer mon ticket`

The information CTA is:

`En savoir plus avant de participer`

The visitor must see purpose, data use, retention, deletion, safety limits, and the
no-match statement before the primary CTA creates participation state.

### Conditional destination contract

Required public routes:

- `https://crush.lu/fr/fouer/`
- `https://crush.lu/de/fouer/`
- `https://crush.lu/en/fouer/`

They must work in a new mobile browser without authentication. Do not substitute
the event lobby, My Crush, a generic signup page, or a Campaign Dashboard redirect.

Required UTM values for the physical ticket:

- `utm_source=crush.lu`
- `utm_medium=thermal_ticket`
- `utm_campaign=schueberfouer_thermal_{year}`
- `utm_content=ticket_qr`

Example for a future review, not a live URL:

`https://crush.lu/fr/fouer/?utm_source=crush.lu&utm_medium=thermal_ticket&utm_campaign=schueberfouer_thermal_2027&utm_content=ticket_qr`

Language and attribution must survive information, authentication, consent, and
activation. The ticket token must be opaque, expiring, single-use, revocable, and
absent from analytics payloads.

## 7. French-first copy deck

French is the source copy. German and English require human language review before
printing or publishing. Luxembourgish is not an application locale in the
inspected implementation, so no Luxembourgish product variant is specified.

All copy in this section is draft-only while the campaign remains NO-GO.

### Ambassador opening in French

> Bonjour, c'est une expérience Crush.lu réservée aux adultes. Le ticket propose
> une introduction ludique avec une autre personne participante. Rien n'est
> garanti et tu peux t'arrêter à tout moment. Tu veux d'abord voir comment ça
> marche et comment tes données seront utilisées ?

If the visitor declines:

> Pas de souci. Bonne Fouer !

The ambassador must not ask for contact or preference data before the visitor has
opened the information step and chosen to continue.

### Thermal ticket

#### French

- Brand: `CRUSH.LU`
- Headline: `TON TICKET DEMI-MATCH`
- Value line: `Une introduction locale, ludique et volontaire.`
- Role: `TON RÔLE : {role_a}`
- Counterpart: `CHERCHE : {role_b}`
- CTA: `SCANNE POUR ACTIVER TON TICKET`
- Fallback: `crush.lu/fr/fouer/`
- Safety: `18+ • Participation volontaire • Aucun match garanti.`
- Privacy: `Ne partage pas ce ticket. Infos et suppression sur la page d'activation.`

#### German

- Brand: `CRUSH.LU`
- Headline: `DEIN HALB-MATCH-TICKET`
- Value line: `Eine spielerische, freiwillige Begegnung vor Ort.`
- Role: `DEINE ROLLE: {role_a}`
- Counterpart: `SUCHT: {role_b}`
- CTA: `SCANNEN UND TICKET AKTIVIEREN`
- Fallback: `crush.lu/de/fouer/`
- Safety: `18+ • Freiwillige Teilnahme • Kein Match garantiert.`
- Privacy: `Teile dieses Ticket nicht. Infos und Löschung auf der Aktivierungsseite.`

#### English

- Brand: `CRUSH.LU`
- Headline: `YOUR HALF-MATCH TICKET`
- Value line: `A playful, voluntary local introduction.`
- Role: `YOUR ROLE: {role_a}`
- Counterpart: `LOOKING FOR: {role_b}`
- CTA: `SCAN TO ACTIVATE YOUR TICKET`
- Fallback: `crush.lu/en/fouer/`
- Safety: `18+ • Voluntary participation • No match guaranteed.`
- Privacy: `Do not share this ticket. See the activation page for information and deletion.`

The printed record may contain only the approved copy, role labels from a finite
reviewed list, an opaque ticket reference, QR code, and fallback URL. It must not
use `EventRegistration.id` or print visitor data.

### Landing page

#### French

- Headline: `Active ton Ticket Demi-Match`
- Body: `Crush.lu te propose une introduction ludique avec une autre personne participante. Confirme que tu as 18 ans, lis comment tes données seront utilisées, puis choisis librement de participer.`
- Primary CTA: `Activer mon ticket`
- Information CTA: `En savoir plus avant de participer`
- Success: `Ticket activé. Reviens sur cette page pour voir si ton demi-match est prêt. Aucune rencontre n'est garantie.`
- Invalid or expired: `Ce ticket ne peut pas être activé. Demande de l'aide à l'équipe Crush.lu.`
- Stop CTA: `Arrêter de participer et supprimer mes données d'activation`

#### German

- Headline: `Aktiviere dein Halb-Match-Ticket`
- Body: `Crush.lu bietet dir eine spielerische Begegnung mit einer anderen teilnehmenden Person. Bestätige, dass du 18 Jahre alt bist, lies, wie deine Daten verwendet werden, und entscheide freiwillig, ob du teilnehmen möchtest.`
- Primary CTA: `Mein Ticket aktivieren`
- Information CTA: `Mehr erfahren, bevor ich teilnehme`
- Success: `Ticket aktiviert. Kehre zu dieser Seite zurück, um zu sehen, ob dein Halb-Match bereit ist. Eine Begegnung ist nicht garantiert.`
- Invalid or expired: `Dieses Ticket kann nicht aktiviert werden. Bitte das Crush.lu-Team um Hilfe.`
- Stop CTA: `Teilnahme beenden und meine Aktivierungsdaten löschen`

#### English

- Headline: `Activate your Half-Match Ticket`
- Body: `Crush.lu offers a playful introduction to another participant. Confirm that you are 18, read how your data will be used, then freely choose whether to take part.`
- Primary CTA: `Activate my ticket`
- Information CTA: `Learn more before taking part`
- Success: `Ticket activated. Return to this page to see whether your half-match is ready. An introduction is not guaranteed.`
- Invalid or expired: `This ticket cannot be activated. Ask the Crush.lu team for help.`
- Stop CTA: `Stop participating and delete my activation data`

### Organic social support copy in French

This copy may be used only after the public flow passes every launch gate:

> À la Schueberfouer, Crush.lu teste une nouvelle façon de faire connaissance :
> un Ticket Demi-Match à activer sur place. Participation réservée aux 18 ans et
> plus, volontaire, sans rencontre garantie. Retrouve l'équipe Crush.lu au
> Glacis pour découvrir le fonctionnement avant de choisir de participer.

Do not add a countdown, scarcity claim, reward, performance number, or paid boost
without a separate approval. German and English social variants should be
translated from the approved French source after the pilot date and location are
confirmed.

## 8. Funnel

### Stage 0: approach

A trained ambassador offers an explanation without collecting data. The visitor
must be 18 or over and may decline without further contact.

Exit measure: the visitor asks to receive a ticket. Do not track a passer-by or
ambassador conversation.

### Stage 1: anonymous ticket issue

The server creates an opaque, expiring activation record without visitor data. The
ambassador prints and hands over an 80 mm campaign ticket. Receiving a ticket is
not participation consent. A generated payload is not proof of a physical print.

Exit measure: distinguish record creation, print handoff, and confirmed field
issue so print failures do not inflate distribution.

### Stage 2: information

The visitor reads purpose, limits, data use, retention, deletion, support, and
participation terms in their selected language.

Exit measure: the visitor deliberately starts consent.

### Stage 3: participation consent

The visitor confirms age and records participation consent. Marketing consent is
a separate, optional control that defaults off.

Exit measure: a consent record is stored successfully.

### Stage 4: QR open and entry

The QR opens the public locale route. A future approved authentication path creates
or restores a session without sending the visitor through the existing event
registration or lobby.

Exit measure: a session is available and attribution is retained.

### Stage 5: activation

The session binds the ticket exactly once. Retries return the same result instead
of creating duplicate accounts or activations.

Exit measure: the ticket is in an activated state.

### Stage 6: product value

The page shows only real states. Pairing, chat, radar, and rewards remain absent
unless implemented, staffed, and approved. A truthful waiting state is acceptable.

Exit measure: the future product owner defines the first-value milestone before
build approval.

### Stage 7: stop or continue

The visitor can stop, request deletion, get support, and optionally continue to a
full Crush.lu profile. Profile completion is not required to read campaign
information.

## 9. Channel plan

| Channel                    | Role                                       | Current state | Rule                                                                                 |
| -------------------------- | ------------------------------------------ | ------------- | ------------------------------------------------------------------------------------ |
| Street team at the Glacis  | Primary discovery and explanation          | OFF           | Needs venue permission, named staff, training, visitor cap, and a kill switch        |
| 80 mm thermal ticket       | Anonymous physical handoff on request      | OFF           | Receiving it is not consent; never reuse the event check-in ticket                   |
| Organic Instagram/Facebook | Tell people where and how the pilot works  | OFF           | Publish only after the destination and support window are live                       |
| Existing-member email      | Optional community notice, not acquisition | OFF           | Use only newsletter-opted-in recipients and direct unsubscribe links                 |
| Existing-member WhatsApp   | Optional community notice, not acquisition | OFF           | Requires explicit WhatsApp opt-in, a verified phone, and an approved locale template |
| Existing-member web push   | Optional community notice, not acquisition | OFF           | Requires an enabled push subscription                                                |
| Partner channels           | Optional future distribution               | UNAVAILABLE   | Needs a written partner agreement and approved copy                                  |
| Paid media                 | None                                       | NOT APPROVED  | Current budget is EUR 0; no spend                                                    |

The Campaign Dashboard may support existing-member notices, but those users belong
in a separate retention/community cohort. Do not count sends, clicks, or activations
from existing accounts as acquired members.

## 10. Measurement contract

### Event rules

- Optional analytics events fire only under the site's current consent behavior.
- Essential activation and security records must be documented separately from
  analytics consent.
- Analytics failure must not block QR resolution, consent, activation, or deletion.
- Never send phone, email, name, birth date, orientation, preferences, free text,
  full token, IP address, user agent, or counterpart identity as event properties.
- `ticket_id` below means a random, non-reversible analytics identifier, not the
  activation token or a database primary key exposed to the visitor.
- Server-side events use idempotency keys so refreshes and retries do not double
  count milestones.

### Proposed events

| Event                           | Trigger                                                   | Allowed properties                                                |
| ------------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------- |
| `fouer_ticket_record_created`   | Server creates an activation record                       | `campaign_id`, `ticket_id`, `language`, `batch_id`                |
| `fouer_print_handoff_succeeded` | RawBT handoff returns success                             | `campaign_id`, `ticket_id`, `printer_station_id`                  |
| `fouer_ticket_issued`           | Staff confirms the paper ticket was handed to the visitor | `campaign_id`, `ticket_id`, `language`, `batch_id`                |
| `fouer_information_opened`      | Public information page validates an issued ticket        | `campaign_id`, `ticket_id`, `language`, `token_state`, UTM values |
| `fouer_consent_started`         | Visitor deliberately opens the participation step         | `campaign_id`, `ticket_id`, `language`                            |
| `fouer_consent_completed`       | Participation consent is stored                           | `campaign_id`, `ticket_id`, `language`, `marketing_consent`       |
| `sign_up`                       | A new account or approved guest session is created        | GA4 standard event with the approved `method` only                |
| `fouer_ticket_activated`        | Ticket binds idempotently to the participant session      | `campaign_id`, `ticket_id`, `language`                            |
| `fouer_first_value_reached`     | The approved first-value state becomes real               | `campaign_id`, `ticket_id`, `value_state_version`                 |
| `fouer_profile_claimed`         | Participant completes the approved profile milestone      | `campaign_id`, `ticket_id`                                        |
| `fouer_participation_stopped`   | Participant stops or requests deletion                    | `campaign_id`, `ticket_id`, `reason_category`                     |

Do not emit pairing, chat, reward, or redemption events until those capabilities are
in the approved scope. If added later, each needs an exact product trigger rather
than a page-view proxy.

### Funnel metrics

- Information rate: unique valid information opens / tickets issued.
- Consent completion: consent completions / consent starts.
- Issue success: tickets physically issued / ticket records created.
- Activation rate: unique activated tickets / tickets issued, also reported per
  unique information open.
- New-account rate: new campaign accounts / activated tickets. Existing members
  are excluded from the numerator.
- First-value rate: tickets reaching the approved first-value state / activations.
- Profile-claim rate: new campaign accounts that complete the milestone / new
  campaign accounts.
- Stop and deletion rates: each outcome / activated tickets, reported separately.
- Operational failure rates: invalid, expired, duplicate, print, authentication,
  and support-assisted cases / ticket records created.

No acquisition or conversion target is approved. There is no pilot baseline,
capacity, staffing plan, or approved budget from which to derive an honest target.
The owner must set go, stop, safety, and capacity thresholds before a live pilot.

### 2026 closure checks

- acquisition tickets issued: 0;
- campaign QR codes published: 0;
- campaign messages sent: 0;
- paid media and campaign hardware spend: EUR 0;
- prospect records created: 0;
- campaign-driven changes to the event check-in ticket: 0.

## 11. Rollout and gates

### Phase 0: current closure

Keep every channel off. Do not create campaign records, public destinations,
tracking links, Meta templates, print batches, or hardware orders.

### Phase 1: approval

A new product-owner decision must name the year, dates, venue permission, budget,
owner, staff, support window, visitor cap, and stop owner. Privacy and security
review must approve the data model, legal basis, consent, retention, deletion,
authentication, token threat model, and incident path.

### Phase 2: internal test

Use staff and test records only. Test the public locale routes, authentication,
consent, idempotent activation, deletion, support, analytics consent behavior, and
kill switch. Print and scan on the actual 80 mm device with the RawBT server
running.

### Phase 3: bounded live pilot

Start with one staffed shift and an approved visitor cap. Stop immediately for a
login wall, broken QR, duplicate account, PII leak, misleading promise, failed
deletion, unsafe product state, or inability to disable ticket issue.

### Phase 4: review

Compare the complete funnel, operational failures, and safety outcomes with the
thresholds approved before the pilot. Expansion and paid spend require a new
owner decision.

### Launch checklist

Every item is required:

- [ ] A newer product-owner decision supersedes `t_98963758`.
- [ ] Dates, venue permission, owner, staffing, support, visitor cap, budget, and
      rollback owner are recorded.
- [ ] Pairing, chat, radar, rewards, and profile claim are approved or removed.
- [ ] `/fr/fouer/`, `/de/fouer/`, and `/en/fouer/` work without authentication.
- [ ] The visitor can read information before signup.
- [ ] Public session creation passes security and abuse review.
- [ ] Activation is expiring, single-use, idempotent, revocable, and supportable.
- [ ] Participation consent and marketing consent are separate.
- [ ] Retention, deletion, block, report, support, and incident paths are tested.
- [ ] Printed content and telemetry contain no PII or sensitive preferences.
- [ ] French source copy and German/English variants receive human review.
- [ ] Locale and attribution persist through the full journey.
- [ ] Mobile accessibility checks pass for focus, labels, contrast, errors, and
      zoom.
- [ ] Hardware is 80 mm, 48-column, verified ESC/POS, Bluetooth SPP,
      battery-powered, and RawBT-compatible.
- [ ] A physical print has no clipping, accidental wraps, or unreadable QR.
- [ ] The fallback URL works when the QR cannot be scanned.
- [ ] Field devices run the separate Server for RawBT app with battery
      optimisation disabled.
- [ ] Print failure cannot produce a false ticket-issued state.
- [ ] Event schemas, deduplication, consent, retention, and deletion pass tests.
- [ ] The owner sets conversion, safety, capacity, and stop thresholds.
- [ ] The staff script, support rota, kill switch, and rollback rehearsal are
      complete.
- [ ] The product owner gives final written go.

Any unchecked item means NO-GO.

## 12. Assumptions and blocking facts

### Verified assumptions

- The 2026 activation and ticket-only fallback are cancelled.
- Existing thermal printing is for registered event check-in, not anonymous
  acquisition.
- The product supports French, German, and English.
- The Campaign Dashboard applies channel consent to existing users and provides
  PII-minimized click tracking.
- Optional analytics currently use Consent Mode v2 and default to denied.
- The current growth budget is EUR 0 and organic-only.

### Facts that block a future launch

These are not blockers to preparing this brief. They are hard launch gates:

- no newer product-owner approval;
- no approved date, venue permission, staff plan, visitor cap, support owner, or
  budget;
- no public campaign route or anonymous activation model;
- no approved public authentication and session path;
- no approved legal basis, retention period, deletion contract, or token threat
  model;
- no decision on pairing, first value, chat, radar, or rewards;
- no pilot thresholds.

An implementer must not fill these gaps by inference.

## 13. Handoff

For the current 2026 task graph, make no campaign-specific product change and do
not modify the live event ticket. A future implementation task may use this brief
only after a newer owner decision resolves the launch gates above.
