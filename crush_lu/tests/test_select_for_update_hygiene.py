"""
`select_for_update()` + `select_related()` must lock only the base table.

PostgreSQL refuses ``SELECT ... FOR UPDATE`` on the nullable side of an outer
join::

    ERROR:  FOR UPDATE cannot be applied to the nullable side of an outer join

``select_related()`` on a nullable ForeignKey produces exactly that outer
join, so pairing the two without ``of=("self",)`` raises at query time — on
production Postgres only. The test suite runs on SQLite, where
``select_for_update()`` is a documented no-op, so **every such query passes
locally and 500s in production**. No runtime test can catch this on SQLite;
this is a source check instead.

The codebase has been bitten before — see the comment in
``views_checkin.check_in_scan`` — and the "My Crush!" coach surfaces
reintroduced it in five places (fixed alongside this guard).

Nullable FKs are not distinguished from non-nullable ones on purpose:
"pass ``of=`` whenever you combine the two" is checkable by reading a single
expression, and locking just the base row is what these call sites want
anyway.

``KNOWN_OFFENDERS`` pins pre-existing call sites outside the crush-lead work.
Each was checked individually: they all `select_related` **non-nullable** FKs,
which produce inner joins that PostgreSQL locks without complaint — so they
are correct today, not broken. They are listed rather than fixed because
adding ``of=`` there is someone else's change to make, and listed rather than
exempted by a nullability check because the blanket rule is the cheap one to
enforce: it costs nothing to lock only the base row, and it stays right if one
of those FKs later becomes nullable. The list may shrink, never grow.

Run with: pytest crush_lu/tests/test_select_for_update_hygiene.py -v
"""
import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

# Pre-existing and currently safe (inner joins only) — see module docstring.
# Verified non-nullable: EventRegistration.user/.event,
# EventLobbyParticipation.event_registration, RemovalRequest.encounter.
KNOWN_OFFENDERS = {
    "api_quiz.py",
    "services/event_lobby.py",
    "views_checkin.py",
}

# `select_for_update(...)` followed, in the same chained expression, by
# `.select_related(...)`. The args group tolerates one level of nesting so
# `of=("self",)` is read as a whole argument rather than ending at its inner
# closing paren.
CHAIN = re.compile(
    r"select_for_update\((?P<args>(?:[^()]|\([^()]*\))*)\)"
    r"(?:\s*\.\s*\w+\((?:[^()]|\([^()]*\))*\))*?"
    r"\s*\.\s*select_related\(",
    re.DOTALL,
)


def _offenders():
    found = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = path.relative_to(APP_ROOT).as_posix()
        if rel.startswith("tests/") or "migrations/" in rel:
            continue
        source = path.read_text(encoding="utf-8")
        for match in CHAIN.finditer(source):
            if "of=" in match.group("args"):
                continue
            line = source[: match.start()].count("\n") + 1
            found.append((rel, f"{rel}:{line}"))
    return found


def test_locked_queries_with_select_related_lock_only_the_base_row():
    offenders = [loc for rel, loc in _offenders() if rel not in KNOWN_OFFENDERS]
    assert not offenders, (
        "select_for_update() combined with select_related() must pass "
        'of=("self",) — PostgreSQL cannot lock the nullable side of the outer '
        "join select_related() creates, and SQLite hides this in tests. "
        "Offenders: " + ", ".join(offenders)
    )


def test_the_known_offender_list_does_not_outlive_its_offenders():
    """A stale allowlist entry would quietly re-open the hole it pins."""
    still_offending = {rel for rel, _ in _offenders()}
    assert not (KNOWN_OFFENDERS - still_offending), (
        "These files no longer combine an unguarded select_for_update() with "
        "select_related() — drop them from KNOWN_OFFENDERS: "
        + ", ".join(sorted(KNOWN_OFFENDERS - still_offending))
    )


def test_the_guard_detects_a_violation():
    """The check only means something if it can fail — a regex that silently
    matches nothing would report a clean codebase forever."""
    sample = """
        obj = (
            Model.objects.select_for_update()
            .select_related("nullable_fk")
            .get(pk=1)
        )
    """
    match = CHAIN.search(sample)
    assert match is not None and "of=" not in match.group("args")


def test_the_guard_accepts_a_guarded_lock():
    sample = """
        obj = (
            Model.objects.select_for_update(of=("self",))
            .select_related("nullable_fk")
            .get(pk=1)
        )
    """
    match = CHAIN.search(sample)
    assert match is not None, "nested parens in of=(...) must not break parsing"
    assert "of=" in match.group("args")


def test_the_guard_accepts_extra_kwargs_alongside_of():
    sample = 'M.objects.select_for_update(skip_locked=True, of=("self",)).select_related("f")'
    match = CHAIN.search(sample)
    assert match is not None and "of=" in match.group("args")


def test_the_guard_ignores_a_bare_lock():
    """`select_for_update()` with no `select_related()` joins nothing, so
    there is no nullable side to refuse."""
    assert CHAIN.search("M.objects.select_for_update().get(pk=1)") is None
    assert CHAIN.search('M.objects.select_for_update().only("id").get(pk=1)') is None
