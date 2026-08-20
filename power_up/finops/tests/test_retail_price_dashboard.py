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
        connector=FakeConnector(first),
    )
    sync_retail_prices(snapshot_date=today, connector=FakeConnector(second))
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
    assert len(response.context["chart_series"][0]["data"]) == 2
    content = response.content.decode()
    assert "European Azure VM retail prices" in content
    assert "legal or contractual compliance" in content
    assert "▲ +20.00%" in content


@pytest.mark.django_db
def test_retail_sync_webhook_requires_token_and_invokes_command(
    client, settings, mocker
):
    settings.SECRET_SYNC_TOKEN = "expected-token"
    command = mocker.patch("power_up.finops.views_webhook.call_command")

    assert client.post("/finops/api/sync/retail-prices/").status_code == 403
    response = client.post(
        "/finops/api/sync/retail-prices/",
        HTTP_X_SYNC_TOKEN="expected-token",
    )

    assert response.status_code == 200
    command.assert_called_once()
    assert command.call_args.args[0] == "sync_retail_prices"
    assert command.call_args.kwargs["currency"] == "EUR"
