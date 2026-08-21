"""Shared connector contract for provider retail-price APIs."""

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ConnectorPage:
    source_url: str
    next_page_url: str
    payload: dict[str, Any]
    raw_content: bytes


class RetailPriceConnector:
    provider: str
    name: str
    endpoint: str

    def iter_pages(
        self, *, regions: list[str], currency: str
    ) -> Iterable[ConnectorPage]:
        raise NotImplementedError

    def normalize_item(self, *, item, snapshot_date, run, raw_page, item_index):
        """Return canonical RetailPriceSnapshot objects for one provider item."""
        raise NotImplementedError
