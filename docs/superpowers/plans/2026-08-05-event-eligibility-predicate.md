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
| The rule is implemented **3×** | **4×**. The third copy is not a template — it is Python: `crush_lu/views_coach.py:2713-2839` rebuilds all six branches as **querysets** to pick SMS invitees. `crush_lu/admin/events.py:71-111` holds a fourth copy of the semantics as organizer-facing labels. |
| `coach_event_sms_invite.html:156` is a partial copy of the ladder | It is **9 lines** (156–164) of descriptive header text, with no `profile_exists` or `completed` case and no CTA. Harmless; not a gate. |
| `event_detail.html` ladder is ~250 lines from line 522 | Start line correct. It is **328 lines, 522–849**, nested inside a larger action area spanning 362–872. |
| Template context uses `can_register` / `is_registered` | Neither name exists. The real context keys are `user_profile`, `user_registration`, `event_full_for_user`, `premium_reserved_seat_available`, `language_requirement_met`, plus the model property `event.is_registration_accepting`. |
| `CLAUDE.md` at repo root | Does not exist. The repo's convention doc is **`AGENTS.md`**. |
| Fields `profile_completed` / `is_participation_ready` | Do not exist anywhere in the repo. "Participation-ready" is only the *display label* for `profile_requirement="completed"`; the gate's actual test is the inline expression at `views_events.py:858-860`. |
| Tests must use literal paths because `reverse("crush_lu:…")` 404s under `HTTP_HOST=crush.lu` | `test_profile_requirement_gates.py` uses **`reverse()`** and sends **no** `HTTP_HOST`. It sets `pytestmark = [..., pytest.mark.urls("azureproject.urls_crush")]` (line 44), which swaps the URLconf directly and bypasses host routing. Both idioms exist in the suite; see §6. |
| `crush_lu/tests/conftest.py` | Does not exist. Only the repo-root `conftest.py` applies to these tests. |
| Capacity is a gate | It is **not**. Capacity never refuses a registration — it downgrades `status` to `"waitlist"` (`views_events.py:1146-1172`). The only hard stop after the requirement/age/language checks is `is_registration_accepting`. |
| `docs/plans/` | Does not exist. Repo precedent is `docs/superpowers/plans/` beside `docs/superpowers/specs/`, which is where this file lives. |

**One more finding the brief did not have.** `event_detail.html` branches on
`user_profile.is_approved` (lines 539, 644, 729, 777) while `event_register`
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
| 1053–1245 | POST: `select_for_update`, re-check, capacity → waitlist, payment status |

Note the redirect asymmetry: a **missing** profile always redirects to
`create_profile`; a **wrong-state** profile redirects to `event_detail` — except
in the `completed` branch, which sends it to `create_profile`. This is
load-bearing and must survive the refactor.

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

### The other axes

`profile_requirement` is not the only gate, and treating it as one would advertise
events the member is too young for. Each remaining axis gets the treatment its
semantics allow:

| Axis | Where evaluated | Why |
|---|---|---|
| **Age** | SQL — `Q(min_age__lte=age) & Q(max_age__gte=age)` | `min_age`/`max_age` are non-nullable ints with defaults 18/99. Integer comparison is backend-neutral. If `profile.age is None`, narrow to unrestricted events only (`min_age__lte=18, max_age__gte=99`), mirroring the gate's refusal. |
| **Deadline / published / cancelled** | SQL, with **`now` passed in from Python** | Never use `Now()` in the filter. Clock skew between the app and the database is a documented divergence; one `now` value feeds both halves. |
| **Language** | **Python only** | `MeetupEvent.languages` is a `JSONField`. `__contains` semantics on JSON differ across backends, and this is exactly the trap. Reuse the existing `event.user_meets_language_requirement(user)` (`models/events.py:654-686`), which already returns `(bool, message)` — so it already produces our denial message for free. |
| **Capacity / waitlist** | Python, off `with_registration_counts()` | This is *availability*, not eligibility — and capacity never refuses, it waitlists (`views_events.py:1146-1172`). |

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
2. **Prove equivalence exhaustively.** The state space is finite and tiny —
   7 states × 6 requirements = 42 cells. This is not property-based sampling; it
   is a total enumeration, which is strictly stronger.

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
    return Q()  # "none"
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
    message: str                       # gettext_lazy
    unlock_label: str | None = None
    unlock_path: str | None = None     # literal path, e.g. "/profile/create/"

    @property
    def has_unlock(self) -> bool:
        return bool(self.unlock_path)


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
        return not self.eligible and self.denial is not None and self.denial.has_unlock
```

Entry points:

```python
def allowed_requirements(profile) -> frozenset[str]: ...
def evaluate(user, event, *, profile, now, registration=None) -> Eligibility: ...
def eligible_events_q(profile, *, now) -> Q: ...       # events side (member fixed)
def profile_pool_q(requirement: str) -> Q: ...         # profiles side (event fixed)
```

`evaluate()` runs the axes in the gate's existing order — requirement, age,
language — and returns on the first failure, so the reported reason matches the
message `event_register` produces today.

Reasons are module-level constants. `views_events.py` already imports
`gettext_lazy as _` (line 4), so lifting the existing strings out of the view is
safe as-is.

### Caller: a view

```python
verdict = eligibility.evaluate(
    request.user, event, profile=profile, now=timezone.now()
)
if not verdict.eligible:
    messages.error(request, verdict.denial.message)
    return redirect(verdict.denial.unlock_path or f"/events/{event.id}/")
```

### Caller: a template

```django
{% if eligibility.can_register %}
  <a href="/events/{{ event.id }}/register/" class="btn-crush-primary btn-block btn-lg">
    {% if event_full_for_user %}{% trans "Join Waitlist" %}{% else %}{% trans "Register for This Event" %}{% endif %}
  </a>
{% else %}
  {% include "crush_lu/components/eligibility_notice.html" with verdict=eligibility %}
{% endif %}
```

Note the include parameter is named `verdict`, **not** `block` — Django binds a
truthy `BlockNode` under that name inside every `{% block %}`, so `{% if block %}`
is unconditionally true on any page extending a base template.

### Caller: a queryset

```python
now = timezone.now()
events = (
    MeetupEvent.objects.with_registration_counts()
    .filter(eligibility.eligible_events_q(profile, now=now))
    .order_by("date_time")[:12]
)
joinable = [e for e in events if eligibility.evaluate(
    user, e, profile=profile, now=now).can_register]
```

One profile load, one event query, then pure Python. No N+1.

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
    else:
        profile = CrushProfile.objects.filter(user=request.user).first()

    verdict = eligibility.evaluate(request.user, event, profile=profile, now=now)
    if not verdict.eligible:
        messages.error(request, verdict.denial.message)
        return redirect(verdict.denial.unlock_path or f"/events/{event.id}/")
```

**~150 lines → ~8.** The private-invitation branch (804–845) is untouched — it is
mutually exclusive with the ladder and has its own semantics. The age (996–1019)
and language (1021–1026) blocks move *into* `evaluate()`, keeping their order and
their exact messages. The duplicate-registration and `is_registration_accepting`
checks stay in the view: the former needs `request.user`, and the latter's
deliberate silence (no flash, comment at 1037–1038) is view policy, not
eligibility.

The redirect asymmetry is carried on `Reason.unlock_path`: reasons whose fix is
"create a profile" point at `/profile/create/`; the rest leave it `None`, and the
view falls back to the event detail path.

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

### 4.4 `admin/events.py:71-111`

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
`next_event`. That query gains the eligibility filter and a Python pass:

```python
upcoming = (
    MeetupEvent.objects.with_registration_counts()
    .filter(eligibility.eligible_events_q(profile, now=_now))
    .order_by("date_time")[:12]
)
joinable = [
    (e, v) for e in upcoming
    if (v := eligibility.evaluate(request.user, e, profile=profile, now=_now))
]
```

**No new queries beyond the one already there.**

The section slots in after `dashboard.html:161` — below "Your next event"
(section 5), above the counts grid (section 6) — reusing the
`status-card rounded-2xl` shell with `status-card-body p-4 sm:p-5`, matching the
neighbouring sections.

Four render states:

| State | Renders |
|---|---|
| **Eligible** | Event card + `.btn-crush-solid` "Register" linking to `/events/<id>/register/`. Waitlist events say "Join Waitlist". |
| **One-step-away** (`verdict.one_step_away`) | Event card + the **single** unlock CTA from `Reason.unlock_label`/`unlock_path` — "Complete your profile", "Get verified". One action, never a list. |
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

   Total enumeration of the finite space, not sampling. **This test must also be
   run against PostgreSQL before merging PR1 and PR5**, because CI is SQLite and
   the research documents lookups that differ silently between the two. The
   `exact`/`in`/`isnull`-only vocabulary from §2 is what makes the SQLite run
   meaningful; the Postgres run is what confirms it.

4. **Rendered-page assertions — non-negotiable.** This repo has been bitten
   *precisely* here: the audit spec exists because the gate was fixed and the page
   was not, and the file's own comment at lines 223–228 says so —
   *"event detail is the ONLY normal entry point to it, so a page that hides the
   CTA keeps a lockout alive with a green backend"*.

   The existing `_detail_has_register_cta()` (line 231) already does a real
   `client.get` and checks for the literal `/events/<id>/register/` in the decoded
   body. Extend that idiom to the **dashboard** and **event_list**, asserting both
   CTA presence/absence and the unlock link's target per state.

   Follow `test_dashboard_event_status.py` for those: it uses
   `@override_settings(ROOT_URLCONF="azureproject.urls_crush")` **and**
   `HTTP_HOST="crush.lu"` (line 78), and it has a scoped-regex helper at lines
   34–41 with a comment explaining that a page-wide `assertContains` would be a
   false pass. Scope the assertion to the new section, or a string appearing
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
`profile_requirement`. `evaluate()` must not be called on that path, or a dormant
requirement value would suddenly become live. The admin already preserves the
dormant value deliberately (`admin/events.py:151-153`).

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
