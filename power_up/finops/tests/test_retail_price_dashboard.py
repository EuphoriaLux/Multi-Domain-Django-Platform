from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from power_up.finops.retail_prices.service import sync_retail_prices
from power_up.finops.tests.test_retail_price_sync import FakeConnector, azure_item

User = get_user_model()


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        username="price-user",
        email="price-user@example.com",
        password="pw",
        is_staff=False,
    )


@pytest.mark.django_db
def test_price_dashboard_requires_login(client):
    response = client.get("/finops/prices/")
    assert response.status_code == 302
    assert "login" in response.url


@pytest.mark.django_db
def test_power_up_login_page_uses_domain_safe_template(client):
    response = client.get(
        "/accounts/login/?next=/finops/prices/",
        HTTP_HOST="test.powerup.lu",
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Power Hub" in content
    assert "Access the protected Azure price tracker." in content


@pytest.mark.django_db
def test_authenticated_customer_sees_eur_european_comparison_and_change(
    client, regular_user
):
    today = timezone.localdate()
    first = {"Items": [azure_item("0.10000000")], "NextPageLink": None}
    second = {"Items": [azure_item("0.12000000")], "NextPageLink": None}
    sync_retail_prices(
        snapshot_date=today - timedelta(days=1),
        region="westeurope",
        connector=FakeConnector(first),
    )
    sync_retail_prices(
        snapshot_date=today, region="westeurope", connector=FakeConnector(second)
    )
    client.force_login(regular_user)

    response = client.get(
        "/finops/prices/",
        {
            "sku": "Standard_D2s_v5",
            "currency": "EUR",
            "region": "westeurope",
            "price_type": "Consumption",
            "purchase_model": "on_demand",
        },
    )

    assert response.status_code == 200
    assert response.context["currency"] == "EUR"
    assert response.context["selected_regions"] == ["westeurope"]
    assert response.context["increased_count"] == 1
    assert len(response.context["region_options"]) == 19
    germany_north = next(
        item
        for item in response.context["region_options"]
        if item["code"] == "germanynorth"
    )
    assert germany_north["restricted_access"] is True
    assert germany_north["has_snapshot"] is False
    assert len(response.context["chart_series"][0]["data"]) == 2
    content = response.content.decode()
    assert "European Azure VM retail prices" in content
    assert "legal or contractual compliance" in content
    assert "▲ +20.00%" in content


@pytest.mark.django_db
def test_price_decrease_renders_without_a_redundant_sign(client, regular_user):
    """A price decrease must render as '▼ 16.67%', never '▼ -16.67%'.

    change_percent is negative for a decrease -- the down arrow already
    conveys direction, so the magnitude shown next to it must be unsigned.
    Regression test for issue #896.
    """
    today = timezone.localdate()
    first = {"Items": [azure_item("0.12000000")], "NextPageLink": None}
    second = {"Items": [azure_item("0.10000000")], "NextPageLink": None}
    sync_retail_prices(
        snapshot_date=today - timedelta(days=1),
        region="westeurope",
        connector=FakeConnector(first),
    )
    sync_retail_prices(
        snapshot_date=today, region="westeurope", connector=FakeConnector(second)
    )
    client.force_login(regular_user)

    response = client.get(
        "/finops/prices/",
        {
            "sku": "Standard_D2s_v5",
            "currency": "EUR",
            "region": "westeurope",
            "price_type": "Consumption",
            "purchase_model": "on_demand",
        },
    )

    assert response.status_code == 200
    assert response.context["decreased_count"] == 1
    content = response.content.decode()
    assert "▼ 16.67%" in content
    assert "▼ -16.67%" not in content


@pytest.mark.django_db
def test_retail_sync_webhook_requires_token_and_invokes_command(
    client, settings, mocker
):
    settings.SECRET_SYNC_TOKEN = "expected-token"
    command = mocker.patch("power_up.finops.views_webhook.call_command")

    assert client.post("/finops/api/sync/retail-prices/").status_code == 403
    response = client.post(
        "/finops/api/sync/retail-prices/",
        data={"region": "westeurope"},
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    assert response.status_code == 200
    assert response.json()["region"] == "westeurope"
    command.assert_called_once()
    assert command.call_args.args[0] == "sync_retail_prices"
    assert command.call_args.kwargs["currency"] == "EUR"
    assert command.call_args.kwargs["regions"] == ["westeurope"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [{}, {"region": ""}, {"region": "us-east-1"}],
    ids=["missing", "blank", "unknown"],
)
def test_retail_sync_webhook_rejects_a_request_without_a_valid_region(
    client, settings, mocker, body
):
    """A region-less POST must fail loudly rather than fall back to all 19.

    Syncing the whole catalogue in one request is what timed out every night;
    silently accepting the old payload shape would reintroduce it.
    """
    settings.SECRET_SYNC_TOKEN = "expected-token"
    command = mocker.patch("power_up.finops.views_webhook.call_command")

    response = client.post(
        "/finops/api/sync/retail-prices/",
        data=body,
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    assert response.status_code == 400
    command.assert_not_called()


@pytest.mark.django_db
def test_retail_sync_regions_endpoint_requires_token_and_lists_every_region(
    client, settings
):
    from power_up.finops.retail_prices.connectors.azure import (
        DEFAULT_EUROPEAN_REGIONS,
    )

    settings.SECRET_SYNC_TOKEN = "expected-token"

    assert client.get("/finops/api/sync/retail-prices/regions/").status_code == 403
    response = client.get(
        "/finops/api/sync/retail-prices/regions/",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    assert response.status_code == 200
    assert response.json()["regions"] == list(DEFAULT_EUROPEAN_REGIONS)
    # Handed out once so every region of an invocation shares a date.
    assert response.json()["snapshot_date"] == timezone.localdate().isoformat()


@pytest.mark.django_db
def test_retail_sync_webhook_pins_the_snapshot_date_it_is_given(
    client, settings, mocker
):
    """Each region arrives as its own request, so the date must be caller-pinned.

    Left to itself every call would re-evaluate ``localdate()``, and a walk
    straddling local midnight would file its regions under two dates and
    complete neither.
    """
    settings.SECRET_SYNC_TOKEN = "expected-token"
    command = mocker.patch("power_up.finops.views_webhook.call_command")

    response = client.post(
        "/finops/api/sync/retail-prices/",
        data={"region": "westeurope", "snapshot_date": "2026-08-21"},
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    assert response.status_code == 200
    assert command.call_args.kwargs["snapshot_date"] == "2026-08-21"

    # Omitting it is fine for a manual one-off: the command falls back to today.
    command.reset_mock()
    client.post(
        "/finops/api/sync/retail-prices/",
        data={"region": "westeurope"},
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )
    assert command.call_args.kwargs["snapshot_date"] is None

    # A malformed date is refused rather than silently ignored.
    command.reset_mock()
    bad = client.post(
        "/finops/api/sync/retail-prices/",
        data={"region": "westeurope", "snapshot_date": "21-08-2026"},
        content_type="application/json",
        HTTP_X_SYNC_TOKEN="expected-token",
    )
    assert bad.status_code == 400
    command.assert_not_called()
