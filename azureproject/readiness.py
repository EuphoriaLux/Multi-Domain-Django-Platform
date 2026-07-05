"""
Deep readiness checks backing the /readyz/ endpoint.

/healthz/ (static 200, HealthCheckMiddleware) answers "is the process up?"
and must stay cheap — Azure's Health Check feature pings it per instance
and recycles instances that fail it.

/readyz/ answers "can this container actually serve traffic?" and is used
as the slot-swap warm-up gate (WEBSITE_SWAP_WARMUP_PING_PATH): during a
swap, Azure pings the *incoming* container with production config applied
and refuses to complete the swap until this returns 200. The checks below
therefore verify exactly what a swap changes under the container: database
binding, applied migrations, Redis binding, and storage credentials.

Failure detail deliberately exposes exception class names only — messages
can embed hostnames/DSNs.
"""

from django.core.cache import caches
from django.core.files.storage import default_storage
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


def _check_database():
    connection = connections["default"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _check_migrations():
    connection = connections["default"]
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if plan:
        raise RuntimeError(f"{len(plan)} unapplied migration(s)")


def _check_cache():
    cache = caches["default"]
    # django-redis is configured with IGNORE_EXCEPTIONS, so a set/get
    # roundtrip through the cache API silently succeeds with Redis down.
    # Ping the raw client instead when Redis backs the cache.
    if "RedisCache" in cache.__class__.__name__:
        from django_redis import get_redis_connection

        get_redis_connection("default").ping()
    else:
        cache.set("readyz_probe", "ok", timeout=10)
        if cache.get("readyz_probe") != "ok":
            raise RuntimeError("cache roundtrip failed")


def _check_storage():
    # On Azure Blob, exists() returns False both for a missing blob and for a
    # missing/misnamed *container*, so probing a blob key can't distinguish
    # "healthy" from "container gone". When the backend exposes a container
    # client (django-storages AzureStorage does), assert the container itself
    # exists; otherwise fall back to a connectivity probe.
    client = getattr(default_storage, "client", None)
    if client is not None and hasattr(client, "exists"):
        if not client.exists():
            raise RuntimeError("storage container missing")
    else:
        default_storage.exists("readyz-probe")


CHECKS = (
    ("database", _check_database),
    ("migrations", _check_migrations),
    ("cache", _check_cache),
    ("storage", _check_storage),
)


def run_readiness_checks():
    """Run all checks; return (all_passed, {name: "ok" | "fail: ExcName"})."""
    results = {}
    all_passed = True
    for name, check in CHECKS:
        try:
            check()
            results[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - each check must not kill the probe
            all_passed = False
            # RuntimeErrors are raised by the checks themselves with safe
            # messages; anything else may embed hostnames/DSNs — class only.
            if isinstance(exc, RuntimeError):
                detail = str(exc)
            else:
                detail = exc.__class__.__name__
            results[name] = f"fail: {detail}"
    return all_passed, results
