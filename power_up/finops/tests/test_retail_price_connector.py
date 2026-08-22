import json

from power_up.finops.retail_prices.connectors.azure import (
    AzureRetailPricesConnector,
)


class FakeResponse:
    def __init__(self, *, url, payload):
        self.url = url
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8")

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def test_retry_after_is_capped_so_a_throttled_upstream_cannot_park_a_worker():
    """urllib3 sleeps for whatever `Retry-After` says, bounded by nothing.

    Not the connect timeout, not the read timeout, not the backoff cap. An
    uncapped `Retry-After: 300` from a throttled prices.azure.com would hold a
    web worker for five minutes inside a request the scheduler stopped reading
    long ago — the precise failure the per-region budget exists to prevent.
    """

    class Headers(dict):
        pass

    class ThrottledResponse:
        headers = Headers({"Retry-After": "300"})

    connector = AzureRetailPricesConnector()
    retry = connector.session.get_adapter("https://prices.azure.com").max_retries

    assert retry.get_retry_after(ThrottledResponse()) == connector.MAX_RETRY_AFTER

    # urllib3 rebuilds the Retry on each increment; the cap must survive that.
    assert retry.new().get_retry_after(ThrottledResponse()) == (
        connector.MAX_RETRY_AFTER
    )

    # A short, reasonable Retry-After is still honoured as-is.
    ThrottledResponse.headers = Headers({"Retry-After": "2"})
    assert retry.get_retry_after(ThrottledResponse()) == 2


def test_azure_connector_follows_next_page_and_scopes_european_vm_prices_eur():
    next_url = "https://prices.azure.com/api/retail/prices?$skip=1000"
    session = FakeSession(
        [
            FakeResponse(
                url="https://prices.azure.com/api/retail/prices?first",
                payload={"Items": [{"meterId": "one"}], "NextPageLink": next_url},
            ),
            FakeResponse(
                url=next_url,
                payload={"Items": [{"meterId": "two"}], "NextPageLink": None},
            ),
        ]
    )
    connector = AzureRetailPricesConnector(session=session)

    pages = list(connector.iter_pages(regions=["westeurope"], currency="EUR"))

    assert len(pages) == 2
    assert session.calls[1][0] == next_url
    assert session.calls[1][1]["params"] is None
    first_params = session.calls[0][1]["params"]
    assert first_params["currencyCode"] == "'EUR'"
    assert "serviceName eq 'Virtual Machines'" in first_params["$filter"]
    assert "armRegionName eq 'westeurope'" in first_params["$filter"]
