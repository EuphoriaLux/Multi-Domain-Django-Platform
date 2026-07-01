"""
Pytest configuration for FinOps Hub tests
"""

import pytest
from django.conf import settings
from django.test import Client
from django.urls import clear_url_caches


@pytest.fixture
def client():
    """Test client that sends the Power-Up host.

    DomainURLRoutingMiddleware routes by host, so the default 'testserver'
    host would resolve to DEV_DEFAULT's urlconf (crush.lu) and every
    /finops/ request would 404. Sending the real domain makes the
    middleware pick azureproject.urls_power_up.
    """
    return Client(HTTP_HOST='power-up.lu')


@pytest.fixture(scope='function', autouse=True)
def use_power_up_urls():
    """Point ROOT_URLCONF at Power-UP so reverse() works outside a request"""
    # Store original ROOT_URLCONF
    original_urlconf = settings.ROOT_URLCONF

    # Set to Power-UP URLs
    settings.ROOT_URLCONF = 'azureproject.urls_power_up'

    # Clear URL resolver cache to force re-import
    clear_url_caches()

    yield

    # Restore after each test
    settings.ROOT_URLCONF = original_urlconf
    clear_url_caches()
