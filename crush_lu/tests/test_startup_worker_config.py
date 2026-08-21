"""Regression checks for slot-aware Gunicorn worker configuration."""

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STARTUP_SCRIPT = REPOSITORY_ROOT / "startup.sh"


def _startup_script() -> str:
    return STARTUP_SCRIPT.read_text(encoding="utf-8")


def test_staging_defaults_to_one_worker() -> None:
    script = _startup_script()

    assert 'if [ "${DJANGO_ENV:-production}" = "staging" ]; then' in script
    assert "GUNICORN_WORKERS=1" in script


def test_production_keeps_four_workers_by_default() -> None:
    script = _startup_script()

    assert "GUNICORN_WORKERS=4" in script
    assert 'gunicorn --workers "$GUNICORN_WORKERS"' in script
    assert "gunicorn --workers 4" not in script


def test_web_concurrency_can_override_slot_default() -> None:
    script = _startup_script()

    assert 'GUNICORN_WORKERS="${WEB_CONCURRENCY:-}"' in script
    assert "WEB_CONCURRENCY must be a positive integer" in script
    assert "WEB_CONCURRENCY must be at least 1" in script
