"""Tests for AzureCostBlobReader streaming decompression."""
import gzip
import io
from unittest.mock import MagicMock

import pytest

from power_up.finops.utils.blob_reader import AzureCostBlobReader


@pytest.fixture
def reader():
    # __init__ pulls Azure credentials from settings; bypass it for
    # unit tests. We only exercise download_and_decompress, which
    # touches blob_service_client.
    instance = AzureCostBlobReader.__new__(AzureCostBlobReader)
    instance.blob_service_client = MagicMock()
    instance.container_name = "msexports"
    return instance


def _make_gzip_bytes(rows):
    header = "cost,currency\n"
    body = "".join(f"{amount},{currency}\n" for amount, currency in rows)
    # Cost Management exports include a UTF-8 BOM on the first line.
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(("\ufeff" + header + body).encode("utf-8"))
    buf.seek(0)
    return buf


def test_download_and_decompress_streams_csv(reader):
    rows = [("10.5", "EUR"), ("3.25", "USD"), ("0.01", "CHF")]
    blob_bytes = _make_gzip_bytes(rows)

    blob_client = MagicMock()
    blob_client.download_blob.return_value = blob_bytes
    reader.blob_service_client.get_blob_client.return_value = blob_client

    stream = reader.download_and_decompress("some/path/part_0.csv.gz")

    # Header should not contain the stripped BOM.
    header = stream.readline().rstrip("\n")
    assert header == "cost,currency"

    data_lines = [line.rstrip("\n") for line in stream if line.strip()]
    assert data_lines == ["10.5,EUR", "3.25,USD", "0.01,CHF"]


def test_stream_csv_records_end_to_end(reader):
    rows = [(str(i), "EUR") for i in range(5)]
    blob_client = MagicMock()
    blob_client.download_blob.return_value = _make_gzip_bytes(rows)
    reader.blob_service_client.get_blob_client.return_value = blob_client

    batches = list(reader.stream_csv_records("path.csv.gz", batch_size=2))

    flattened = [r for batch in batches for r in batch]
    assert len(flattened) == 5
    assert flattened[0] == {"cost": "0", "currency": "EUR"}
    assert flattened[-1] == {"cost": "4", "currency": "EUR"}
