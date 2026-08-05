# A single event-eligibility predicate for `crush_lu`

**Date:** 2026-08-05
**Status:** proposed
**Continues:** [`docs/superpowers/specs/2026-07-27-profile-requirement-audit.md`](../specs/2026-07-27-profile-requirement-audit.md)

`MeetupEvent.profile_requirement` decides who may register for an event. The rule
is written out independently in four places. The audit spec above exists because
that duplication already failed once in production: `profile_exists` silently
locked out every verified member while the backend was green. The member
dashboard now wants "the upcoming events you can actually join, and for the rest,
the one thing that would unlock it" — building that today means a fifth copy.

This document proposes a single predicate, argues for building it in-house rather
than adopting a library, and stages the migration.

---

## 0. Corrections to the brief

The task brief was written from an earlier reading of the code and had drifted.
Everything below was re-verified against the worktree at
`.claude/worktrees/serene-taussig-d8932d`.

| Brief said | Verified reality |
|---|---|
| The rule is implemented **3×** | **4×**. The third copy is not a template — it is Python: `crush_lu/views_coach.py:2713-2839` rebuilds all six branches as **querysets** to pick SMS invitees. `crush_lu/admin/events.py:66-112` holds a fourth copy of the semantics as organizer-facing labels (`STANDARD_CHOICES`/`ADVANCED_CHOICES` at 66–83, `AUDIENCE_DESCRIPTIONS` at 84–112). |
| `coach_event_sms_invite.html:156` is a partial copy of the ladder | It is **9 lines** (156–164) of descriptive header text, with no `profile_exists` or `completed` case and no CTA. Harmless; not a gate. |
| `event_detail.html` ladder is ~250 lines from line 522 | Start line correct. It is **328 lines, 522–849**, nested inside a larger action area spanning 362–872. |
| Template context uses `can_register` / `is_registered` | Neither name exists. The real context keys are `user_profile`, `user_registration`, `event_full_for_user`, `premium_reserved_seat_available`, `language_requirement_met`, plus the model property `event.is_registration_accepting`. |
| `CLAUDE.md` at repo root | Does not exist. The repo's convention doc is **`AGENTS.md`**. |
| Fields `profile_completed` / `is_participation_ready` | Do not exist anywhere in the repo. "Participation-ready" is only the *display label* for `profile_requirement="completed"`; the gate's actual test is the inline expression at `views_events.py:858-860`. |
| Tests must use literal paths because `reverse("crush_lu:…")` 404s under `HTTP_HOST=crush.lu` | `test_profile_requirement_gates.py` uses **`reverse()`** and sends **no** `HTTP_HOST`. It sets `pytestmark = [..., pytest.mark.urls("azureproject.urls_crush")]` (line 44), which swaps the URLconf directly and bypasses host routing. Both idioms exist in the suite; see §6. |
| `crush_lu/tests/conftest.py` | Does not exist. Only the repo-root `conftest.py` applies to these tests. |
| Capacity is a gate | It is **not**. Capacity never refuses a registration — it downgrades `status` to `"waitlist"` (`views_events.py:1146-1172`). The only hard stop after the requirement/age/language checks is `is_registration_accepting`. |
| `docs/plans/` | Does not exist. Repo precedent is `docs/superpowers/plans/` beside `docs/superpowers/specs/`, which is where this file lives. |

**Two live defects in the coach SMS invite pool, both costing real money today.**
Neither is caused by this refactor and neither needs it — both are independently
shippable, and PR5 fixes them as a side effect:

1. **Language** — the `unverified`/`none` branches admit profiles with no declared
   languages (`views_coach.py:2770-2773`, `:2834-2837`), whom
   `user_meets_language_requirement` then refuses at registration.
2. **Age** — `sub_age_q` (`:2694-2696`) admits profiles with **no date of birth**
   via `Q(profile__date_of_birth__isnull=True)`, but `event_register` refuses a
   missing DOB outright on an age-restricted event (`views_events.py:1001-1009`).

Both send SMS invites to members the gate will bounce. Details in §4.3.

**One more finding the brief did not have.** `event_detail.html` branches on
`user_profile.is_approved` (lines 539, 644, 729, 777 and 779 — five branches,
the last nested inside the 777 `elif`) while `event_register`
branches on `verification_status` (lines 884, 907, 939, 973). `CrushProfile.save()`
syncs `is_approved → verification_status` but never the reverse, and the model
comments `is_approved` as legacy. **The page and the gate read different fields
for the same concept.** They agree today only because every path that sets one
also sets the other. That is the drift hazard in its purest form, and collapsing
the template removes it.

### The real order of checks in `event_register` (`views_events.py:797-1253`)

Any predicate must preserve this order, because each stage produces a different
message and a different redirect target.

| Lines | Stage |
|---|---|
| 804–845 | `is_private_invitation` — **mutually exclusive** with the ladder; a private event never evaluates `profile_requirement` |
| 846–994 | the `profile_requirement` ladder (`completed`, `approved`, `coach_assigned`, `unverified`, `profile_exists`, else `none`) |
| 996–1019 | age (`min_age > 18 or max_age < 99` gate, then range check) |
| 1021–1026 | language, via `event.user_meets_language_requirement(user)` |
| 1028–1034 | duplicate registration (cancelled rows excluded, so they are reusable) |
| 1036–1039 | `is_registration_accepting` — **silent** redirect, deliberately no flash |
| 1053–1239 | POST: `select_for_update`, re-check, capacity → waitlist, payment status |
| 1240–1245 | GET `else`: builds the unbound form |

Note the redirect asymmetry **within the ladder** (846–994): a **missing** profile
redirects to `create_profile`; a **wrong-state** profile redirects to
`event_detail` — except in the `completed` branch, which sends it to
`create_profile`. This is load-bearing and must survive the refactor.

The private-invitation path (804–845) does **not** follow that rule and is a
genuine exception: an *invited existing user* with no profile goes to
`create_profile` (819–830), but an *external guest* with no profile is logged as
a security issue and sent to **`event_detail`** (831–845). Since `evaluate()` is
never called on the private path (§8), this asymmetry stays where it is — but do
not "tidy" it into the general rule during PR2.

---

## 1. Findings — build in-house

Research was run as a 110-agent fan-out over primary sources. It hit a session
limit partway through adversarial verification, so eight claims carry 3-vote
confirmation and the remainder are single-source extractions from primary docs.
Confidence is marked per claim. Anything unverified is flagged as such rather
than asserted.

### No library fits

- **`django-rules` cannot filter querysets.** Requested in
  [issue #40](https://github.com/dfunckt/django-rules/issues/40) (opened
  2016-09-13), closed the next day by maintainer dfunckt: *"I think this should
  go in a separate app and it actually wouldn't even need to depend on rules."*
  This settles a commonly repeated but wrong claim. **(confirmed 3-0)**
- **`django-guardian`** is row-level ACL: it stores a permission row per
  (user, object). Our rule is a pure function of profile state, so every
  verification transition would have to fan out and rewrite rows for every
  future event. Wrong shape. *(judgement — not a cited finding)*
- **Oso / `django-oso` is deprecated.** The [repo](https://github.com/osohq/oso)
  description reads *"Deprecated: See README"*. **(confirmed 2-0)** Worse for our
  purposes, Oso's own deprecation notice names data-filtering performance as a
  reason and **explicitly blames the ORM hooks, naming `django-oso`**. And its
  Django data filtering emits only a silently narrowed QuerySet — **no reason,
  no diagnostic, no "why not"**: unauthorized rows just do not appear. It fails
  both halves of what we need. *(single-source, primary)*
- **`casbin` / `django-casbin`** — **not verified.** The verifiers died before
  reaching it. No claim is made here in either direction.

### The right *pattern* is well documented; the packages are not installable

- **Bridgekeeper** — one rule object exposing both `Rule.check(user, instance)`
  and `Rule.filter(user, queryset)`
  ([docs](https://bridgekeeper.readthedocs.io/en/latest/api/rules.html)).
  **(confirmed 3-0)** So "one predicate, two evaluation modes" is an existing
  Django-ecosystem pattern, not an invention. Two details worth stealing:
  - It needs **`UNIVERSAL` / `EMPTY` sentinels** alongside `Q`, because a bare
    `Q()` cannot express "matches everything" or "matches nothing" — an empty
    `Q()` reads as *no filter*, not as *deny all*. **(confirmed 3-0)**
  - Its own docs warn that the Python `==` path and the database-equality path
    **can disagree** on imprecise or mistyped values. **(confirmed 3-0)**
- **`django-predicate`** — its `P` subclasses `Q`, so one object both filters and
  evaluates, with no translation step. It ships an explicit divergence harness,
  `predicate/debug.py::orm_eval()`, which runs the in-memory evaluation *and* a
  real ORM round-trip for the same instance and asserts they agree. Declared
  **unmaintained on 2022-04-27**.
- **`django-qtools`** — `@q_method` declares one `Q` used as an ORM filter, as a
  joined filter, and as a per-instance predicate via `obj_matches_q`. It was
  forced to ship **per-backend lookup adapters** (`SqLiteCompatibleLookups`,
  `MySqlCompatibleLookups`) to paper over collation, case sensitivity, NULL
  three-valued logic and empty-regex behaviour — and it documents that a
  queryset traversing a one-to-many relation returns **duplicate rows** while the
  in-memory path never does. Targets **Django 1.7/1.8**; unusable here.

### Django core will not help

- `Q.check(against, using=...)` **does exist**, in
  [`django/db/models/query_utils.py`](https://github.com/django/django/blob/main/django/db/models/query_utils.py)
  — so the common assertion that Django has no way to evaluate a `Q` against one
  instance is wrong. But it is **not a Python evaluator**: it builds a model-less
  `Query`, annotates each key as a `Value()`, and **asks the database**. One SQL
  round-trip per call. And it **fails open** — a `DatabaseError` is caught, logged
  at WARNING, and the method returns `True`.
- `BaseConstraint.validate()` is the same story, and additionally **skips**
  (rather than reports) a constraint whose fields are excluded — a skipped
  constraint is indistinguishable from a satisfied one.
- Ticket [#30581](https://code.djangoproject.com/ticket/30581) shows Django core
  **considered and rejected** inferring a Python validator from a declarative `Q`,
  on the grounds that the semantics cannot be made to match SQL on every backend.

So a predicate built on Django's own `Q`-evaluation machinery would be an N+1
**that grants access on error**. Not acceptable for a gate.

### Explainable denial has a standards precedent

- **XACML 3.0** separates **Obligation** — an action the enforcement point *must*
  discharge, and a conformant PEP must **deny** if it cannot — from **Advice**
  (§2.13), explicitly non-binding supplemental information the PEP may ignore.
  That is precisely the split we need between *the reason, always shown* and
  *the unlock action, sometimes absent*. XACML also pairs its Decision with a
  `<Status>` carrying `<StatusCode>` + `<StatusMessage>` — a machine-readable
  code beside a human-readable message.
- **Cedar** returns a decision plus diagnostics naming the determining policies —
  but a `Deny` caused by **default-deny returns an empty determining-policies
  list**, so the most common refusal explains nothing. The lesson: **denial
  reasons must be authored, not derived.** Cedar's docs endorse exactly the
  two-layer design we are proposing — engine emits structure, application decides
  what to tell the user.
- **OPA** correlates a decision to its explanation by `decision_id` out-of-band;
  its decision-log schema carries **no reason field**, and drop rules can suppress
  a denial's log entirely.
- **DRF** is the counter-example. `has_permission` / `has_object_permission`
  return bare booleans, and DRF explicitly documents that object permissions are
  **not** pushed into querysets for list responses, telling you to filter
  separately. That is the canonical primary-source statement of the
  double-implementation problem — and DRF's own remedy is a *second*
  implementation (`DjangoObjectPermissionsFilter` over django-guardian), not a
  translation of the first.
- **Becker & Nanz**,
  [*A Logic for State-Modifying Authorization Policies*](https://www.microsoft.com/en-us/research/wp-content/uploads/2007/08/tr-2007-105.pdf)
  (MSR TR-2007-105), frame explainable denial as the **dual** of the
  authorization decision and show one deductive evaluation can be conservatively
  extended to yield both the boolean and the structured "what was missing".
  Datalog-scoped; no ORM pushdown.

### Product prior art: nobody in this space does it well

- **Eventbrite** surfaces refusal as a small set of terse status strings on the
  listing *before* commitment — "Sold Out", "Sales ended", "N/A or Unavailable".
  But it names an unlock action for only **one** of them (Sold Out → contact the
  organizer, an off-platform human handoff, not a waitlist button). "Sales ended"
  is a bare fact. The audience-restriction denial discloses only that you are not
  in the eligible audience and names **no** criterion you could satisfy.
- **Luma** is worse for our purposes: approval-required tickets are
  **post-commitment and human-adjudicated**. The guest completes registration and
  has their card authorized *before* any decision. A decline is documented purely
  as releasing the payment hold — no reason code, no explanation, no remediation
  to the declined guest. The tri-state (pending/completed/declined) is surfaced to
  the **host**, not to the person being judged.

Doing "here's why, here's the one fix" *before* commitment would put Crush.lu
ahead of both.

### The funnel evidence supports it

- **Baymard**: only ~2% of sites use *adaptive* error messages targeted at the
  exact rule that failed; 98% show generic messaging. (Sample denominator not
  stated in the retrieved text — flagged.) Time-to-correct *"increases
  exponentially"* under generic messaging, with test participants spending up to
  five minutes resolving simple errors, and users who could not determine **why**
  a validation error occurred **abandoned**. Baymard also argues the specific
  reason **already exists inside the validation logic** — surfacing it is
  plumbing, not new logic. That is precisely our situation: `event_register`
  already knows which branch refused; it just throws that away into a redirect.
- **NN/g** ranks **error prevention above error messaging**: the best design stops
  the user reaching the failure. Applied here, that is the argument for badging
  the list, not just explaining at the gate.

### Recommendation

**Build a small in-house module, `crush_lu/services/eligibility.py`.**

The closest precedent is `crush_lu/services/profile_verification.py`: ~70 lines,
module-level functions with keyword-only arguments, a docstring explaining *why*
the shared abstraction exists, importing `CrushProfile` at module level, and
deliberately **not** re-exported from `services/__init__.py`.

**What would change my mind:** if eligibility ever comes to depend on a
per-(member, event) *relational* fact — "not blocked by any attendee of this
event", "hasn't attended three events this month", "their coach is hosting" —
then the set-collapse in §2 stops working and a genuine event-side `Q` becomes
necessary. At that point, copy Bridgekeeper's `check`/`filter`/`UNIVERSAL`/`EMPTY`
protocol rather than inventing one. Nothing in the current six requirements needs
it.

---

## 2. The design — the set-collapse

**This is the centre of the task, and the brief mis-framed it.** The brief assumes
we need a per-object predicate *and* a queryset filter, kept in sync, and treats
that as the hardest problem. For the direction the dashboard actually needs, we
do not need a queryset translation at all.

Every divergence failure mode the research turned up — SQL NULL three-valued
logic, collation, case sensitivity, JSON containment, join duplication, Python
`==` versus database equality, `Now()` skew — arises from **translating
comparisons over the model being filtered**. Avoid the translation and you avoid
the entire class of bug.

Observe: `profile_requirement` eligibility is a pure function of
`(requirement_code, profile_state)`, and for the dashboard the **member is fixed**
while the event varies. So evaluate the rule over the six codes in Python, once,
with **zero queries**, and the answer is a *set of strings*:

```python
ALL_REQUIREMENTS = frozenset(
    code for code, _label in MeetupEvent.PROFILE_REQUIREMENT_CHOICES
)


def allowed_requirements(profile) -> frozenset[str]:
    """The `profile_requirement` codes this member satisfies.

    The single source of truth. Pure, no queries, no database semantics — the
    per-object check and the queryset filter are both derived from this, so they
    cannot drift apart.

    Presumes an AUTHENTICATED member. `profile=None` means S0 — a logged-in
    account with no CrushProfile — NOT an anonymous visitor. `evaluate()` must
    reject anonymous users before calling this; see the warning below.
    """
    if profile is None:
        return frozenset({"none"})  # S0: an account with no CrushProfile

    status = profile.verification_status
    has_coach = bool(profile.assigned_coach_id)

    ok = {"none"}
    if status == "rejected":
        return frozenset(ok)  # rejected is admitted by `none` only

    ok.add("profile_exists")
    if has_coach:
        ok.add("coach_assigned")
    if status == "verified":
        ok.update({"approved", "completed"})
    else:
        ok.add("unverified")
        if status == "pending" and profile.phone_verified:
            ok.add("completed")
    return frozenset(ok)
```

Both representations now fall out of **one** function:

- **per-object** — `event.profile_requirement in allowed_requirements(profile)`
- **set-level** — `.filter(profile_requirement__in=allowed_requirements(profile))`

They cannot diverge, because there is only one implementation. And the generated
SQL is a plain, indexable `IN` over exact-match lowercase enum codes on a
non-nullable `CharField`: no NULLs, no collation, no case folding, no joins, no
JSON. Every documented failure mode is excluded **by construction**, not by
discipline.

> I verified this mapping by hand against `EXPECTED`
> (`test_profile_requirement_gates.py:58-71`) for all 7 states × 6 requirements
> plus S0 — 43 cells, all match. The audited spec passes **unchanged**.

> ⚠️ **`profile is None` is ambiguous and must be disambiguated by the caller.**
> `event_detail` (`views_events.py:485`) has **no** login decorator, and
> `event_list` is public too — so an anonymous visitor also arrives with
> `profile=None`. Treating that as S0 would make `can_register` true on an open
> `profile_requirement="none"` event with no age or language restriction, and the
> page would swap today's "Log in / sign up to register" prompt
> (`event_detail.html:851-865`) for a Register CTA that bounces off
> `@crush_login_required`. `evaluate()` must therefore check
> `user.is_authenticated` **first** and return a dedicated
> `REASON_LOGIN_REQUIRED` whose unlock is the login route. `allowed_requirements()`
> itself stays pure and presumes an authenticated member.

### The other axes

`profile_requirement` is not the only gate, and treating it as one would advertise
events the member is too young for. Each remaining axis gets the treatment its
semantics allow:

| Axis | Where evaluated | Why |
|---|---|---|
| **Age** | SQL — but **only for age-restricted events**: `Q(min_age__lte=18, max_age__gte=99) \| Q(min_age__lte=age, max_age__gte=age)` | `min_age`/`max_age` are non-nullable ints with defaults 18/99, so integer comparison is backend-neutral. ⚠️ The range check must **not** be applied unconditionally — see below. If `profile.age is None`, narrow to unrestricted events only (`min_age__lte=18, max_age__gte=99`), mirroring the gate's refusal. |
| **Deadline / published / cancelled** | SQL, with **`now` passed in from Python** | Never use `Now()` in the filter. Clock skew between the app and the database is a documented divergence; one `now` value feeds both halves. |
| **Language** | **Python only** | `MeetupEvent.languages` is a `JSONField`. `__contains` semantics on JSON differ across backends, and this is exactly the trap. Reuse the existing `event.user_meets_language_requirement(user)` (`models/events.py:654-686`), which already returns `(bool, message)` — so it already produces our denial message for free. ⚠️ But it re-fetches the profile itself (`profile = user.crushprofile`, line 663), ignoring the one already passed to `evaluate()` — see below. |
| **Capacity / waitlist** | Python, off `with_registration_counts()` | This is *availability*, not eligibility — and capacity never refuses, it waitlists (`views_events.py:1146-1172`). See the note on `is_registration_accepting` below: a **total-full event still accepts signups**, so `"waitlist"` is the correct availability, not `"closed"`. |

> ⚠️ **The age filter must reproduce the gate's `event_has_age_restriction`
> guard, not just its range check.** The gate computes
> `event_has_age_restriction = event.min_age > 18 or event.max_age < 99`
> (`views_events.py:999`) and **only then** range-checks `profile.age`. An event
> left at the defaults is never age-checked at all — so a member aged 100 can
> register for it today. Applying `max_age__gte=age` unconditionally would filter
> that same event out of their dashboard, because `99 >= 100` is false. The
> narrowing would then be *stricter* than the gate, which is the one direction
> §2's whole argument forbids: it would hide events the member is genuinely
> allowed to join. Hence the `Q(unrestricted) | Q(in range)` shape.

> ⚠️ **`user_meets_language_requirement` re-fetches the profile.** It does
> `profile = user.crushprofile` internally (`models/events.py:663`) rather than
> using the profile `evaluate()` was handed. Two consequences, and the smaller one
> is the query: Django caches the reverse one-to-one on the `user` instance, so a
> loop over events issues **one** redundant query, not one per event — this is
> *not* an N+1. The real hazard is correctness: the method silently evaluates a
> *different profile object* than the rest of `evaluate()`, so a caller that
> passes a profile from a bulk fetch, an unsaved edit, or a merge-in-progress gets
> two different answers from one verdict. Either seed the cache
> (`user._state.fields_cache`) before the loop, or — better — give the method an
> optional `profile=` parameter and pass the one already loaded.

> ⚠️ **Capacity is not one number: it is `public_capacity` vs `max_participants`,
> plus the gender pool.** `is_full_for(is_premium)` (`models/events.py:510-516`)
> measures a coach-assigned member against `max_participants` and everyone else
> against `public_capacity = max_participants - reserved_premium_seats`
> (`:505-508`), and `event_register` calls it with
> `is_premium=bool(profile.assigned_coach_id)` (line 1150). So availability
> computed from `with_registration_counts()`'s *totals alone* will show "open" to a
> non-coach member whose public seats are exhausted while reserved seats remain —
> and the POST then waitlists them. Availability must carry the same
> public-vs-total rule as the gate, on top of the gender-pool rule.

> ⚠️ **`is_registration_accepting` does NOT include a capacity check** — a claim
> worth pinning, because it has been misread in review. The property
> (`models/events.py:451-459`) is `is_published and not is_cancelled and now <
> registration_deadline`, nothing more. The capacity condition lives in the
> *separate* `is_registration_open` (`:461-469`), which the registration gate
> never consults — `event_register` checks `is_registration_accepting` at both
> line 1036 and line 1068. So a full event still accepts a POST and the seat is
> downgraded to `waitlist` at 1146-1172. Marking total-full events `"closed"`
> would make the dashboard refuse what the gate accepts, losing real waitlist
> signups.

So the queryset filter is a **sound narrowing** over the cheap indexable axes, and
the Python predicate is authoritative and produces the explanation. The dashboard
fetches one bounded window and evaluates in Python — which is exactly what
`views.py:549-557` already does today for `next_event`. Given Crush.lu's actual
scale (a handful of published upcoming events at a time), this is not a
compromise; it is right-sized. The N+1 risk was never per-event SQL — it was
per-event *profile* loads, and those vanish by loading the profile once.

### The one place a real `Q` is genuinely needed

The coach SMS invite pool inverts the direction: it fixes the **event** and varies
the **profile**. That does need a `Q` over `CrushProfile`, and that `Q` *can*
diverge from the Python predicate. Two mitigations, both structural:

1. **Restrict the vocabulary to `exact`, `in`, `isnull`** over non-nullable
   `CharField`s, `BooleanField`s and FK ids. That subset has identical semantics
   on SQLite and PostgreSQL. Everything the research documents as divergent —
   `contains` being case-insensitive on SQLite, `iexact` degrading to `exact` for
   non-ASCII, MySQL's case-insensitive default collation, Oracle's `NULL == ''`,
   join duplication — is outside it. **No `icontains`, no `contains`, no
   `iexact`, no JSON lookups, no reverse-relation traversal.**
2. **Prove equivalence over the whole input space.** The space is finite and tiny,
   so this can be a total enumeration rather than property-based sampling — but it
   must be enumerated over the predicate's *actual inputs*
   (`verification_status` × `phone_verified` × `has_coach` = 16 profiles × 6
   requirements = 96 cells), **not** over the 7 rows of `STATES`. `STATES` carries
   a coach on only two rows and omits `incomplete + coach` and `pending + coach`
   entirely, so a matrix built on it would miss a whole axis. See §6 for why this
   distinction has teeth.

Mitigation 1 matters because **CI runs the test suite on SQLite**
(`.github/workflows/test-and-validate.yml:109`, *"Run tests (parallel with
pytest-xdist, SQLite)"*) while production is PostgreSQL. This repo already has
that scar: `select_for_update` is a silent no-op on SQLite, so lock-ordering bugs
pass every CI run and fail only on production Postgres. A queryset-half proved
only on SQLite proves nothing — unless the vocabulary makes the two backends
agree by construction.

```python
def profile_pool_q(requirement: str) -> Q:
    """Inverse of `allowed_requirements`: which profiles satisfy `requirement`.

    Vocabulary is deliberately limited to exact/in/isnull over non-nullable
    columns so SQLite (CI) and PostgreSQL (production) cannot disagree.
    """
    if requirement == "approved":
        return Q(verification_status="verified")
    if requirement == "completed":
        return Q(verification_status="verified") | Q(
            verification_status="pending", phone_verified=True
        )
    if requirement == "coach_assigned":
        return ~Q(verification_status="rejected") & Q(assigned_coach__isnull=False)
    if requirement == "unverified":
        return ~Q(verification_status__in=("verified", "rejected"))
    if requirement == "profile_exists":
        return ~Q(verification_status="rejected")
    if requirement == "none":
        return Q()
    # Never fall through to Q(). An unknown code — a typo, a bad row, or a
    # seventh choice added to the model without updating this function — would
    # silently mean "everyone" here while `allowed_requirements()` denies it,
    # breaking the one-predicate invariant in the most dangerous direction:
    # an over-broad SMS invite pool.
    raise ValueError(f"unknown profile_requirement: {requirement!r}")
```

`~Q(...)` is safe here specifically because `verification_status` is non-nullable
with a default — there is no three-valued-logic trap.

---

## 3. Proposed API

`crush_lu/services/eligibility.py`:

```python
@dataclass(frozen=True)
class Reason:
    """Why a member cannot register, and the single thing that would fix it.

    `code` is stable and machine-readable; `message` is the member-facing string.
    The unlock pair is XACML's *Advice*, not *Obligation* — optional, and absent
    when nothing the member can do would help (e.g. a rejected profile).
    """

    code: str                          # "profile_missing", "not_verified", …
    message: str                       # already interpolated — see below
    # Where the GATE sends you on a failed POST. Preserves today's behaviour
    # exactly — which is NOT simply "create_profile if missing, else
    # event_detail". The `completed` branch also sends a *wrong-state* profile
    # (incomplete, or pending without a verified phone) to create_profile, since
    # finishing the profile is the actual remedy. See §0 and the note below.
    redirect_url_name: str = "crush_lu:event_detail"
    # The ADVISORY unlock shown on a card. Independent of the redirect: an
    # "unverified members only" denial has a redirect but no unlock, while a
    # language denial has both, pointing at different places.
    unlock_label: str | None = None
    unlock_url_name: str | None = None  # URL *name*, e.g. "crush_lu:edit_profile"
    unlock_query: dict | None = None    # e.g. {"next": request.path} for login

    @property
    def has_unlock(self) -> bool:
        return bool(self.unlock_url_name)

    def unlock_href(self) -> str:
        """Resolve the name, then append the query. i18n prefix preserved."""
        url = reverse(self.unlock_url_name)
        return f"{url}?{urlencode(self.unlock_query)}" if self.unlock_query else url


@dataclass(frozen=True)
class Eligibility:
    """Eligibility and availability are different axes; keep them apart."""

    eligible: bool                     # requirement + age + language
    denial: Reason | None              # set iff not eligible; never None on a denial
    availability: str                  # "open" | "waitlist" | "closed" | "registered"

    @property
    def can_register(self) -> bool:
        return self.eligible and self.availability in ("open", "waitlist")

    @property
    def one_step_away(self) -> bool:
        # Availability matters here too: an event past its registration deadline
        # cannot be unlocked by *any* member action, so promising "Complete your
        # profile" on a closed event is a lie. Only offer an unlock when taking
        # it would actually lead to a registerable event.
        return (
            not self.eligible
            and self.denial is not None
            and self.denial.has_unlock
            and self.availability in ("open", "waitlist")
        )
```

Entry points:

```python
def allowed_requirements(profile) -> frozenset[str]: ...
def evaluate(
    user, event, *, profile, now, registration=None, check_requirement=True,
    next_path=None,   # request.path — needed to build the login unlock's ?next=
) -> Eligibility: ...
def eligible_events_q(profile, *, now) -> Q: ...       # events side (member fixed)
def profile_pool_q(requirement: str) -> Q: ...         # profiles side (event fixed)
```

`evaluate()` runs the axes in the gate's existing order — authentication,
requirement, age, language — and returns on the first failure, so the reported
reason matches the message `event_register` produces today.

**Static reasons are module-level constants; event-specific ones are built inside
`evaluate()`.** `views_events.py` already imports `gettext_lazy as _` (line 4), so
lifting the fixed strings out of the view is safe as-is.

But two of the existing denials interpolate event data and **cannot** be
constants:

- **Age** — `"This event is restricted to ages %(min)d–%(max)d…"`
  (`views_events.py:1013-1017`), formatted with `event.min_age`/`event.max_age`.
- **Language** — `user_meets_language_requirement()` already returns
  `(False, "This event requires one of these languages: %(languages)s…")` with the
  event's language display names joined in (`models/events.py:676-684`).

Freezing those as constants would ship raw `%(min)d` placeholders to members, or
silently replace them with a vaguer message than the gate produces today. So
`Reason.message` holds an **already-interpolated** string: the constants cover the
fixed reasons (`profile_missing`, `not_verified`, `no_coach`, `rejected`,
`login_required`), and `evaluate()` constructs the age and language reasons at
call time — reusing the existing lazy msgids so the DE/FR catalogs still match and
PRs 1–3 stay at zero `.po` cost.

> ⚠️ **URL *names*, never literal paths.** The crush URLconf wraps its routes in
> `i18n_patterns` (`azureproject/urls_crush.py:12`, and `AGENTS.md:88-90`), so a
> hardcoded `/events/<id>/register/` loses the `/de/` or `/fr/` prefix and sends
> DE/FR members to the default-language route. The existing code already does
> this correctly — `event_detail.html:551` uses
> `{% url 'crush_lu:event_register' event.id %}` and `event_register` uses
> `redirect("crush_lu:create_profile")` — so `Reason` stores a **URL name** and
> resolution happens at render/redirect time.
>
> Literal paths are correct in exactly one place: **test assertions**, where
> `_detail_has_register_cta()` greps the rendered body for
> `/events/<id>/register/`. Do not generalise that to templates or views.

### Caller: a view

```python
verdict = eligibility.evaluate(
    request.user, event, profile=profile, now=timezone.now()
)
if not verdict.eligible:
    messages.error(request, verdict.denial.message)
    if verdict.denial.redirect_url_name == "crush_lu:event_detail":
        return redirect("crush_lu:event_detail", event_id=event.id)
    return redirect(verdict.denial.redirect_url_name)
```

> ⚠️ **The gate redirect and the advisory unlock are different fields on purpose.**
> Conflating them breaks one of the two callers. Today a language failure
> redirects to `event_detail` (`views_events.py:1026`) — but the *unlock* a
> dashboard card should offer is "Update your languages", pointing at
> `edit_profile`. If one field served both, either PR2 silently changes where a
> failed registration lands (a member-visible behaviour change the gate matrix
> would not catch, since it asserts on row creation, not redirect targets), or the
> dashboard has no unlock to render and the one-step-away card disappears.
> `redirect_url_name` preserves today's behaviour exactly; `unlock_url_name` is
> new and advisory.
>
> **Per-reason redirect targets, exactly as they are today** — the mapping is not
> a rule of thumb, it is a table PR2 must reproduce:
>
> | Reason | `redirect_url_name` | Source |
> |---|---|---|
> | profile missing (any requirement) | `create_profile` | 869/877, 899, 932, 963, 989 |
> | **`completed`, profile exists but not ready** | **`create_profile`** | **869** |
> | `approved`, not verified | `event_detail` | 891 |
> | `coach_assigned`, rejected / no coach | `event_detail` | 915, 924 |
> | `unverified`, verified / rejected | `event_detail` | 946, 955 |
> | `profile_exists`, rejected | `event_detail` | 981 |
> | age out of range | `event_detail` | 1019 |
> | age restricted, no DOB | `create_profile` | 1009 |
> | language mismatch | `event_detail` | 1026 |
>
> The `completed` row is the one that breaks a naive "missing → create_profile,
> otherwise → event_detail" reading: an incomplete or phone-unverified profile is
> *not* missing, yet the gate still routes it to the completion flow because that
> is the remedy. Getting this wrong silently strands those members on the event
> page. The gate matrix will not catch it — it asserts row creation, not redirect
> targets — so PR2 needs an explicit assertion per row of this table.

### Caller: a template

```django
{% if eligibility.can_register %}
  <a href="{% url 'crush_lu:event_register' event.id %}" class="btn-crush-primary btn-block btn-lg">
    {% if eligibility.availability == "waitlist" %}{% trans "Join Waitlist" %}{% else %}{% trans "Register for This Event" %}{% endif %}
  </a>
{% elif eligibility.denial %}
  {% include "crush_lu/components/eligibility_notice.html" with verdict=eligibility %}
{% else %}
  {# Eligible but not bookable — availability, not a denial. #}
  {% include "crush_lu/components/availability_notice.html" with verdict=eligibility %}
{% endif %}
```

> ⚠️ **The CTA label comes from `eligibility.availability`, not
> `event_full_for_user`.** The existing context flag is
> `event.is_full_for(is_premium=…)` (`views_events.py:671`) — total/public capacity
> only, blind to the gender pool. On a gender-limited event where this member's
> pool is full but public seats remain, that flag is `False`, so the old label
> reads "Register" while the POST immediately waitlists them
> (`views_events.py:1152-1159`). Driving the label from the verdict is the whole
> point of centralising availability; leaving it on `event_full_for_user` would
> keep a mismatch alive in the one place members actually click.

> ⚠️ **Three branches, not two.** `eligible` and `bookable` are separate axes (§2),
> so `not can_register` does **not** imply a denial exists. An eligible member
> viewing an event past its deadline has `eligible=True`, `availability="closed"`
> and `denial=None` — feeding that into the denial partial renders nothing and
> silently drops today's "Registration is closed for this event." box
> (`event_detail.html`, **six** copies — lines 565, 623, 685, 755, 805, 844, one
> per ladder branch). The
> availability states get their own small partial. A `{% if %}`/`{% else %}` here
> would be a member-visible regression on every closed event.

Inside the partial the unlock button uses `unlock_href()`, which resolves the name
*and* appends any query string:

```django
{% if verdict.denial.has_unlock %}
  <a href="{{ verdict.denial.unlock_href }}" class="btn-crush-outline btn-sm">
    {{ verdict.denial.unlock_label }}
  </a>
{% endif %}
```

> The query string is not decoration. Today `event_detail.html:859` renders
> `{% url 'crush_lu:login' %}?next={{ request.path }}` for anonymous visitors — so
> a `REASON_LOGIN_REQUIRED` carrying only a bare route name would drop `next` and
> dump the member on the default post-login page instead of back at the event they
> were trying to join. The service has no `request`, so the caller passes
> `next_path=request.path` and `evaluate()` puts it in
> `unlock_query={"next": next_path}`. Without that parameter the login `Reason`
> could only guess a return URL — the signature has to carry it explicitly, or the
> login unlock must be built in the view instead.
> (`{% url verdict.denial.unlock_url_name %}` with an unquoted variable is valid
> Django and resolves dynamically, but it cannot carry the query — hence
> `unlock_href()`.)

Note the include parameter is named `verdict`, **not** `block` — Django binds a
truthy `BlockNode` under that name inside every `{% block %}`, so `{% if block %}`
is unconditionally true on any page extending a base template.

### Caller: a queryset

```python
now = timezone.now()

# Preload the member's live registrations — one query, not one per event.
# Without this, `availability` can never become "registered" and an
# already-registered member is offered a duplicate Register CTA.
registered = dict(
    EventRegistration.objects
    .filter(user=user, event__in=Subquery(candidates.values("pk")))
    .exclude(status="cancelled")
    .values_list("event_id", "status")
)

joinable = []
for event in candidates.iterator():          # NOT sliced yet — see below
    verdict = eligibility.evaluate(
        user, event, profile=profile, now=now,
        registration=registered.get(event.id),
    )
    if verdict.can_register:
        joinable.append((event, verdict))
    if len(joinable) >= WANTED:
        break
```

Three things this shape gets right that a naive one does not:

1. **Registrations are preloaded into a dict.** `evaluate()` takes the member's
   registration status as an argument rather than querying for it, so
   `availability` can reach `"registered"` without an N+1. Omit it and an
   already-registered member gets a second Register CTA.
2. **The slice comes *after* the Python pass, not before.** Language is
   deliberately evaluated only in Python (§2), so `[:12]` up front can return
   twelve language-ineligible events and hide the joinable ones behind them.
   Take from a bounded candidate queryset until `WANTED` survive, with a hard
   ceiling so a pathological catalogue cannot walk the whole table.
3. **One profile load, one event query, one registration query** — constant, not
   per-event.

### i18n — a real cost, budgeted

`gettext` is not installed on this machine, so `.mo` files must be compiled with
`polib.save_as_mofile`, and a malformed `.mo` returns 500 on **every** DE/FR
request in production.

**The mitigation is to reuse the existing msgids verbatim.** Every string the
migrated call sites need already exists in `crush_lu/locale/{de,fr}/LC_MESSAGES/django.po`
with translations, because they are the strings `event_register` and
`event_detail.html` emit today. If `Reason.message` uses the identical msgid,
**PRs 1–3 need zero `.po` work.** Only genuinely new strings — the dashboard
section and the list badges — need catalog entries.

Those go through a script under `scripts/i18n/`, following
`add_phone_step_translations.py`: a module-level `NEW = {msgid: {"de": …, "fr": …}}`
dict, then `po.save(path)` and `po.save_as_mofile(path.replace(".po", ".mo"))`,
DE and FR only (EN is the source language). Two `test_i18n.py` invariants must
hold afterwards: every `.mo` declares `charset=UTF-8` (line 1165) and no fuzzy
entry reaches the compiled catalog (line 1187).

---

## 4. Migrating the existing call sites

### 4.1 `event_register` — `views_events.py:846-994`

The five-branch ladder with its ten `try/except CrushProfile.DoesNotExist` blocks
collapses to:

```python
    if event.is_private_invitation:
        ...                                    # unchanged, lines 804-845
    else:
        profile = CrushProfile.objects.filter(user=request.user).first()

    # `check_requirement` is False on the private path: a private event keeps a
    # dormant `profile_requirement` value that must NOT become live. Age and
    # language still run for private events today, so they run here too.
    verdict = eligibility.evaluate(
        request.user, event, profile=profile, now=now,
        check_requirement=not event.is_private_invitation,
    )
    if not verdict.eligible:
        messages.error(request, verdict.denial.message)
        if verdict.denial.redirect_url_name == "crush_lu:event_detail":
            return redirect("crush_lu:event_detail", event_id=event.id)
        return redirect(verdict.denial.redirect_url_name)
```

> ⚠️ **The `check_requirement` flag is load-bearing, not decoration.** Today the
> ladder sits inside the `else` of `if event.is_private_invitation` (846), while
> age (996) and language (1021) run *after* the whole `if/else` and therefore
> apply to both paths. Calling `evaluate()` unconditionally with the requirement
> axis enabled would newly enforce the dormant `profile_requirement` on private
> events — an invited guest on a private event whose preserved value is the
> default `completed` would suddenly be refused. The admin deliberately keeps
> that value dormant (`admin/events.py:151-153`). An earlier draft of this plan
> had exactly that bug: §8 said "never call `evaluate()` on the private path"
> while this snippet did. The flag makes the two agree.

**~150 lines → ~8.** The private-invitation branch (804–845) is untouched — it is
mutually exclusive with the ladder and has its own semantics. The age (996–1019)
and language (1021–1026) blocks move *into* `evaluate()`, keeping their order and
their exact messages. The duplicate-registration and `is_registration_accepting`
checks stay in the view: the former needs `request.user`, and the latter's
deliberate silence (no flash, comment at 1037–1038) is view policy, not
eligibility.

The redirect asymmetry is carried on `Reason.redirect_url_name`: reasons whose fix
is "create a profile" carry `"crush_lu:create_profile"`; the rest keep the default
`"crush_lu:event_detail"`. That field is **not** `unlock_url_name` — the gate's
redirect and the card's advisory unlock are deliberately separate (§3), so adding
an unlock to a reason never moves where a failed POST lands. Names, not paths —
see the i18n warning in §3.

### 4.2 `event_detail.html:522-849`

The 328-line ladder collapses to roughly 12 lines — the `{% if %}` shown in §3.
The explanatory boxes become one partial,
`crush_lu/templates/crush_lu/components/eligibility_notice.html` (~60 lines), which
renders `verdict.denial.message` and, when present, the unlock button. That
partial is then reused by the dashboard and the list card, so the three surfaces
cannot disagree.

**Net removal from `event_detail.html`: ~250 lines** (328 replaced by ~12, plus a
new shared 60-line partial that serves three call sites).

`event_detail` (`views_events.py:485-703`) gains one context key,
`eligibility`, computed from the profile it already loads at line 512. This also
retires the `is_approved` / `verification_status` split noted in §0, because the
template stops reading profile fields entirely.

**Style constraints.** `event_detail.html` is **not** in the exempt list of
`crush_lu/scripts/lint_design_tokens.py`, so the new partial must avoid hardcoded
brand hexes (`#7c3aed`, `#4f46e5`, `#6366f1`). Per `crush_lu/STYLE.md`: exactly
one `.btn-crush-primary` per page (that is the register CTA), so unlock buttons
use `.btn-crush-solid` or `.btn-crush-outline`. Critically, `.btn-crush-outline`
is **unlayered CSS** and beats all layered CSS regardless of specificity — so
padding utilities on it are silently inert. Size it with `.btn-sm` / `.btn-lg`,
never `px-*` / `py-*`. Follow the naming convention:
`crush_lu/templates/crush_lu/components/<thing>.html`, no leading underscore,
`{% load i18n %}` at the top.

### 4.3 `views_coach.py:2713-2839` — the SMS invite pool

This is the copy the brief missed, and the one that has actually drifted. It
becomes:

```python
profile_pool_qs = (
    CrushProfile.objects.filter(eligibility.profile_pool_q(event.profile_requirement))
    .filter(phone_q)        # SMS-specific: you cannot text an unverified phone
    .filter(age_filter)
    .filter(strict_lang_q)  # matches user_meets_language_requirement
    .select_related("user")
)
```

The `phone_q` requirement, the `ProfileSubmission` pools and the already-invited
exclusion stay local — those are legitimately invite-specific, not drift. The pool
is `eligibility ∧ contactable ∧ not-already-asked`, and only the first factor is
shared.

> ⚠️ **The `unverified` branch renders TWO pools and the split must survive.**
> This is the one place the swap is not a drop-in. Today `unverified` builds
> `pending_submissions_qs` (profiles whose latest `ProfileSubmission` is
> `pending`/`recontact_coach`) *and* `profile_pool_qs`, and it keeps them
> disjoint with the `latest_submission_status` annotation — the profile pool is
> filtered to `latest_submission_status IS NULL OR = "expired"`
> (`views_coach.py:2751-2769`), and the submission pool excludes
> `profile_latest_status="expired"` (`:2735-2736`). Both `Subquery` annotations
> exist purely to prevent one member appearing in both lists.
>
> `profile_pool_q("unverified")` is deliberately broader — it is the *eligibility*
> predicate, `verification_status NOT IN (verified, rejected)`, and knows nothing
> about submissions. Substituting it without re-applying the no-submission/
> expired-latest filter would list and count the same member twice, and coaches
> would send them **two invite SMS**. Keep the annotation, or exclude the
> submission pool's profile ids explicitly:
>
> ```python
> profile_pool_qs = profile_pool_qs.exclude(
>     pk__in=pending_submissions_qs.values("profile_id")
> )
> ```
>
> PR5 must carry a test asserting the two pools are disjoint for `unverified` —
> a plain count check on the rendered page is enough to catch a regression.

> ⚠️ **`strict_lang_q` is the one part of this pool the equivalence test does not
> cover, and §2 forbids it in SQL.** §2 keeps language in Python precisely because
> `event_languages` is a `JSONField` and containment semantics differ between
> SQLite (CI) and PostgreSQL (production). But the pool inverts the direction — it
> filters *profiles* for a fixed event — so it cannot avoid touching that JSON
> column, and the existing code already does (`views_coach.py:2704-2705`,
> `event_languages__contains`).
>
> Be honest about what this means: `profile_pool_q()` is covered by the exhaustive
> equivalence test, `strict_lang_q` is **not**. Two options, and PR5 must pick one
> explicitly rather than leave it implicit:
> 1. Apply the language filter in **Python** over the already-narrowed pool. The
>    pool is small (contactable profiles for one event), so the cost is trivial and
>    it reuses `user_meets_language_requirement` verbatim — no second
>    implementation, no backend risk.
> 2. Keep it in SQL and add **PostgreSQL-specific** coverage for the language
>    filter alone, asserting it agrees with `user_meets_language_requirement` for
>    profiles with empty, missing, partial and full language overlap.
>
> Option 1 is the better trade. Option 2 keeps a JSON `Q` whose correctness CI
> cannot check, which is the exact failure mode §2 exists to avoid.

> ⚠️ **The `ProfileSubmission` pool has its own age drift — a *second* live
> defect.** `sub_age_q` (`views_coach.py:2694-2696`) is
> `Q(profile__date_of_birth__gt=min_dob, profile__date_of_birth__lte=max_dob) |
> Q(profile__date_of_birth__isnull=True)` — it deliberately **admits profiles with
> no date of birth**. But `event_register` refuses a missing DOB outright on an
> age-restricted event: `profile is None or profile.age is None` → error →
> `create_profile` (`views_events.py:1001-1009`).
>
> So for an age-restricted `unverified` event, coaches are texting invites to
> members with no DOB, and every one of those bounces at the age gate. Same
> failure as the language drift below, same cost — real SMS spend on invites the
> gate will refuse. PR5 must apply the strict age filter to the submission rows
> whenever the event is actually age-restricted (`min_age > 18 or max_age < 99`),
> keeping the `isnull=True` leg only for unrestricted events, where the gate does
> not age-check at all.

**This fixes one genuine drift.** The `unverified` and `none` branches currently
apply `lang_q | Q(event_languages=[]) | Q(event_languages__isnull=True)` (lines
2770–2773, 2834–2837), i.e. they invite members with *no* declared languages. But
`user_meets_language_requirement` returns `False` for a profile with no
`event_languages`, so `event_register` refuses exactly those people. The other
four branches already use strict matching and comment that this is why
(lines 2789–2791). Coaches are currently texting people the gate will bounce.

It also retires the `unverified` branch's reliance on `is_approved=False`
(line 2759) in favour of `verification_status`, matching the gate and the model's
own note that `is_approved` is legacy.

### 4.4 `admin/events.py:66-112`

Keeps its own `AUDIENCE_DESCRIPTIONS` — organizer-facing copy is a different
vocabulary from member-facing denial messages, and conflating them would make both
worse. But it imports the **code list** from the service, so a seventh requirement
cannot be added in one place and forgotten in the other. `apply_registration_audience`
(lines 145–162) already validates against
`dict(MeetupEvent.PROFILE_REQUIREMENT_CHOICES)`; that stays.

---

## 5. The new dashboard section, and the list page

### Dashboard

`dashboard` (`views.py:344-616`) is wrapped in
`try: … except CrushProfile.DoesNotExist: redirect(create_profile)`, so **the
profile is never `None` here** — the S0 case cannot arise on this surface.

The view already scans a bounded upcoming window at lines 549–557 to compute
`next_event`. That window is what the section builds on.

> **Why this section states constraints instead of worked code.** Three successive
> review rounds found defects in this section's illustrative snippet — and only in
> this section, plus §3's samples. §0's corrections, §2's set-collapse and
> PR1–PR3 have gone untouched throughout, because those describe code that
> *exists* and reviewers can check against it. A snippet for a view nobody has
> written yet has no compiler, no tests, and no ground truth, so each fix invites
> the next round of critique without converging. Every finding is captured below
> as a requirement PR4 must satisfy; the code gets written against real tests when
> PR4 is scoped. Pretending otherwise wastes review capacity that PR1–PR3 need.

**Requirements PR4 must satisfy.** Each one is here because a review round caught
the snippet violating it:

1. **Scan a bounded window, and bound it in SQL.** `_filter_private_events`
   (`views_events.py:147-172`) is a list comprehension — it **materialises whatever
   it is handed**. Passing an unsliced queryset evaluates every future published
   event before any limit applies, so the "constant-size query" property is lost
   exactly when the catalogue grows. Apply the SQL slice (or chunk) *before*
   materialising, while still fetching enough rows to fill the section.
2. **Filter ended events before they consume the scan window.** The candidate
   filter uses `date_time__gte=live_lookback_cutoff(_now)`, a seven-day lookback
   that deliberately retains recently-ended events. The existing code follows it
   with a Python `e.end_time >= now` pass (`views_events.py:191`, `views.py:473`);
   omit that and a week of finished events can fill the scan limit and render an
   empty state while joinable events sit just past it.
3. **Exclude private-invitation events** via `_filter_private_events`, as
   `event_list` does at `views_events.py:216-217`. Otherwise a published private
   event is evaluated as ordinary: a Register CTA for a non-invitee the gate
   refuses at line 813, or an irrelevant unlock card for an invitee whose path
   bypasses the requirement entirely (§4.1).
4. **Preload registrations into a dict** and pass them to `evaluate()`, so
   `availability` can reach `"registered"` without a query per event. An
   already-registered member must get the ticket/details treatment, not a second
   Register CTA.
5. **Filter on `verdict.can_register or verdict.one_step_away`**, never on the
   verdict object — `Eligibility` has no `__bool__`, so a bare truthiness test
   passes everything through, including non-actionable denials the omission rule
   below says to drop.
6. **Respect gender-pool capacity before promising a CTA — but annotate it, do
   not call the model method per event.** `with_registration_counts()` annotates
   *total* confirmed/waitlist only, while `event_register` waitlists a member
   whose **gender pool** is full even when total capacity remains
   (`views_events.py:1146-1159`, `gender_pool_full and not total_full`). So
   availability must account for the member's pool where `gender_limits_active`.
   ⚠️ `is_gender_pool_full(user_gender)` calls `get_confirmed_count_for_gender()`,
   which **issues a query**: calling it inside the loop is an N+1 the moment two
   gender-capped events are scanned, contradicting requirement 7. Annotate the
   member's pool count across the bounded window in the same query that fetches
   the events (the member's gender is fixed, so it is one conditional aggregate,
   not one per event).
7. **Two queries, both constant** — the events window (carrying both the total and
   the member's gender-pool counts) and one registration lookup. No per-event
   work. If requirement 6 cannot be met with an annotation, relax this budget
   *explicitly* rather than letting a per-event query hide behind a stale
   guarantee.

For reference, the shape (illustrative only — PR4 owns the real thing):

```python
shown = []
for event in candidates:          # bounded + private-filtered + ended-filtered
    verdict = eligibility.evaluate(
        request.user, event, profile=profile, now=_now,
        registration=registered.get(event.id),
    )
    if verdict.can_register or verdict.one_step_away:
        shown.append((event, verdict))
    if len(shown) >= UPCOMING_SHOWN:
        break
```

**Two queries, both constant** — the events window and one registration lookup.
No per-event work.

> ⚠️ **Do NOT apply `eligible_events_q()` here.** This is the subtlest trap in
> the plan, and an earlier draft fell into it. `eligible_events_q()` narrows to
> requirements the member **already satisfies** — so it excludes, by construction,
> exactly the *one-step-away* events this section exists to surface. An incomplete
> member would never fetch a `completed` event, and the planned "Complete your
> profile" card could never render at all. Fetch the broader upcoming window and
> let `evaluate()` do the classifying.
>
> **This applies to the list page too** — neither member-facing surface may use it
> as a pre-filter, because both show near-misses.
>
> **`eligible_events_q()` is a SQL *narrowing*, never a sufficient answer.** It
> evaluates only the indexable axes; by design it does **not** evaluate language
> (Python-only, §2), registration state, or availability. So even a caller that
> genuinely wants joinable-events-only — a digest email, an API, a count — must
> still run `evaluate()` over the narrowed rows before emitting anything
> member-visible, or it will include language-ineligible events, events the member
> is already registered for, and events past their deadline. Treat the helper as
> "cheaply discard the definitely-ineligible", not as "these are the joinable
> events".

The section slots in after `dashboard.html:161` — below "Your next event"
(section 5), above the counts grid (section 6) — reusing the
`status-card rounded-2xl` shell with `status-card-body p-4 sm:p-5`, matching the
neighbouring sections.

Four render states:

| State | Renders |
|---|---|
| **Eligible** | Event card + `.btn-crush-solid` "Register" via `{% url 'crush_lu:event_register' event.id %}`. Waitlist events say "Join Waitlist". A member already registered has `availability == "registered"` and gets the existing ticket/details treatment, **not** a second Register CTA. |
| **One-step-away** (`verdict.one_step_away`) | Event card + the **single** unlock CTA from `Reason.unlock_label`/`unlock_url_name` — "Complete your profile", "Get verified". One action, never a list. |
| **Not eligible at all** (denial with no unlock) | **Omitted from the section.** A rejected profile, or an "unverified members only" event a verified member cannot join, is not actionable — showing it would be the dashboard lecturing. This is deliberate and is the main place the dashboard differs from `event_detail`, which must explain because the member navigated there on purpose. |
| **Nothing upcoming** | The existing empty-state pattern used elsewhere on the page. |

### `event_list.html` / `includes/event_card.html`

Each card gains an eligibility state, so a member learns before the tap. NN/g's
error-prevention-over-error-messaging finding is the argument; Baymard's
abandonment data is the cost of not doing it.

`event_list` (`views_events.py:175-376`) currently puts **no** `user_profile` in
its context — yet `event_card.html:54` and `:98` already reference it, so it
silently resolves falsy for everyone and the card shows canton instead of the full
address. That is a pre-existing latent bug; fix it in the same PR since the card
is being touched anyway.

> ⚠️ **The list page must NOT pre-filter with `eligible_events_q()` either.** Same
> trap as the dashboard: it narrows to requirements the member already satisfies,
> so an incomplete member would never *fetch* a `completed` event and could never
> see the "Complete your profile" badge that is the entire point of this change.
> The list keeps its existing visibility query (published, not cancelled, upcoming,
> `_filter_private_events`) and `evaluate()` **annotates** each card rather than
> deciding which cards exist. A catalogue page that hides events is a worse product
> than one that labels them — and hiding them would also be a member-visible
> regression, since today every published event is listed.

This is the highest-traffic page and the most member-visible change, so it ships
last (§7).

---

## 6. Test strategy

`test_profile_requirement_gates.py` becomes the **shared specification** by
keeping `STATES` (line 47) and `EXPECTED` (line 58) exactly as they are and
driving four consumers off the same tables.

1. **`test_gate_matrix` — unchanged.** Still 42 real POSTs through
   `event_register`, still asserting on an `EventRegistration` row existing. This
   is the contract; if the refactor is correct it does not move.

2. **Pure predicate, no database.**
   `EXPECTED[req] == {s for s in STATES if req in allowed_requirements(fake_profile(s))}`
   over all six requirements. 42 assertions, microseconds, no fixtures.

3. **Queryset/predicate equivalence — exhaustive.** Create all seven states, then
   for each requirement assert set equality between the two representations:

   ```python
   assert set(CrushProfile.objects.filter(profile_pool_q(req))) == {
       p for p in all_profiles if req in allowed_requirements(p)
   }
   ```

   **This test must also be run against PostgreSQL before merging PR1 and PR5**,
   because CI is SQLite and the research documents lookups that differ silently
   between the two. The `exact`/`in`/`isnull`-only vocabulary from §2 is what makes
   the SQLite run meaningful; the Postgres run is what confirms it.

   > ⚠️ **`STATES` is not the full cross product — extend it for *this* test only.**
   > `allowed_requirements()` reads three independent inputs
   > (`verification_status`, `phone_verified`, `assigned_coach_id`), but `STATES`
   > carries a coach on only two rows, both `verified` or `rejected`. There is no
   > `incomplete + coach` or `pending + coach` fixture — yet the gate admits **any**
   > non-rejected profile with a coach under `coach_assigned`. So a regression that
   > made `profile_pool_q("coach_assigned")` require `verification_status="verified"`
   > would pass all 42 cells untouched. Calling that matrix "total enumeration"
   > overstates it: it is exhaustive over `STATES`, not over the input space.
   >
   > The fix is **not** to edit `STATES`/`EXPECTED` — those are the audited spec and
   > must stay byte-identical (§7, PR1). Build the equivalence test on a separate,
   > wider fixture set: the cross product of the four `verification_status` values ×
   > `phone_verified` ∈ {T, F} × `has_coach` ∈ {T, F} = **16 profiles**.
   >
   > ⚠️ **But do not derive that test's expectations from `allowed_requirements()`.**
   > Doing so only proves `profile_pool_q()` agrees with the service — it says
   > nothing about whether the service agrees with the **gate**, which is the thing
   > the audit actually pinned. A PR2 regression that dropped `incomplete + coach`
   > from `allowed_requirements()` would change production behaviour (the gate
   > admits any non-rejected profile with a coach, `views_events.py:907-924`) and
   > still pass a self-derived matrix, because the expectation would have moved with
   > the bug. That is a tautology, not a test.
   >
   > So the 16-profile product needs **two** assertions, not one:
   > 1. `profile_pool_q(req)` ⟺ `req in allowed_requirements(p)` — the two
   >    representations agree (self-derived is fine *here*; both sides are the thing
   >    under test).
   > 2. `req in allowed_requirements(p)` ⟺ a real POST to `event_register`
   >    creates a row — the service agrees with the gate. This one must drive the
   >    view, exactly as `test_gate_matrix` does, and it is what catches the coach
   >    regression. Reuse `_try_register()`.
   >
   > Assertion 2 is the load-bearing one and the only one that would have caught the
   > original audit's defects. Budget for the rate-limit fixture: 96 POSTs at
   > `5/h` per user needs the same `cache.clear()` the existing suite uses.

4. **Rendered-page assertions — non-negotiable.** This repo has been bitten
   *precisely* here: the audit spec exists because the gate was fixed and the page
   was not, and the file's own comment at lines 223–228 says so —
   *"event detail is the ONLY normal entry point to it, so a page that hides the
   CTA keeps a lockout alive with a green backend"*.

   The existing `_detail_has_register_cta()` (line 231) already does a real
   `client.get` and checks for the literal `/events/<id>/register/` in the decoded
   body. Extend that idiom to the **dashboard** and **event_list**, asserting both
   CTA presence/absence and the unlock link's target per state.

   Follow `test_dashboard_event_status.py` for those: it decorates the class with
   `@override_settings(ROOT_URLCONF="azureproject.urls_crush")` (line 71) **and**
   passes `HTTP_HOST="crush.lu"` on each request (line 78), and it has a
   scoped-regex helper at lines 32–43 whose comment explains that a page-wide
   `assertContains` would be a false pass. Scope the assertion to the new section, or a string appearing
   elsewhere on the dashboard will pass the test for the wrong reason.

   Reusable fixtures: `_make_member` / `_make_coach` in
   `test_profile_edit_connect_card.py:27,55` — already imported cross-file by both
   dashboard test modules.

**Constraints on any new test.** `pytest.ini` runs `-x` (stops at first failure)
and `-n auto --dist worksteal`. The file's autouse `_reset_ratelimit` fixture
exists because `event_register` is `@ratelimit(key="user", rate="5/h")` and
rolled-back user pks restart at 1, so the cached counter collides across tests and
429s get read as rejections — any new POST-driving test needs the same
`cache.clear()`. And any helper building profile states must set the verification
fields via `CrushProfile.objects.filter(pk=…).update(…)`, because `save()` syncs
`is_approved → verification_status` and would clobber `"rejected"` (lines 134–138).

---

## 7. PR staging

Smallest first. Each leaves the app working and independently reviewable.

**PR1 — add the service and its tests. No call site changes.**
Pure addition: `crush_lu/services/eligibility.py` plus the predicate and
equivalence tests. Proves the predicate against `EXPECTED` and proves the two
representations agree.
*Does NOT:* change any runtime behaviour, touch any view, template or `.po`.

**PR2 — migrate `event_register`.**
The ladder becomes the `evaluate()` call. Reuses existing msgids verbatim.
*Does NOT:* touch any template, add any string, or change any message text. Verified
by `test_gate_matrix` remaining green unchanged.

**PR3 — collapse `event_detail.html`; add `eligibility_notice.html`.**
~250 lines removed. `event_detail` gains one context key. Retires the
`is_approved` vs `verification_status` split.
*Does NOT:* add new user-facing strings, touch the dashboard, or change the gate.

**PR4 — the dashboard section.**
First new strings → polib script, DE + FR, both `test_i18n.py` catalog invariants
must pass.
*Does NOT:* touch the coach pool or the list page.

**PR5 — the coach invite pool.**
Behaviour-changing for coaches; isolated so it can be reverted alone. Run the
equivalence test against Postgres before merging.
*Does NOT:* change any member-facing surface.

**PR6 — `event_list` card badges.**
Highest-traffic page, most member-visible change, ships last. Also fixes the
missing `user_profile` context key.
*Does NOT:* change the gate or any denial message.

Note for CI: required checks in this repo are path-filtered, so a CSS- or
docs-only PR can read as BLOCKED. Check `mergeStateStatus`, not `mergeable`.

---

## 8. Risks

**Member-visible — the list page starts refusing before the tap (PR6).**
Intended, and the point of the exercise, but it converts an implicit "maybe" into
an explicit "no" on the busiest page. Mitigate by preferring the unlock CTA over a
bare refusal wherever a `Reason` carries one — Eventbrite's audience-restriction
denial, which names no criterion, is the anti-pattern to avoid. Caught by the PR6
rendered-page tests.

**Member-visible via SMS — PR5 changes who gets invited.**
The language fix removes people the gate would have refused anyway, so it is
strictly fewer wasted texts, but it is a real change to a coach-facing tool.
Isolated in its own PR and revertible alone. Worth telling the coaches.

**SQLite/PostgreSQL blindness — the largest technical risk.**
CI runs on SQLite; production is Postgres. This repo already has the scar:
`select_for_update` is a silent no-op on SQLite, so lock-ordering bugs pass every
CI run and fail only on production Postgres. Mitigation is structural (the
restricted `Q` vocabulary in §2) plus procedural (run the equivalence test with
`DBHOST` set before merging PR1 and PR5).

**A silently over-narrowing filter.**
If `eligible_events_q` ever excludes an event the Python predicate would admit,
the dashboard shows *fewer* events with no error anywhere — a failure that looks
like "quiet week". Mitigation: the Python predicate is authoritative and the SQL
is only a narrowing over indexable axes; the equivalence test pins them together.

**A malformed `.mo` returns 500 on every DE/FR request in production.**
Only PR4 and PR6 add strings. Both `test_i18n.py` catalog invariants (UTF-8
declared, no fuzzy entries compiled) must pass before merge. Note
`test_profile_requirement_gates.py:377-404` already asserts specific DE/FR admin
labels, so it will catch a broken catalog too.

**Rate limiting.**
`event_register` stays `@ratelimit(key="user", rate="5/h", method="POST")`.
Nothing here changes it, but any new test that POSTs needs the same `cache.clear()`
the existing autouse fixture does, or it will see 429s and read them as denials.

**Private-invitation events.**
`is_private_invitation` short-circuits before the ladder and never consults
`profile_requirement`. `evaluate()` must be called with `check_requirement=False`
on that path (§4.1), or a dormant requirement value would suddenly become live and
refuse invited guests. The admin preserves that dormant value deliberately
(`admin/events.py:151-153`). Age and language still apply, as they do today.

**Anonymous visitors on public pages.**
`event_detail` and `event_list` have no login decorator, so `profile=None` there
can mean "anonymous", not S0. Without the `user.is_authenticated` check in
`evaluate()` (§2), an open `none` event would show a Register CTA to a logged-out
visitor in place of the current login prompt. Caught by adding an anonymous case
to the rendered-page tests in §6.

**A duplicate Register CTA for already-registered members.**
`availability` can only reach `"registered"` if the member's registration state is
passed into `evaluate()`. Preload it as a dict (§3); querying per event would
reintroduce the N+1 the design exists to avoid.

**Double-listing in the coach invite pool (PR5).**
The `unverified` branch renders two pools that are kept disjoint by submission-status
annotations. Dropping those in favour of the shared predicate would list a member
twice and send two invite SMS — real money. PR5 carries a disjointness test (§4.3).

**Private events leaking onto the dashboard.**
The dashboard's candidate query must go through `_filter_private_events`
(`views_events.py:147`) as `event_list` already does, or a published
private-invitation event is evaluated as ordinary — offering a Register CTA to
non-invitees and an irrelevant unlock card to invitees (§5).

**An unlock CTA that unlocks nothing.**
`one_step_away` now requires `availability in ("open", "waitlist")`. Without that,
a member failing a fixable requirement on an event whose deadline has passed is
told to "Complete your profile" for an event they still could not join.

**Losing the "Registration is closed" box (PR3).**
`eligible` and `bookable` are separate axes, so `not can_register` does not imply a
denial. An eligible member on a closed event has `denial=None`; a two-branch
template would render nothing where today's ladder shows a closed notice in five
places. Needs three branches and an availability partial (§3). Caught by a
rendered-page test on a past-deadline event.

**PR2 silently changing where failed registrations land.**
`redirect_url_name` is separate from `unlock_url_name` precisely so the gate keeps
today's targets while cards get advisory unlocks. Note the gate matrix asserts on
**row creation, not redirect targets**, so it would not catch this — PR2 needs an
explicit redirect-target assertion per denial reason.

**A Register CTA for a member the gender pool will waitlist.**
`with_registration_counts()` annotates totals only, but the gate waitlists on a
full *gender* pool even when total capacity remains. Availability must consult
`is_gender_pool_full()` where `gender_limits_active`, or the new surfaces
re-create the exact mismatch this plan exists to remove.

**A self-derived equivalence test that proves nothing.**
If the 16-profile matrix takes its expectations from `allowed_requirements()`, a
regression moves the expectation with it. Assertion 2 in §6 — driving real POSTs
through `event_register` — is the one that actually pins the service to the gate.

**Untranslated placeholders in denial messages.**
The age and language reasons interpolate event data, so they must be built inside
`evaluate()`, not frozen as module constants — otherwise members see literal
`%(min)d` or lose the language names the current gate shows (§3).

**Losing `next` on the login unlock.**
`event_detail.html:859` carries `?next={{ request.path }}` today. A login unlock
that drops it strands the member on the default post-login page instead of the
event they were trying to join. `Reason.unlock_query` preserves it.

**An unknown `profile_requirement` silently meaning "everyone".**
`profile_pool_q()` raises on unrecognised codes rather than falling through to
`Q()`. Without that, adding a seventh choice to the model and forgetting this
function would widen the SMS pool to every contactable profile while the
per-object check denies them all.

---

## Verification

```bash
pytest crush_lu/tests/test_profile_requirement_gates.py -v
```

Must stay green **unchanged** at every PR — it is the contract.

```bash
pytest crush_lu/tests/test_dashboard_event_status.py crush_lu/tests/test_events.py
```

For the rendered surfaces.

Local SQLite runs need `DBHOST=''` and `-n 0`.

### Running against PostgreSQL (verified 2026-08-05)

The equivalence test must additionally run against Postgres before PR1 and PR5
merge — CI is SQLite-only, so that run is the only thing standing between a
backend-divergent lookup and production.

There is a local Postgres in Docker (`entreprinder-postgres`, `postgres:15`, on
`localhost:5432`, `DBNAME=entreprinder`). pytest-django creates and drops
`test_entreprinder`, so the dev database is untouched.

**Two gotchas make the obvious command fail**, both specific to running from a
git worktree:

1. `settings.py:29` sets `DOTENV_PATH = BASE_DIR / ".env"`, and `BASE_DIR`
   resolves to the **worktree**, which has no `.env`. Nothing is loaded, so
   `SECRET_KEY` is unset and settings raise `ImproperlyConfigured` before pytest
   collects anything. Passing `DBHOST=... pytest` alone is not enough.
2. There is no `.venv` in the worktree either; the root repo's works, since only
   dependencies come from it.

So load the root `.env` into the process first:

```bash
cd /c/GitHub/Multi-Domain-Django-Platform/.claude/worktrees/<worktree> && "C:/GitHub/Multi-Domain-Django-Platform/.venv/Scripts/python.exe" -c "import sys; from dotenv import load_dotenv; load_dotenv(dotenv_path='C:/GitHub/Multi-Domain-Django-Platform/.env'); import pytest; sys.exit(pytest.main(['crush_lu/tests/test_profile_requirement_gates.py','-v','-n','0','--tb=short']))"
```

**Baseline confirmed 2026-08-05: 70 passed in 11.79s** against Postgres 15
(Django 6.0.7, Python 3.14.6). The spec is green on both backends *today*, so any
divergence a later PR introduces is attributable to that PR. The run is fast
enough (~12s with `--reuse-db`) that there is no excuse for skipping it.

Then a browser walk on `localhost:8000` (CSRF origins are hardcoded to that port)
across event detail, dashboard and event list, as each of S1_incomplete,
S3_pending_phone, S4_verified, S6_verified_coach and S7_rejected — the five states
where the four render states differ.
