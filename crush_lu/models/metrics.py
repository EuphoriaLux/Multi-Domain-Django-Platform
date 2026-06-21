"""
Weekly KPI snapshots for Crush.lu.

One row per ISO week persists the full KPI dict computed by
``crush_lu.services.weekly_kpis``. Persisting each week is what makes
week-over-week tracking (and the deltas shown in the weekly email) possible,
and gives a future dashboard a ready-made time series to read.
"""
from django.db import models


class WeeklyMetricsSnapshot(models.Model):
    """A frozen snapshot of the platform's KPIs for a single ISO week.

    ``week_start`` is always a Monday (ISO weekday 1) and is unique, so
    re-running the computation for a week ``update_or_create``s the same row
    rather than duplicating it. ``metrics`` holds the entire KPI payload (see
    ``compute_weekly_snapshot``) so new KPIs can be added without a migration.
    """

    week_start = models.DateField(
        unique=True,
        db_index=True,
        help_text="Monday (ISO) that begins the reported week.",
    )
    week_end = models.DateField(
        help_text="Sunday (inclusive) that ends the reported week.",
    )
    metrics = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full KPI payload for the week (see compute_weekly_snapshot).",
    )
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-week_start"]
        verbose_name = "Weekly KPI snapshot"
        verbose_name_plural = "Weekly KPI snapshots"

    def __str__(self):
        return f"KPIs for week of {self.week_start.isoformat()}"
