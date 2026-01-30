"""
Pytest configuration for FinOps Hub tests
"""

import pytest
from django.conf import settings
from django.urls import clear_url_caches


@pytest.fixture(scope='function', autouse=True)
def use_power_up_urls():
    """Configure URLs to use Power-UP configuration for all tests"""
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
