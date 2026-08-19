"""Shared fixtures for the ``crush_lu`` test package.

Everything here applies to modules under ``crush_lu/tests/`` only — see the
scoping guard in ``pytest_collection_modifyitems`` below.
"""

import pytest


def pytest_collection_modifyitems(session, config, items):
    """Attach the migration-catalogue restore fixture to any crush_lu module
    that flushes the DB.

    A module "flushes" when any of its collected items carries
    ``django_db(transaction=True)`` — as a whole-module ``pytestmark`` (the
    three playwright-gated modules: ``test_visual_regression.py``,
    ``test_mobile_ui.py``, ``test_coach_review_profile_playwright.py``) or as
    a single test's own marker (``test_crush_member_flow.py::
    test_concurrent_declarations_never_exceed_limit``, which needs real
    concurrency and can't run inside the rollback-wrapped default).
    ``TransactionTestCase``-style teardown truncates every table, which takes
    the data-migration-seeded catalogues (Interest, Trait, SparkPrompt,
    ConnectQuestion, …) with it. Django's own test runner sidesteps this by
    running ``TransactionTestCase``s last; pytest has no such ordering, so
    under ``-n auto --dist worksteal`` whichever module lands after a
    flushing one on the same worker inherits an empty catalogue — and with
    ``--reuse-db`` the damage outlives the run.

    This is a collection hook, not a blanket ``autouse`` fixture, on purpose:
    an autouse fixture at this scope would pull ``django_db_setup`` (and so
    real DB creation) into *every* module under ``crush_lu/tests/``,
    including pure-unit ones like ``test_asgi_static.py`` — exactly what the
    root ``conftest.py``'s own ``django_db_setup`` override goes out of its
    way to keep lazy (see its docstring). Tagging only the modules that
    actually flush keeps that invariant intact, and covers a newly added
    ``transaction=True`` test anywhere in the package without a manual
    per-file edit — which is the trigger #716 was filed to catch.

    Runs regardless of ``-m``/``-k`` deselection order: an item tagged here
    that later gets deselected (e.g. a playwright test under the default
    ``-m "not playwright"``) never executes, so its fixture never activates
    either way.
    """
    by_module = {}
    for item in items:
        module = getattr(item, "module", None)
        if module is None or not module.__name__.startswith("crush_lu.tests"):
            continue
        by_module.setdefault(module, []).append(item)

    for module_items in by_module.values():
        flushes = any(
            marker.kwargs.get("transaction")
            for item in module_items
            for marker in item.iter_markers("django_db")
        )
        if not flushes:
            continue
        for item in module_items:
            # add_marker(usefixtures(...)) alone is too late here: an item's
            # fixture closure is already computed by collection time, and
            # adding the marker doesn't retroactively recompute it. Append to
            # fixturenames directly (the marker stays too, for introspection).
            item.add_marker(pytest.mark.usefixtures("_restore_migration_seeded_rows"))
            if "_restore_migration_seeded_rows" not in item.fixturenames:
                item.fixturenames.append("_restore_migration_seeded_rows")


@pytest.fixture(scope="module")
def _restore_migration_seeded_rows(django_db_setup, django_db_blocker):
    """Put the crush_lu data-migration catalogues back after this module
    flushes them.

    Module scope is what makes this work: a function-scoped fixture would be
    finalized *before* the flush it needs to undo. Snapshot the seeded rows
    on the way in, replay them on the way out. Attached to a module only via
    ``pytest_collection_modifyitems`` above, never requested directly.

    Three details here are load-bearing:
    - **module scope** — see above;
    - **deferred constraint checks** on replay — ``Trait.opposite`` is
      self-referential, so no single insertion order satisfies every FK;
    - **sequence reset after replay** — replaying explicit pks leaves the
      sequences behind them, so the next insert would collide.

    Deliberately scoped to ``crush_lu`` models only: ``django.contrib.sites``
    is rebuilt by ``post_migrate`` and by the root ``conftest.py``, and
    replaying its rows fights the ``SITE_ID`` row over pks.

    Known gap (#716, accepted rather than fixed — P3, unreachable through a
    normal run today): seven modules assert on these same migration-seeded
    catalogues without seeding their own copy — ``test_matching.py``,
    ``test_crush_connect_gate.py``, ``test_crush_connect.py``,
    ``test_crush_connect_onboarding.py``, ``test_onboarding_step_sync_resume.py``,
    ``test_event_identity_ui.py``, ``test_profiles.py``. That only bites a
    serial run against a DB flushed *before the first test* — a state this
    fixture now prevents from arising in normal use, since nothing flushes
    without restoring anymore. One of the seven, ``test_crush_connect_onboarding.py``,
    already self-heals independently of this fixture (its ``_interest_pks``/
    ``_trait_pks`` helpers lazily create any missing rows); the other six
    still trust the DB to hold what the migrations put there.
    """
    from django.apps import apps as global_apps
    from django.core import serializers
    from django.core.management.color import no_style
    from django.db import connection as db_connection, transaction

    models = [
        model
        for model in global_apps.get_models()
        if model._meta.app_label == "crush_lu" and not model._meta.proxy
    ]
    with django_db_blocker.unblock():
        # A pristine test DB holds nothing but migration-seeded rows here:
        # every preceding test is a plain TestCase and rolled its own data
        # back.
        snapshot = serializers.serialize(
            "json",
            [obj for model in models for obj in model._base_manager.all()],
        )

    yield

    with django_db_blocker.unblock():
        # Constraint checks are deferred for the replay, exactly as Django's
        # own ``deserialize_db_from_string`` does it: ``Trait.opposite``
        # points at another Trait, so no single insertion order satisfies
        # every FK.
        with transaction.atomic(), db_connection.constraint_checks_disabled():
            for wrapped in serializers.deserialize(
                "json", snapshot, ignorenonexistent=True
            ):
                wrapped.save()
        db_connection.check_constraints()

        # Replaying explicit pks leaves the sequences behind them, so the
        # next insert would collide — reset them the way ``loaddata`` does.
        reset_sql = db_connection.ops.sequence_reset_sql(no_style(), models)
        if reset_sql:
            with db_connection.cursor() as cursor:
                for statement in reset_sql:
                    cursor.execute(statement)
