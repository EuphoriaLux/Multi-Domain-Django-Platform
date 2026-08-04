"""
Tests for the Outlook contact sync (crush_lu/services/graph_contacts.py).

This sync writes to a live shared mailbox and has a destructive delete path, so
it had no business being untested. The invariants pinned here are the ones that
fail *silently* in production -- nothing raises, the sync reports success, and
the contact is simply wrong or absent from caller ID:

1. mobilePhone must reach Outlook in strict E.164. Outlook/Teams caller-ID
   lookup only matches '+' followed by digits; CrushProfile's own validator
   permits spaces and dashes, and LuxID writes bypass the form that strips
   them. A '+352 621 123 456' contact syncs "successfully" and never matches.
2. Stored contact IDs must be immutable IDs. A default Outlook ID changes when
   the item moves container; the stale ID then 404s and the next sync creates a
   duplicate contact rather than updating the existing one.
3. Bulk delete must not touch contacts it did not create. noreply@crush.lu is a
   shared mailbox and the delete path is reachable over HTTP.
4. Photos must not be re-uploaded when unchanged -- Exchange caps a mailbox at
   150 MB of writes per 5 minutes, and photos are the only large payload here.
5. 429 backoff must be bounded, because these calls run on the web request
   thread via post_save.
"""

from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest

from crush_lu.services import graph_contacts
from crush_lu.services.graph_contacts import (
    CRUSH_CATEGORY,
    IMMUTABLE_ID_PREFER,
    GraphContactsService,
    normalize_e164,
)


def _response(status_code=200, json_data=None, headers=None):
    """Build a stand-in for a requests.Response."""
    return SimpleNamespace(
        status_code=status_code,
        headers=headers or {},
        text="",
        json=lambda: json_data if json_data is not None else {},
    )


# The root conftest installs an autouse `disable_outlook_sync` fixture that
# stubs these four methods out for every test in the suite, so nothing can ever
# reach the live shared mailbox by accident. That guard stays; this module is
# the one place that needs the real implementations back, so it captures them
# at import time (before any patching) and restores them per-test.
_REAL_METHODS = {
    name: getattr(GraphContactsService, name)
    for name in ("create_contact", "update_contact", "delete_contact", "sync_profile")
}


@pytest.fixture
def service(disable_outlook_sync, monkeypatch):
    """A service with credentials stubbed and token acquisition short-circuited.

    Depends on disable_outlook_sync so it is guaranteed to run *after* the
    global stubs are installed, then puts the real methods back for this test
    only. Network access is still impossible -- every test here patches
    requests.request.
    """
    for name, func in _REAL_METHODS.items():
        monkeypatch.setattr(GraphContactsService, name, func)

    svc = GraphContactsService.__new__(GraphContactsService)
    svc.tenant_id = "tenant"
    svc.client_id = "client"
    svc.client_secret = "secret"
    svc.mailbox = "noreply@crush.lu"
    svc.retry_budget = graph_contacts.MAX_RETRY_SLEEP_TOTAL
    svc.get_access_token = lambda: "token"
    return svc


# ---------------------------------------------------------------------------
# E.164 normalization
# ---------------------------------------------------------------------------


class TestNormalizeE164:
    """Caller ID matches only on E.164. Anything else is a silent no-match."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+352621123456", "+352621123456"),
            ("+352 621 123 456", "+352621123456"),
            ("+352-621-123-456", "+352621123456"),
            ("+352 (621) 123.456", "+352621123456"),
            ("00352621123456", "+352621123456"),
            ("  +352 621 123 456  ", "+352621123456"),
        ],
    )
    def test_normalizes_accepted_formats(self, raw, expected):
        assert normalize_e164(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            None,
            "621123456",  # no country code / no '+'
            "352621123456",  # digits only, ambiguous
            "+0352621123456",  # E.164 country codes never start with 0
            "+35262",  # too short
            "+3526211234567890123",  # more than 15 digits
            "not a phone",
        ],
    )
    def test_rejects_unusable_input(self, raw):
        assert normalize_e164(raw) is None


# ---------------------------------------------------------------------------
# Contact payload
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildContactPayload:
    @pytest.fixture
    def profile(self, django_user_model):
        from crush_lu.models import CrushProfile

        user = django_user_model.objects.create_user(
            username="payload@crush.lu",
            email="payload@crush.lu",
            first_name="Ada",
            last_name="Lovelace",
        )
        return CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 5, 15),
            gender="F",
            location="Luxembourg City",
            is_approved=True,
            phone_number="+352 621 123 456",
        )

    def test_mobile_phone_is_normalized_to_e164(self, service, profile):
        """A DB value with spaces is valid per the model validator, and would
        never match an inbound call if forwarded to Outlook verbatim."""
        payload = service._build_contact_payload(profile)

        assert payload["mobilePhone"] == "+352621123456"

    def test_unnormalizable_phone_is_omitted_not_forwarded(self, service, profile):
        """Better to sync a contact with no number than one that looks correct
        in Outlook but can never match."""
        profile.phone_number = "621123456"

        payload = service._build_contact_payload(profile)

        assert "mobilePhone" not in payload

    def test_contact_is_stamped_with_the_crush_category(self, service, profile):
        """The category is the only thing that distinguishes our rows from a
        human's contacts in the shared mailbox; the bulk delete filters on it."""
        payload = service._build_contact_payload(profile)

        assert payload["categories"] == [CRUSH_CATEGORY]

    def test_identity_fields_are_mapped(self, service, profile):
        payload = service._build_contact_payload(profile)

        assert payload["givenName"] == "Ada"
        assert payload["surname"] == "Lovelace"
        assert payload["emailAddresses"][0]["address"] == "payload@crush.lu"
        assert "(Crush.lu)" in payload["displayName"]


# ---------------------------------------------------------------------------
# Immutable IDs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestImmutableIds:
    @pytest.fixture
    def profile(self, django_user_model):
        from crush_lu.models import CrushProfile

        user = django_user_model.objects.create_user(
            username="immutable@crush.lu",
            email="immutable@crush.lu",
            first_name="Grace",
            last_name="Hopper",
        )
        return CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1990, 1, 1),
            gender="F",
            is_approved=True,
            phone_number="+352621999999",
        )

    def test_create_requests_an_immutable_id(self, service, profile):
        """Without this header Outlook returns an ID that changes when the
        contact moves folder, orphaning the row and duplicating the contact."""
        with mock.patch(
            "requests.request", return_value=_response(201, {"id": "IMMUTABLE"})
        ) as request:
            contact_id = service.create_contact(profile, force=True)

        assert contact_id == "IMMUTABLE"
        assert request.call_args.kwargs["headers"]["Prefer"] == IMMUTABLE_ID_PREFER

    def test_update_reads_etag_with_the_immutable_id_header(self, service, profile):
        """The ETag GET must speak the same ID dialect as the stored ID."""
        profile.outlook_contact_id = "IMMUTABLE"
        responses = [
            _response(200, {"id": "IMMUTABLE"}, {"ETag": 'W/"1"'}),  # ETag GET
            _response(204),  # PATCH
        ]

        with mock.patch("requests.request", side_effect=responses) as request:
            assert service.update_contact(profile, force=True) is True

        get_call = request.call_args_list[0]
        assert get_call.kwargs["headers"]["Prefer"] == IMMUTABLE_ID_PREFER

    def test_translate_maps_ids_and_skips_errored_entries(self, service):
        """Already-immutable IDs come back with errorDetails; they must be
        dropped rather than written back as empty or garbage."""
        payload = {
            "value": [
                {"sourceId": "old-a", "targetId": "new-a"},
                {"sourceId": "old-b", "errorDetails": {"message": "bad id"}},
                {"sourceId": "old-c", "targetId": "new-c"},
            ]
        }

        with mock.patch("requests.request", return_value=_response(200, payload)):
            result = service.translate_ids_to_immutable(["old-a", "old-b", "old-c"])

        assert result == {"old-a": "new-a", "old-c": "new-c"}

    def test_translate_refuses_oversized_batches(self, service):
        """Graph caps translateExchangeIds at 1000 ids; silently truncating
        would leave the tail un-migrated and looking done."""
        with pytest.raises(ValueError):
            service.translate_ids_to_immutable(["x"] * 1001)


# ---------------------------------------------------------------------------
# Photo upload gating
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPhotoUploadGating:
    @pytest.fixture
    def profile(self, django_user_model):
        from crush_lu.models import CrushProfile

        user = django_user_model.objects.create_user(
            username="photo@crush.lu", email="photo@crush.lu"
        )
        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1990, 1, 1),
            is_approved=True,
            phone_number="+352621888888",
        )
        # Stand in for a real ImageField without touching storage.
        profile.photo_1 = mock.Mock()
        profile.photo_1.name = "users/1/photos/abc123.jpg"
        profile.photo_1.read.return_value = b"jpegbytes"
        profile.photo_1.__bool__ = lambda self: True
        return profile

    def test_unchanged_photo_is_not_re_uploaded(self, service, profile):
        """The nightly full sync touches every profile; re-PUTting every photo
        is the most likely way to hit the 150 MB / 5 min mailbox write cap."""
        profile.outlook_photo_key = "users/1/photos/abc123.jpg"

        with mock.patch("requests.request") as request:
            uploaded = service._upload_contact_photo("cid", profile, "token")

        assert uploaded is False
        request.assert_not_called()

    def test_changed_photo_is_uploaded_and_key_recorded(self, service, profile):
        from crush_lu.models import CrushProfile

        profile.outlook_photo_key = "users/1/photos/OLD.jpg"

        with mock.patch("requests.request", return_value=_response(200)):
            uploaded = service._upload_contact_photo("cid", profile, "token")

        assert uploaded is True
        assert (
            CrushProfile.objects.get(pk=profile.pk).outlook_photo_key
            == "users/1/photos/abc123.jpg"
        )

    def test_force_uploads_even_when_key_matches(self, service, profile):
        """A newly created contact carries no photo, even if the profile's key
        is still set from a contact that was deleted."""
        profile.outlook_photo_key = "users/1/photos/abc123.jpg"

        with mock.patch("requests.request", return_value=_response(200)) as request:
            uploaded = service._upload_contact_photo(
                "cid", profile, "token", force=True
            )

        assert uploaded is True
        request.assert_called_once()

    def test_failed_upload_does_not_record_the_key(self, service, profile):
        """Otherwise a transient failure is remembered as success and the photo
        is never retried."""
        from crush_lu.models import CrushProfile

        profile.outlook_photo_key = ""

        with mock.patch("requests.request", return_value=_response(500)):
            uploaded = service._upload_contact_photo("cid", profile, "token")

        assert uploaded is False
        assert CrushProfile.objects.get(pk=profile.pk).outlook_photo_key == ""


# ---------------------------------------------------------------------------
# Destructive bulk delete
# ---------------------------------------------------------------------------


class TestDeleteAllScoping:
    """noreply@crush.lu is a shared mailbox and delete_all_contacts_endpoint
    exposes this over HTTP. Deleting uncategorized contacts destroys anything a
    human filed there."""

    CONTACTS = [
        {"id": "ours-1", "displayName": "A (Crush.lu)", "categories": [CRUSH_CATEGORY]},
        {"id": "theirs", "displayName": "Accountant", "categories": []},
        {"id": "ours-2", "displayName": "B (Crush.lu)", "categories": [CRUSH_CATEGORY]},
        {"id": "theirs-2", "displayName": "Landlord"},  # no categories key at all
    ]

    def test_only_crush_contacts_are_deleted_by_default(self, service):
        deleted = []
        service.list_all_contacts_from_outlook = lambda: list(self.CONTACTS)
        service.delete_contact = lambda cid: (deleted.append(cid), True)[1]

        with mock.patch.object(graph_contacts, "is_sync_enabled", return_value=True):
            stats = service.delete_all_contacts_from_outlook()

        assert deleted == ["ours-1", "ours-2"]
        assert stats["deleted"] == 2
        assert stats["skipped"] == 2

    def test_include_foreign_is_opt_in(self, service):
        deleted = []
        service.list_all_contacts_from_outlook = lambda: list(self.CONTACTS)
        service.delete_contact = lambda cid: (deleted.append(cid), True)[1]

        with mock.patch.object(graph_contacts, "is_sync_enabled", return_value=True):
            stats = service.delete_all_contacts_from_outlook(include_foreign=True)

        assert len(deleted) == 4
        assert stats["skipped"] == 0


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------


class TestRetryBudget:
    """post_save runs this on the web request thread, so an unbounded
    Retry-After would park a user's profile save for the full wait."""

    def test_sleeps_are_capped_by_the_budget(self, service):
        service.retry_budget = 5.0
        throttled = _response(429, headers={"Retry-After": "60"})

        with mock.patch("requests.request", return_value=throttled) as request:
            with mock.patch.object(graph_contacts.time, "sleep") as sleep:
                response = service._request_with_retry("get", "https://example.test")

        assert response.status_code == 429
        sleep.assert_not_called()  # 60s > 5s budget, so give up immediately
        assert request.call_count == 1

    def test_retries_within_budget(self, service):
        service.retry_budget = 30.0
        responses = [
            _response(429, headers={"Retry-After": "2"}),
            _response(429, headers={"Retry-After": "2"}),
            _response(200, {"ok": True}),
        ]

        with mock.patch("requests.request", side_effect=responses):
            with mock.patch.object(graph_contacts.time, "sleep") as sleep:
                response = service._request_with_retry("get", "https://example.test")

        assert response.status_code == 200
        assert sleep.call_count == 2

    def test_non_429_is_returned_without_retrying(self, service):
        with mock.patch(
            "requests.request", return_value=_response(404)
        ) as request:
            response = service._request_with_retry("get", "https://example.test")

        assert response.status_code == 404
        assert request.call_count == 1


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------


class TestListContacts:
    def test_list_selects_only_needed_fields(self, service):
        """Without $select Graph returns every property of every contact --
        a large multiple of the payload we use, against the mailbox budget."""
        page = {"value": [{"id": "a"}]}

        with mock.patch.object(graph_contacts, "is_sync_enabled", return_value=True):
            with mock.patch(
                "requests.request", return_value=_response(200, page)
            ) as request:
                contacts = service.list_all_contacts_from_outlook()

        assert contacts == [{"id": "a"}]
        params = request.call_args.kwargs["params"]
        assert "$select" in params
        assert "mobilePhone" in params["$select"]

    def test_pagination_does_not_resend_params(self, service):
        """The @odata.nextLink already encodes the query; re-appending $select
        to it is at best redundant and at worst a 400."""
        pages = [
            _response(200, {"value": [{"id": "a"}], "@odata.nextLink": "https://next"}),
            _response(200, {"value": [{"id": "b"}]}),
        ]

        with mock.patch.object(graph_contacts, "is_sync_enabled", return_value=True):
            with mock.patch("requests.request", side_effect=pages) as request:
                contacts = service.list_all_contacts_from_outlook()

        assert [c["id"] for c in contacts] == ["a", "b"]
        assert request.call_args_list[1].kwargs["params"] is None


# ---------------------------------------------------------------------------
# Token caching
# ---------------------------------------------------------------------------


class TestMsalAppCaching:
    def test_app_is_reused_across_calls(self):
        """A fresh ConfidentialClientApplication per call has an empty token
        cache, so acquire_token_silent always misses and every Graph operation
        pays a round trip to login.microsoftonline.com."""
        graph_contacts._MSAL_APP_CACHE.clear()
        fake_msal = mock.Mock()
        fake_msal.ConfidentialClientApplication.side_effect = (
            lambda *a, **kw: mock.Mock()
        )

        with mock.patch.dict("sys.modules", {"msal": fake_msal}):
            first = graph_contacts._get_msal_app("t", "c", "s")
            second = graph_contacts._get_msal_app("t", "c", "s")

        assert first is second
        assert fake_msal.ConfidentialClientApplication.call_count == 1
        graph_contacts._MSAL_APP_CACHE.clear()

    def test_distinct_tenants_get_distinct_apps(self):
        graph_contacts._MSAL_APP_CACHE.clear()
        fake_msal = mock.Mock()
        fake_msal.ConfidentialClientApplication.side_effect = (
            lambda *a, **kw: mock.Mock()
        )

        with mock.patch.dict("sys.modules", {"msal": fake_msal}):
            first = graph_contacts._get_msal_app("tenant-a", "c", "s")
            second = graph_contacts._get_msal_app("tenant-b", "c", "s")

        assert first is not second
        graph_contacts._MSAL_APP_CACHE.clear()


# ---------------------------------------------------------------------------
# Environment guard
# ---------------------------------------------------------------------------


class TestSyncEnabledGuard:
    def test_sync_is_disabled_under_pytest(self):
        """Defense in depth: nothing in the test suite may reach the live
        shared mailbox, whatever else is mocked."""
        assert graph_contacts.is_sync_enabled() is False
