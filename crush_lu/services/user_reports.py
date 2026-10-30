"""Filing a member's report about another member into the moderation queue."""

import logging

logger = logging.getLogger(__name__)

# Largest value a PositiveIntegerField accepts on a 32-bit integer backend.
_MAX_POSITIVE_INT = 2147483647


def file_user_report(
    reporter, target, *, reason, details="", source="", source_id=None
):
    """Create a ``UserReport`` and nudge staff. Returns the report.

    Every value may come straight from a client, so an unknown reason or
    source falls back rather than raising, and ``source_id`` is coerced: a
    tampered value ("abc", "-1") can't turn reporting into a 500.
    """
    from crush_lu.models import UserReport

    valid_reasons = {c for c, _label in UserReport.REASON_CHOICES}
    valid_sources = {c for c, _label in UserReport.SOURCE_CHOICES}
    try:
        source_id = int(source_id)
        if source_id <= 0 or source_id > _MAX_POSITIVE_INT:
            source_id = None
    except (TypeError, ValueError):
        source_id = None

    report = UserReport.objects.create(
        reporter=reporter,
        reported_user=target,
        reason=reason if reason in valid_reasons else "other",
        details=(details or "").strip()[:2000],
        source=source if source in valid_sources else "",
        source_id=source_id,
    )

    # Notify staff/coaches best-effort — must never block the report flow.
    try:
        from crush_lu.notification_service import notify_report_filed

        notify_report_filed(report)
    except Exception:  # pragma: no cover - notification must never break reporting
        logger.exception("Report-filed notification failed for report %s", report.pk)

    return report
