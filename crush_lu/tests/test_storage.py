"""Tests for SAS URL caching in PrivateAzureStorage."""
from unittest.mock import patch

import pytest
from django.core.cache import cache

from crush_lu.storage import PrivateAzureStorage


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _make_storage():
    storage = PrivateAzureStorage.__new__(PrivateAzureStorage)
    storage.account_name = "testacct"
    storage.account_key = "dGVzdA=="
    storage.azure_container = "crush-lu-private"
    storage._is_azurite = False
    storage._cdn_domain = None
    storage._azurite_host = None
    storage.expiration_secs = 3600
    return storage


@patch("crush_lu.storage.generate_blob_sas", return_value="sv=fake&sig=abc")
def test_url_caches_sas_token_across_calls(mock_gen):
    storage = _make_storage()

    first = storage.url("users/1/photos/abc.jpg")
    second = storage.url("users/1/photos/abc.jpg")

    assert first == second
    assert mock_gen.call_count == 1


@patch("crush_lu.storage.generate_blob_sas", return_value="sv=fake&sig=abc")
def test_url_cache_is_scoped_per_blob(mock_gen):
    storage = _make_storage()

    storage.url("users/1/photos/a.jpg")
    storage.url("users/1/photos/b.jpg")

    assert mock_gen.call_count == 2


@patch("crush_lu.storage.generate_blob_sas", return_value="sv=fake&sig=abc")
def test_url_cache_scoped_by_expiry(mock_gen):
    storage = _make_storage()

    storage.url("users/1/photos/a.jpg", expire=1800)
    storage.url("users/1/photos/a.jpg", expire=3600)

    assert mock_gen.call_count == 2


@patch("crush_lu.storage.generate_blob_sas", return_value="sv=fake&sig=abc")
def test_cdn_and_direct_url_formats_are_distinct(mock_gen):
    storage = _make_storage()
    direct = storage.url("users/1/photos/a.jpg")

    storage._cdn_domain = "cdn.example.com"
    cdn = storage.url("users/1/photos/a.jpg")

    # Match on explicit prefixes so we verify the full host, not a
    # substring anywhere in the URL (also satisfies CodeQL's
    # py/incomplete-url-substring-sanitization rule on test assertions).
    assert direct.startswith("https://testacct.blob.core.windows.net/")
    assert cdn.startswith("https://cdn.example.com/")
    assert direct != cdn


@patch("crush_lu.storage.generate_blob_sas", return_value="sv=fake&sig=abc")
def test_short_expiry_skips_cache_entirely(mock_gen):
    """Expire windows that would yield a TTL shorter than the safety
    margin must not be cached — the cache would outlive the token."""
    storage = _make_storage()

    storage.url("users/1/photos/a.jpg", expire=30)
    storage.url("users/1/photos/a.jpg", expire=30)

    assert mock_gen.call_count == 2  # no cache hit on the second call


@patch("crush_lu.storage.generate_blob_sas", return_value="sv=fake&sig=abc")
def test_cache_invalidates_on_signing_key_rotation(mock_gen):
    """Rotating AZURE_ACCOUNT_KEY must force fresh SAS generation;
    otherwise revoked tokens would keep serving 403s until TTL expiry."""
    storage = _make_storage()

    storage.url("users/1/photos/a.jpg")
    storage.account_key = "rotated-key=="  # simulate prod key rotation
    storage.url("users/1/photos/a.jpg")

    assert mock_gen.call_count == 2
