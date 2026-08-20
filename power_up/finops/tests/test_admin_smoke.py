"""
Admin regression tests for power_up_admin_site.

`manage.py check` validates that list_display names resolve to a real field,
model-admin method, or model attribute -- it does NOT execute those methods,
so a callable that raises once actually called only surfaces at render time,
on a changelist with at least one row. This is exactly how the
`CostForecastAdmin.confidence_interval` bug reached prod: it rendered fine
with zero rows (nothing to iterate) and 500'd the moment a real forecast
existed, because `format_html('{:.2f}', decimal_value)` escapes its args to
strings *before* Python applies the numeric format spec, and `'{:.2f}'` has
no meaning for a string. `CostAnomalyAdmin.deviation_display` had the
identical bug on the same file, on the same numeric-format-spec-inside-
format_html pattern -- caught by reviewing the rest of the file after the
first one.

Run with: pytest power_up/finops/tests/test_admin_smoke.py -v
"""

import datetime

from django.contrib.auth import get_user_model

from power_up.admin import power_up_admin_site
from power_up.finops.models import CostAnomaly, CostForecast

User = get_user_model()


def _changelist_path(model):
    """Build the power-admin changelist path directly.

    `reverse()` resolves against ROOT_URLCONF, which is only the fallback --
    DomainURLRoutingMiddleware swaps in urls_power_up/urls_portal per
    request, and neither is active outside a real request. Building the
    literal path sidesteps that (see the crush_lu equivalent gotcha in
    CLAUDE.md: "use literal paths in tests").
    """
    return f"/power-admin/{model._meta.app_label}/{model._meta.model_name}/"


class TestAdminChangelistsRenderEmpty:
    """Every registered power_up_admin_site changelist must render with zero rows.

    Catches invalid list_select_related / list_filter references -- the
    class of bug `manage.py check` and an empty-table smoke test both catch.
    """

    def test_all_changelists_render(self, db, client):
        superuser = User.objects.create_superuser(
            username="finops_admin_smoke", email="finops-admin@test.lu", password="x"
        )
        client.force_login(superuser)
        failures = []
        for model in power_up_admin_site._registry:
            url = _changelist_path(model)
            response = client.get(url)
            if response.status_code != 200:
                failures.append(
                    f"{model.__name__}: HTTP {response.status_code} at {url}"
                )
        assert failures == [], "Changelists failed to render:\n" + "\n".join(failures)


class TestCostForecastAdminWithData:
    """The bug only reproduces once a row exists for list_display to render.

    Regression test for the `confidence_interval` column: it must not raise
    when Django actually formats it for a real row.
    """

    def test_changelist_renders_with_a_forecast_row(self, db, client):
        CostForecast.objects.create(
            forecast_date=datetime.date(2026, 9, 1),
            dimension_type="overall",
            dimension_value="all",
            forecast_cost="1234.56",
            lower_bound="1000.00",
            upper_bound="1500.00",
            confidence="95.00",
            training_period_start=datetime.date(2026, 6, 1),
            training_period_end=datetime.date(2026, 8, 31),
            training_days=91,
        )
        superuser = User.objects.create_superuser(
            username="finops_forecast_admin",
            email="forecast-admin@test.lu",
            password="x",
        )
        client.force_login(superuser)
        response = client.get(_changelist_path(CostForecast))
        assert response.status_code == 200
        assert b"1000.00" in response.content and b"1500.00" in response.content


class TestCostAnomalyAdminWithData:
    """Same bug class as CostForecast, in the same file: deviation_display()
    applied a {:.1f} format spec to format_html-escaped deviation_percent.
    """

    def test_changelist_renders_with_an_anomaly_row(self, db, client):
        CostAnomaly.objects.create(
            detected_date=datetime.date(2026, 9, 1),
            anomaly_type="spike",
            severity="high",
            dimension_type="service",
            dimension_value="Virtual Machines",
            actual_cost="450.00",
            expected_cost="150.00",
            deviation_percent="200.00",
            description="Cost spike detected",
        )
        superuser = User.objects.create_superuser(
            username="finops_anomaly_admin",
            email="anomaly-admin@test.lu",
            password="x",
        )
        client.force_login(superuser)
        response = client.get(_changelist_path(CostAnomaly))
        assert response.status_code == 200
        assert b"200.0%" in response.content
