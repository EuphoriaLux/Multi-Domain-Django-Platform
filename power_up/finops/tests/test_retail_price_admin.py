import pytest

from power_up.admin import power_up_admin_site
from power_up.finops.admin import (
    RetailPriceRawPageAdmin,
    RetailPriceSnapshotAdmin,
    RetailPriceSyncRunAdmin,
)
from power_up.finops.models import (
    RetailPriceRawPage,
    RetailPriceSnapshot,
    RetailPriceSyncRun,
)


@pytest.mark.parametrize(
    ("model", "admin_class"),
    [
        (RetailPriceSyncRun, RetailPriceSyncRunAdmin),
        (RetailPriceRawPage, RetailPriceRawPageAdmin),
        (RetailPriceSnapshot, RetailPriceSnapshotAdmin),
    ],
)
def test_retail_price_history_is_registered_read_only(model, admin_class):
    model_admin = power_up_admin_site._registry[model]

    assert isinstance(model_admin, admin_class)
    assert model_admin.has_add_permission(None) is False
    assert model_admin.has_change_permission(None) is False
    assert model_admin.has_delete_permission(None) is False
