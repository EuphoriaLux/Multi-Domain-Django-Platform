"""SafeCurrentSiteMiddleware caches Site scalars, never the model instance.

A pickled model instance records the Django version that wrote it, and
django-redis unpickling it under any other version warns ``Pickled model
instance's Django version 6.0.8 does not match the current version 6.1`` on
every request until the key expires. The staging slot logged exactly that for
its first minutes on 6.1 (2026-09-04), and prod would repeat it at the next
swap that carries a Django bump. LocMemCache pickles values too, so these
tests exercise the real round-trip, not a mock.
"""

import pytest
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db.models import Model
from django.test import RequestFactory

from azureproject import middleware as mw

pytestmark = pytest.mark.django_db

HOST = "crush.lu"
DOMAIN_KEY = mw._site_cache_key(f"site_by_domain:{HOST}")
DEFAULT_KEY = mw._site_cache_key("site_default")
LEGACY_DOMAIN_KEY = f"site_by_domain:{HOST}"
LEGACY_DEFAULT_KEY = "site_default"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def site():
    row, _ = Site.objects.update_or_create(
        domain=HOST, defaults={"name": "Crush.lu"}
    )
    return row


def _get_site(host=HOST):
    middleware = mw.SafeCurrentSiteMiddleware(lambda request: None)
    return middleware._get_site(RequestFactory().get("/", HTTP_HOST=host))


def _get_default_site():
    return mw.SafeCurrentSiteMiddleware(lambda request: None)._get_default_site()


def test_the_cache_holds_scalars_not_a_model_instance(site):
    assert _get_site().pk == site.pk

    cached = cache.get(DOMAIN_KEY)
    assert isinstance(cached, dict)
    assert not isinstance(cached, Model)
    assert cached == {"id": site.pk, "domain": HOST, "name": "Crush.lu"}


def test_a_warm_cache_rebuilds_a_fetched_looking_site_without_a_query(
    site, django_assert_num_queries
):
    _get_site()

    with django_assert_num_queries(0):
        rebuilt = _get_site()

    assert rebuilt == site
    assert (rebuilt.pk, rebuilt.domain, rebuilt.name) == (site.pk, HOST, "Crush.lu")
    # Mirrors Model.from_db(): a save() must UPDATE, never INSERT a duplicate.
    assert rebuilt._state.adding is False
    assert rebuilt._state.db == "default"


def test_www_hosts_share_the_canonical_key(site, django_assert_num_queries):
    _get_site()

    with django_assert_num_queries(0):
        assert _get_site(f"www.{HOST}").pk == site.pk


def test_a_pickled_instance_under_the_versioned_key_is_a_miss(
    site, django_assert_num_queries
):
    """Rollback safety: old code may have written its shape here mid-deploy."""
    cache.set(DOMAIN_KEY, site, 300)

    with django_assert_num_queries(1):
        assert _get_site().pk == site.pk

    assert isinstance(cache.get(DOMAIN_KEY), dict)


def test_a_partial_dict_is_a_miss(site, django_assert_num_queries):
    cache.set(DOMAIN_KEY, {"id": site.pk}, 300)

    with django_assert_num_queries(1):
        assert _get_site().pk == site.pk

    assert cache.get(DOMAIN_KEY) == {"id": site.pk, "domain": HOST, "name": "Crush.lu"}


def test_the_legacy_unversioned_keys_are_never_read(site, django_assert_num_queries):
    """The previous code cached whole instances under these names.

    Reading them would resurrect the version-mismatch warning for up to their
    TTL after a deploy; the format bump in the key is what keeps the two
    shapes apart in both directions.
    """
    cache.set(LEGACY_DOMAIN_KEY, site, 300)
    cache.set(LEGACY_DEFAULT_KEY, site, 300)

    with django_assert_num_queries(1):
        assert _get_site().pk == site.pk
    assert cache.get(DOMAIN_KEY) is not None

    with django_assert_num_queries(1):
        _get_default_site()
    assert isinstance(cache.get(DEFAULT_KEY), dict)


def test_the_default_site_path_caches_scalars_too(django_assert_num_queries):
    Site.objects.update_or_create(pk=1, defaults={"domain": "example.com", "name": "x"})

    first = _get_default_site()

    cached = cache.get(DEFAULT_KEY)
    assert isinstance(cached, dict)
    assert not isinstance(cached, Model)
    assert cached["id"] == first.pk

    with django_assert_num_queries(0):
        assert _get_default_site() == first
