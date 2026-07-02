"""
Pytest configuration for FinOps Hub tests
"""

import pytest
from django.test import Client


@pytest.fixture
def client():
    """Test client that sends the Power-Up host.

    DomainURLRoutingMiddleware sets request.urlconf from the host on every
    request (it never consults ROOT_URLCONF), so the default 'testserver'
    host would resolve to DEV_DEFAULT's urlconf (crush.lu) and every
    /finops/ request would 404. Sending the real domain is the ONLY thing
    that routes these tests — ROOT_URLCONF overrides have no effect here.
    """
    return Client(HTTP_HOST='power-up.lu')
