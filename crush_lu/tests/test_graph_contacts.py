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

    def test_unnormalizable_phone_clears_the_number(self, service, profile):
        """Explicit null, not an omitted key. Graph PATCH is a merge, so
        omitting mobilePhone would leave the previously synced number live --
        and caller ID would keep resolving a number this member no longer has,
        which by then may belong to somebody else."""
        profile.phone_number = "621123456"

        payload = service._build_contact_payload(profile)

        assert "mobilePhone" in payload
        assert payload["mobilePhone"] is None

    def test_blank_phone_also_clears_rather_than_omitting(self, service, profile):
        profile.phone_number = ""

        payload = service._build_contact_payload(profile)

        assert payload["mobilePhone"] is None

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

    def test_update_survives_an_unmigrated_legacy_id(self, service, profile):
        """The critical deploy-ordering case. Rows written before this change
        still hold restIds; if the immutable-dialect 404 were believed, the ID
        would be cleared and sync_profile would create a SECOND contact -- for
        every pre-existing profile, on the first nightly run after deploy."""
        from crush_lu.models import CrushProfile

        profile.outlook_contact_id = "LEGACY"
        profile.save(update_fields=["outlook_contact_id"])
        responses = [
            _response(404),  # ETag GET, immutable dialect -> not found
            _response(200, {"id": "LEGACY"}, {"ETag": 'W/"1"'}),  # legacy retry
            _response(404),  # PATCH, immutable dialect
            _response(204),  # PATCH, legacy retry
        ]

        with mock.patch("requests.request", side_effect=responses):
            assert service.update_contact(profile, force=True) is True

        # The ID must survive: clearing it is what causes the duplicate.
        assert CrushProfile.objects.get(pk=profile.pk).outlook_contact_id == "LEGACY"

    def test_update_still_clears_when_both_dialects_404(self, service, profile):
        """The fallback must not mask a genuinely deleted contact -- that one
        does need clearing so it gets recreated."""
        from crush_lu.models import CrushProfile

        profile.outlook_contact_id = "GONE"
        profile.save(update_fields=["outlook_contact_id"])
        responses = [_response(404), _response(404)]

        with mock.patch("requests.request", side_effect=responses):
            assert service.update_contact(profile, force=True) is False

        assert CrushProfile.objects.get(pk=profile.pk).outlook_contact_id == ""

    def test_delete_sends_the_immutable_id_header(self, service):
        """The ID being deleted came from create/backfill/list, so it is an
        immutable ID. Without the header Graph can 404 -- and delete_contact
        treats 404 as success, silently leaving the contact and its PII in the
        shared mailbox."""
        with mock.patch(
            "requests.request", return_value=_response(204)
        ) as request:
            assert service.delete_contact("IMMUTABLE", force=True) is True

        assert request.call_args.kwargs["headers"]["Prefer"] == IMMUTABLE_ID_PREFER

    def test_delete_falls_back_to_the_legacy_dialect(self, service):
        """A stored ID is only immutable once the backfill has run. Believing
        the first 404 would report a clean delete while the contact -- and its
        PII -- stayed in the shared mailbox."""
        responses = [_response(404), _response(204)]

        with mock.patch("requests.request", side_effect=responses) as request:
            assert service.delete_contact("LEGACY", force=True) is True

        assert request.call_count == 2
        assert "Prefer" in request.call_args_list[0].kwargs["headers"]
        assert "Prefer" not in request.call_args_list[1].kwargs["headers"]

    def test_delete_reports_gone_only_when_both_dialects_404(self, service):
        responses = [_response(404), _response(404)]

        with mock.patch("requests.request", side_effect=responses) as request:
            assert service.delete_contact("REALLY-GONE", force=True) is True

        assert request.call_count == 2

    def test_photo_upload_sends_the_immutable_id_header(self, service):
        """Same dialect problem on the photo PUT: a 404 here means every new
        profile syncs its contact but never gets a picture, and the key is not
        remembered so the full sync retries the failure forever."""
        profile = SimpleNamespace(
            pk=1,
            photo_1=SimpleNamespace(
                name="users/1/photos/abc.jpg",
                read=lambda: b"jpegbytes",
                seek=lambda _: None,
            ),
            outlook_photo_key="",
        )

        with mock.patch("requests.request", return_value=_response(200)) as request:
            with mock.patch.object(GraphContactsService, "_remember_photo_key"):
                service._upload_contact_photo("IMMUTABLE", profile, "token")

        assert request.call_args.kwargs["headers"]["Prefer"] == IMMUTABLE_ID_PREFER

    def test_conflict_retry_clears_the_id_when_the_contact_vanished(
        self, service, profile
    ):
        """A contact can be deleted between the conflicting write and the 412
        retry. If the dead ID is not cleared the profile is stranded: sync_profile
        keeps choosing update over create, and update keeps 404ing."""
        from crush_lu.models import CrushProfile

        profile.outlook_contact_id = "GONE"
        profile.save(update_fields=["outlook_contact_id"])
        responses = [
            _response(200, {"id": "GONE"}, {"ETag": 'W/"1"'}),  # first ETag GET
            _response(412),  # PATCH -> concurrency conflict
            _response(404),  # retry ETag GET, immutable dialect
            _response(404),  # retry ETag GET, legacy dialect -> really gone
        ]

        with mock.patch("requests.request", side_effect=responses):
            assert service.update_contact(profile, force=True) is False

        assert CrushProfile.objects.get(pk=profile.pk).outlook_contact_id == ""

    def test_conflict_retry_clears_the_id_when_the_patch_404s(self, service, profile):
        """Same stranding, one step later: the retry GET succeeds but the
        contact is deleted before the retry PATCH lands."""
        from crush_lu.models import CrushProfile

        profile.outlook_contact_id = "GONE"
        profile.save(update_fields=["outlook_contact_id"])
        responses = [
            _response(200, {"id": "GONE"}, {"ETag": 'W/"1"'}),
            _response(412),
            _response(200, {"id": "GONE"}, {"ETag": 'W/"2"'}),
            _response(404),  # retry PATCH, immutable dialect
            _response(404),  # retry PATCH, legacy dialect -> really gone
        ]

        with mock.patch("requests.request", side_effect=responses):
            assert service.update_contact(profile, force=True) is False

        assert CrushProfile.objects.get(pk=profile.pk).outlook_contact_id == ""

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


@pytest.mark.django_db
class TestDeleteAllEndpointFlagParsing:
    """include_foreign is the only thing standing between this HTTP endpoint
    and every contact in a shared mailbox, so it must not be coerced --
    bool("false") and bool("0") are both True in Python."""

    # Literal path: the domain middleware swaps urlconf per host, so
    # reverse() would build a /crush/... path that 404s under crush.lu.
    URL = "/api/admin/sync-contacts/delete-all/"

    def _post(self, client, body):
        import json as _json

        with mock.patch(
            "crush_lu.api_admin_sync._authenticate_admin_request", return_value=True
        ), mock.patch(
            "crush_lu.api_admin_sync.is_sync_enabled", return_value=True
        ), mock.patch(
            "crush_lu.api_admin_sync.GraphContactsService"
        ) as service_cls:
            service_cls.return_value.delete_all_contacts_from_outlook.return_value = {
                "total": 0,
                "deleted": 0,
                "errors": 0,
                "skipped": 0,
            }
            client.post(
                self.URL,
                data=_json.dumps(body),
                content_type="application/json",
                HTTP_HOST="crush.lu",
            )
        return service_cls.return_value.delete_all_contacts_from_outlook

    @pytest.mark.parametrize("body", [{"include_foreign": "false"}, {"include_foreign": "0"}])
    def test_stringy_false_does_not_enable_the_destructive_path(self, client, body):
        called = self._post(client, body)

        called.assert_called_once_with(include_foreign=False)

    def test_json_true_enables_it(self, client):
        called = self._post(client, {"include_foreign": True})

        called.assert_called_once_with(include_foreign=True)

    def test_absent_flag_defaults_to_safe(self, client):
        called = self._post(client, {})

        called.assert_called_once_with(include_foreign=False)


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
# Signal wiring
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSignalWiring:
    """The post_save handler defers its Graph calls to on_commit. That is the
    one part of this change with no natural unit test, so it is pinned here
    rather than left to reasoning.

    Uses django_capture_on_commit_callbacks rather than transaction=True: a
    TransactionTestCase flushes the database, which destroys the seed rows the
    Connect data migrations install, and with --reuse-db in addopts that damage
    outlives the run and breaks the rest of the suite.
    """

    @pytest.fixture
    def profile(self, django_user_model):
        from crush_lu.models import CrushProfile

        user = django_user_model.objects.create_user(
            username="signal@crush.lu", email="signal@crush.lu"
        )
        return CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1990, 1, 1),
            is_approved=True,
            phone_number="+352621777777",
            phone_verified=True,
            outlook_contact_id="EXISTING",
        )

    @staticmethod
    def _patched_service():
        """Re-enable the signal path and hand back the stub service it uses."""
        stub = mock.Mock()
        stub.sync_profile.return_value = "EXISTING"
        stub.delete_contact.return_value = True
        return mock.patch.multiple(
            graph_contacts,
            is_sync_enabled=mock.Mock(return_value=True),
            GraphContactsService=mock.Mock(return_value=stub),
        ), stub

    def test_sync_does_not_fire_when_the_transaction_rolls_back(self, profile):
        """The whole point of the on_commit deferral: no contact is written for
        a profile whose save is undone. Has teeth because an inline (undeferred)
        Graph call would have landed before the rollback."""
        from django.db import transaction as db_transaction

        patcher, stub = self._patched_service()

        class Rollback(Exception):
            pass

        with patcher:
            with pytest.raises(Rollback):
                with db_transaction.atomic():
                    profile.location = "Esch-sur-Alzette"
                    profile.save()
                    raise Rollback()

        stub.sync_profile.assert_not_called()

    def test_sync_is_deferred_then_fires_on_commit(
        self, profile, django_capture_on_commit_callbacks
    ):
        patcher, stub = self._patched_service()

        with patcher:
            with django_capture_on_commit_callbacks() as callbacks:
                profile.location = "Differdange"
                profile.save()
                # Deferred: the save itself must not have touched Graph.
                stub.sync_profile.assert_not_called()

            assert len(callbacks) == 1
            for callback in callbacks:
                callback()

        stub.sync_profile.assert_called_once()

    @staticmethod
    def _write_bypassing_save(profile, **fields):
        """Apply a change the way the only paths that can reach this state do.

        CrushProfile.save() restores phone_number *and* phone_verified from the
        DB whenever the stored row is verified, so neither field can be cleared
        through save() at all. The paths that can are QuerySet.update() plus a
        hand-sent post_save -- exactly what _luxid_save_profile does.
        """
        from django.db.models.signals import post_save

        from crush_lu.models import CrushProfile

        CrushProfile.objects.filter(pk=profile.pk).update(**fields)
        for field, value in fields.items():
            setattr(profile, field, value)
        post_save.send(
            sender=CrushProfile,
            instance=profile,
            created=False,
            update_fields=frozenset(fields),
        )

    def test_clearing_the_phone_number_deletes_the_contact(
        self, profile, django_capture_on_commit_callbacks
    ):
        """The handler used to return early on a falsy phone_number, *before*
        the delete branch, so a profile whose number was wiped kept a live
        contact in the shared mailbox indefinitely."""
        from crush_lu.models import CrushProfile

        patcher, stub = self._patched_service()

        with patcher:
            with django_capture_on_commit_callbacks(execute=True):
                self._write_bypassing_save(profile, phone_number="")

        stub.delete_contact.assert_called_once_with("EXISTING")
        assert CrushProfile.objects.get(pk=profile.pk).outlook_contact_id == ""

    def test_unverifying_the_phone_still_deletes_the_contact(
        self, profile, django_capture_on_commit_callbacks
    ):
        patcher, stub = self._patched_service()

        with patcher:
            with django_capture_on_commit_callbacks(execute=True):
                self._write_bypassing_save(profile, phone_verified=False)

        stub.delete_contact.assert_called_once_with("EXISTING")


# ---------------------------------------------------------------------------
# Environment guard
# ---------------------------------------------------------------------------


class TestSyncEnabledGuard:
    def test_sync_is_disabled_under_pytest(self):
        """Defense in depth: nothing in the test suite may reach the live
        shared mailbox, whatever else is mocked."""
        assert graph_contacts.is_sync_enabled() is False
