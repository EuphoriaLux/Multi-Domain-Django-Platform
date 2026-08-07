"""Tests for the echo.lu event sync.

Covers the three things that decide whether this integration is safe to leave
running unattended:

* eligibility — a private or unpublished event must never reach a public
  national portal;
* the payload mapping — echo.lu rejects an experience wholesale on bad input,
  and renders whatever it accepts verbatim;
* the id bookkeeping — losing the server-assigned experience id creates a
  duplicate listing we can no longer reach.

No network: every test drives a fake client, so a failure here is always a
bug in our mapping and never echo.lu being slow.

Run with: pytest crush_lu/tests/test_echo_lu_sync.py -v
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import MeetupEvent
from crush_lu.models.echo_lu import EchoExperienceSync
from crush_lu.services import echo_lu


ENABLED = {"ECHO_LU_SYNC_ENABLED": True, "ECHO_LU_API_KEY": "test-key"}


def make_event(**overrides):
    start = timezone.now() + timedelta(days=7)
    defaults = {
        "title": "Speed Dating Luxembourg",
        "description": "An evening of short dates.",
        "event_type": "speed_dating",
        "location": "Café Konrad",
        "address": "7, rue du Nord\nL-2229 Luxembourg",
        "canton": "Luxembourg",
        "date_time": start,
        "duration_minutes": 120,
        "registration_deadline": start - timedelta(days=1),
        "is_published": True,
    }
    defaults.update(overrides)
    return MeetupEvent.objects.create(**defaults)


class FakeClient:
    """Records calls and returns canned responses, in place of EchoLuClient."""

    def __init__(self, create_response=None, error=None):
        self.create_response = create_response or {"id": "exp-123"}
        self.error = error
        self.calls = []

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if self.error:
            raise self.error

    def create_experience(self, payload):
        self._record("create", payload)
        return self.create_response

    def update_experience(self, experience_id, payload):
        self._record("update", experience_id, payload)
        return {"id": experience_id}

    def cancel_experience(self, experience_id):
        self._record("cancel", experience_id)
        return {}

    def unpublish_experience(self, experience_id):
        self._record("unpublish", experience_id)
        return {}

    def delete_experience(self, experience_id):
        self._record("delete", experience_id)
        return {}


class ShouldPublishTests(TestCase):
    """A public national listing is the wrong place to be permissive."""

    def test_published_upcoming_public_event_qualifies(self):
        self.assertTrue(echo_lu.should_publish(make_event()))

    def test_unpublished_event_does_not(self):
        self.assertFalse(echo_lu.should_publish(make_event(is_published=False)))

    def test_cancelled_event_does_not(self):
        self.assertFalse(echo_lu.should_publish(make_event(is_cancelled=True)))

    def test_private_invitation_event_does_not(self):
        # The failure mode this guards is publishing an invitation-only event
        # to the entire country.
        event = make_event(is_private_invitation=True, invitation_code="secret-1")
        self.assertFalse(echo_lu.should_publish(event))

    def test_finished_event_does_not(self):
        start = timezone.now() - timedelta(hours=5)
        event = make_event(
            date_time=start,
            duration_minutes=120,
            registration_deadline=start - timedelta(days=1),
        )
        self.assertFalse(echo_lu.should_publish(event))

    def test_event_still_running_does(self):
        start = timezone.now() - timedelta(minutes=30)
        event = make_event(
            date_time=start,
            duration_minutes=120,
            registration_deadline=start - timedelta(days=1),
        )
        self.assertTrue(echo_lu.should_publish(event))


class AddressParsingTests(TestCase):
    """Best-effort, never-guessing: a wrong house number sends people astray."""

    def test_luxembourg_style_leading_number(self):
        parsed = echo_lu.parse_address("7, rue du Nord\nL-2229 Luxembourg")
        self.assertEqual(parsed["number"], "7")
        self.assertEqual(parsed["street"], "rue du Nord")
        self.assertEqual(parsed["postcode"], "2229")
        self.assertEqual(parsed["town"], "Luxembourg")
        self.assertEqual(parsed["country"], "Luxembourg")

    def test_trailing_number(self):
        parsed = echo_lu.parse_address("Grand-Rue 42\nL-1660 Luxembourg")
        self.assertEqual(parsed["number"], "42")
        self.assertEqual(parsed["street"], "Grand-Rue")

    def test_single_line_address(self):
        parsed = echo_lu.parse_address("12A avenue de la Gare, L-1610 Luxembourg")
        self.assertEqual(parsed["number"], "12A")
        self.assertEqual(parsed["street"], "avenue de la Gare")
        self.assertEqual(parsed["postcode"], "1610")

    def test_unparseable_address_keeps_the_full_line(self):
        # The listing must never be less informative than what we hold, so an
        # address we cannot split still travels intact in `street`.
        parsed = echo_lu.parse_address("Behind the old brewery", canton="Esch")
        self.assertEqual(parsed["street"], "Behind the old brewery")
        self.assertEqual(parsed["number"], "")
        self.assertEqual(parsed["postcode"], "")
        self.assertEqual(parsed["town"], "Esch")

    def test_empty_address_does_not_crash(self):
        parsed = echo_lu.parse_address("", canton="Luxembourg")
        self.assertEqual(parsed["street"], "")
        self.assertEqual(parsed["town"], "Luxembourg")


@override_settings(**ENABLED)
class PayloadTests(TestCase):
    def test_core_fields_map_across(self):
        event = make_event()
        payload = echo_lu.build_experience_payload(event)

        self.assertEqual(payload["title"], "Speed Dating Luxembourg")
        self.assertEqual(payload["description"], "An evening of short dates.")
        self.assertEqual(payload["subtitle"], "Speed Dating")
        self.assertEqual(payload["venues"], ["Café Konrad"])
        self.assertEqual(payload["location"]["address"]["postcode"], "2229")

    def test_dates_are_rfc3339_utc_and_span_the_duration(self):
        event = make_event(duration_minutes=90)
        entry = echo_lu.build_experience_payload(event)["dates"][0]

        self.assertTrue(entry["from"].endswith("Z"))
        self.assertTrue(entry["to"].endswith("Z"))
        self.assertEqual(entry["from"], echo_lu._rfc3339(event.date_time))
        self.assertEqual(entry["to"], echo_lu._rfc3339(event.end_time))

    def test_duration_is_not_sent(self):
        # Its unit is undocumented and from/to already pin the span; a wrong
        # unit would contradict them on the public listing.
        entry = echo_lu.build_experience_payload(make_event())["dates"][0]
        self.assertNotIn("duration", entry)

    def test_free_event_gets_an_explicit_zero_price_ticket(self):
        # No tickets at all reads as "price unknown", which costs signups.
        tickets = echo_lu.build_experience_payload(make_event())["tickets"]
        self.assertEqual(tickets, [{"title": "Free entry", "price": 0, "currency": "EUR"}])

    def test_paid_event_sends_the_fee(self):
        event = make_event(registration_fee=Decimal("15.50"))
        tickets = echo_lu.build_experience_payload(event)["tickets"]
        self.assertEqual(tickets[0]["price"], 15.50)
        self.assertEqual(tickets[0]["currency"], "EUR")

    def test_coordinates_are_omitted_when_unknown(self):
        address = echo_lu.build_experience_payload(make_event())["location"]["address"]
        self.assertNotIn("latitude", address)
        self.assertNotIn("longitude", address)

    def test_coordinates_are_strings_when_known(self):
        event = make_event(
            latitude=Decimal("49.611622"), longitude=Decimal("6.131935")
        )
        address = echo_lu.build_experience_payload(event)["location"]["address"]
        self.assertEqual(address["latitude"], "49.611622")
        self.assertEqual(address["longitude"], "6.131935")

    def test_purchase_link_points_at_the_event_page(self):
        event = make_event()
        payload = echo_lu.build_experience_payload(event)
        self.assertEqual(
            payload["dates"][0]["purchaseLink"],
            f"https://crush.lu/en/events/{event.pk}/",
        )

    @override_settings(ECHO_LU_CONTACT_WEBSITE="https://crush.lu")
    def test_contact_website_uses_the_configured_organiser_site(self):
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(payload["contact"]["website"], "https://crush.lu")

    @override_settings(ECHO_LU_CONTACT_WEBSITE="")
    def test_contact_website_falls_back_to_the_event_page(self):
        # Better than sending nothing: readers still get somewhere useful.
        event = make_event()
        payload = echo_lu.build_experience_payload(event)
        self.assertEqual(
            payload["contact"]["website"], f"https://crush.lu/en/events/{event.pk}/"
        )

    def test_blank_contact_fields_are_dropped(self):
        # An empty string is a supplied value to echo.lu and renders as a blank
        # contact line.
        with override_settings(ECHO_LU_CONTACT_PHONE=""):
            contact = echo_lu.build_experience_payload(make_event())["contact"]
        self.assertNotIn("phone", contact)

    def test_taxonomy_facets_are_omitted_when_unconfigured(self):
        # Guessing a slug gets the whole experience rejected, so empty means
        # "send nothing".
        payload = echo_lu.build_experience_payload(make_event())
        for facet in ("categories", "audiences", "formats", "environments"):
            self.assertNotIn(facet, payload)

    @override_settings(
        ECHO_LU_DEFAULT_CATEGORIES="nightlife",
        ECHO_LU_CATEGORY_MAP='{"speed_dating": ["rencontres"]}',
    )
    def test_event_type_categories_layer_on_the_defaults(self):
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(payload["categories"], ["nightlife", "rencontres"])

    @override_settings(ECHO_LU_CATEGORY_MAP="{not json")
    def test_broken_category_map_falls_back_instead_of_crashing(self):
        with override_settings(ECHO_LU_DEFAULT_CATEGORIES="nightlife"):
            payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(payload["categories"], ["nightlife"])

    def test_english_title_wins_over_the_active_language(self):
        # This runs from commands and background tasks where the active
        # language is whatever LANGUAGE_CODE happens to be.
        event = make_event()
        event.title_en = "English title"
        event.title_fr = "Titre français"
        event.save()
        payload = echo_lu.build_experience_payload(event)
        self.assertEqual(payload["title"], "English title")

    def test_falls_back_when_english_is_missing(self):
        event = make_event()
        event.title_en = ""
        event.title_fr = "Titre français"
        event.save()
        payload = echo_lu.build_experience_payload(event)
        self.assertEqual(payload["title"], "Titre français")


class FingerprintTests(TestCase):
    def test_key_order_does_not_change_the_hash(self):
        left = echo_lu.payload_fingerprint({"a": 1, "b": [1, 2]})
        right = echo_lu.payload_fingerprint({"b": [1, 2], "a": 1})
        self.assertEqual(left, right)

    def test_content_change_changes_the_hash(self):
        left = echo_lu.payload_fingerprint({"title": "A"})
        right = echo_lu.payload_fingerprint({"title": "B"})
        self.assertNotEqual(left, right)


class ExtractExperienceIdTests(TestCase):
    """The response schema is not fully published; accept every spelling."""

    def test_top_level_id(self):
        self.assertEqual(echo_lu.extract_experience_id({"id": "abc"}), "abc")

    def test_camel_case_id(self):
        self.assertEqual(
            echo_lu.extract_experience_id({"experienceId": "abc"}), "abc"
        )

    def test_nested_in_data(self):
        self.assertEqual(
            echo_lu.extract_experience_id({"data": {"_id": "abc"}}), "abc"
        )

    def test_absent_id_returns_empty(self):
        self.assertEqual(echo_lu.extract_experience_id({"ok": True}), "")

    def test_non_dict_returns_empty(self):
        self.assertEqual(echo_lu.extract_experience_id(["abc"]), "")


@override_settings(**ENABLED)
class SyncEventTests(TestCase):
    def test_first_sync_creates_and_stores_the_id(self):
        event = make_event()
        client = FakeClient(create_response={"id": "exp-123"})

        self.assertEqual(echo_lu.sync_event(event, client=client), "created")

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.experience_id, "exp-123")
        self.assertEqual(sync.status, EchoExperienceSync.Status.SYNCED)
        self.assertEqual(client.calls[0][0], "create")

    def test_second_sync_updates_rather_than_creating_a_duplicate(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.title = "Renamed"
        event.save()
        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "updated")

        self.assertEqual([call[0] for call in client.calls], ["create", "update"])
        self.assertEqual(EchoExperienceSync.objects.filter(event=event).count(), 1)

    def test_unchanged_payload_costs_no_api_call(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "unchanged")
        self.assertEqual(len(client.calls), 1)

    def test_force_resends_an_unchanged_payload(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.refresh_from_db()
        self.assertEqual(
            echo_lu.sync_event(event, client=client, force=True), "updated"
        )
        self.assertEqual(len(client.calls), 2)

    def test_rejection_is_recorded_and_re_raised(self):
        event = make_event()
        client = FakeClient(
            error=echo_lu.EchoLuError("bad slug", status_code=422, body={"e": 1})
        )

        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.status, EchoExperienceSync.Status.FAILED)
        self.assertIn("bad slug", sync.last_error)
        self.assertEqual(sync.payload_hash, "")

    def test_failure_does_not_poison_the_retry(self):
        # mark_failure must leave payload_hash describing what echo.lu last
        # ACCEPTED — writing the rejected payload's hash would make every
        # retry a no-op forever.
        event = make_event()
        good = FakeClient()
        echo_lu.sync_event(event, client=good)
        accepted_hash = EchoExperienceSync.objects.get(event=event).payload_hash

        event.title = "Renamed"
        event.save()
        event.refresh_from_db()
        bad = FakeClient(error=echo_lu.EchoLuError("nope", status_code=500))
        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=bad)

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.payload_hash, accepted_hash)

        retry = FakeClient()
        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=retry), "updated")

    def test_create_without_an_id_fails_loudly(self):
        # A 2xx with no id leaves an orphan listing. Storing a blank id would
        # create a fresh duplicate on every later sync.
        event = make_event()
        client = FakeClient(create_response={"ok": True})

        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.status, EchoExperienceSync.Status.FAILED)
        self.assertEqual(sync.experience_id, "")

    def test_unpublishing_withdraws_the_listing(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.is_published = False
        event.save()
        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "withdrawn")

        self.assertEqual(client.calls[-1], ("unpublish", "exp-123"))
        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.status, EchoExperienceSync.Status.WITHDRAWN)
        # The id survives so re-publishing reuses the same listing.
        self.assertEqual(sync.experience_id, "exp-123")

    def test_cancelling_cancels_rather_than_unpublishes(self):
        # Somebody who already saw the listing needs to see a cancellation,
        # not a listing that quietly vanished.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.is_cancelled = True
        event.save()
        event.refresh_from_db()
        echo_lu.sync_event(event, client=client)

        self.assertEqual(client.calls[-1], ("cancel", "exp-123"))

    def test_republishing_reuses_the_same_experience(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        event.is_published = False
        event.save()
        event.refresh_from_db()
        echo_lu.sync_event(event, client=client)

        event.is_published = True
        event.save()
        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "updated")

        self.assertEqual(
            [call[0] for call in client.calls],
            ["create", "unpublish", "update"],
        )

    def test_ineligible_event_never_listed_is_skipped(self):
        event = make_event(is_published=False)
        client = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=client), "skipped")
        self.assertEqual(client.calls, [])

    def test_already_withdrawn_event_is_not_withdrawn_twice(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        event.is_published = False
        event.save()
        event.refresh_from_db()
        echo_lu.sync_event(event, client=client)

        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "skipped")
        self.assertEqual(len(client.calls), 2)

    def test_dry_run_touches_neither_the_api_nor_the_database(self):
        event = make_event()
        client = FakeClient()
        self.assertEqual(
            echo_lu.sync_event(event, client=client, dry_run=True), "created"
        )
        self.assertEqual(client.calls, [])
        self.assertFalse(EchoExperienceSync.objects.exists())


class DisabledByDefaultTests(TestCase):
    """A restored production DB must not be able to mutate live listings."""

    def test_sync_is_off_without_the_flag(self):
        with override_settings(ECHO_LU_SYNC_ENABLED=False, ECHO_LU_API_KEY="k"):
            self.assertFalse(echo_lu.is_sync_enabled())

    def test_sync_is_off_without_a_key(self):
        with override_settings(ECHO_LU_SYNC_ENABLED=True, ECHO_LU_API_KEY=""):
            self.assertFalse(echo_lu.is_sync_enabled())

    @override_settings(ECHO_LU_SYNC_ENABLED=False, ECHO_LU_API_KEY="k")
    def test_sync_event_short_circuits_when_disabled(self):
        event = make_event()
        client = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=client), "disabled")
        self.assertEqual(client.calls, [])

    @override_settings(ECHO_LU_API_KEY="")
    def test_client_refuses_to_send_an_empty_key(self):
        # Otherwise echo.lu answers a bare 401 that looks exactly like a
        # revoked key, and the hunt starts in the wrong place.
        with self.assertRaises(echo_lu.EchoLuNotConfigured):
            echo_lu.EchoLuClient()._headers()


@override_settings(**ENABLED)
class EventsNeedingSyncTests(TestCase):
    def test_includes_publishable_events(self):
        event = make_event()
        self.assertIn(event, echo_lu.events_needing_sync())

    def test_includes_listed_events_that_no_longer_qualify(self):
        # The take-down direction: without this, unpublishing an event would
        # leave it on the portal forever.
        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())
        MeetupEvent.objects.filter(pk=event.pk).update(is_published=False)

        self.assertIn(event, echo_lu.events_needing_sync())

    def test_excludes_never_listed_ineligible_events(self):
        event = make_event(is_published=False)
        self.assertNotIn(event, echo_lu.events_needing_sync())

    def test_excludes_already_withdrawn_events(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        MeetupEvent.objects.filter(pk=event.pk).update(is_published=False)
        event.refresh_from_db()
        echo_lu.sync_event(event, client=client)

        self.assertNotIn(event, echo_lu.events_needing_sync())


@override_settings(**ENABLED)
class ClientTransportTests(TestCase):
    """The bits of _request that are easy to get wrong and hard to notice."""

    def _response(self, status=200, json_body=None, text="", headers=None):
        response = mock.Mock()
        response.status_code = status
        response.headers = headers or {}
        response.content = b"x" if (json_body is not None or text) else b""
        response.text = text
        response.json.return_value = json_body if json_body is not None else {}
        if json_body is None and not text:
            response.json.side_effect = ValueError("no body")
        return response

    def test_api_key_header_is_sent(self):
        with mock.patch("crush_lu.services.echo_lu.requests.request") as request:
            request.return_value = self._response(json_body={"id": "1"})
            echo_lu.EchoLuClient().create_experience({"title": "x"})

        headers = request.call_args.kwargs["headers"]
        self.assertEqual(headers["api-key"], "test-key")

    def test_base_url_trailing_slash_is_tolerated(self):
        client = echo_lu.EchoLuClient(base_url="https://api.echo.lu/v1/")
        with mock.patch("crush_lu.services.echo_lu.requests.request") as request:
            request.return_value = self._response(json_body={"id": "1"})
            client.create_experience({})

        self.assertEqual(
            request.call_args.args[1], "https://api.echo.lu/v1/experiences"
        )

    def test_client_error_is_not_retried(self):
        # Replaying a 422 just burns the same rejection three times.
        with mock.patch("crush_lu.services.echo_lu.requests.request") as request:
            request.return_value = self._response(status=422, json_body={"e": "bad"})
            with self.assertRaises(echo_lu.EchoLuError) as caught:
                echo_lu.EchoLuClient().create_experience({})

        self.assertEqual(request.call_count, 1)
        self.assertEqual(caught.exception.status_code, 422)

    def test_server_error_is_retried_then_raised(self):
        with mock.patch("crush_lu.services.echo_lu.requests.request") as request, \
                mock.patch("crush_lu.services.echo_lu.time.sleep"):
            request.return_value = self._response(status=503)
            with self.assertRaises(echo_lu.EchoLuError):
                echo_lu.EchoLuClient().create_experience({})

        self.assertGreater(request.call_count, 1)

    def test_retry_succeeds_on_the_second_attempt(self):
        with mock.patch("crush_lu.services.echo_lu.requests.request") as request, \
                mock.patch("crush_lu.services.echo_lu.time.sleep"):
            request.side_effect = [
                self._response(status=429, headers={"Retry-After": "1"}),
                self._response(json_body={"id": "exp-9"}),
            ]
            result = echo_lu.EchoLuClient().create_experience({})

        self.assertEqual(result, {"id": "exp-9"})

    def test_empty_204_is_not_an_error(self):
        # DELETE answers with no body; treating that as a failure would leave
        # the sync row stuck.
        with mock.patch("crush_lu.services.echo_lu.requests.request") as request:
            request.return_value = self._response(status=204)
            self.assertEqual(echo_lu.EchoLuClient().delete_experience("x"), {})

    def test_error_body_is_carried_for_the_admin(self):
        with mock.patch("crush_lu.services.echo_lu.requests.request") as request:
            request.return_value = self._response(
                status=400, json_body={"message": "unknown category 'nope'"}
            )
            with self.assertRaises(echo_lu.EchoLuError) as caught:
                echo_lu.EchoLuClient().create_experience({})

        self.assertIn("unknown category", str(caught.exception))


class SyncCommandTests(TestCase):
    """The command is the scheduled entry point, so its guards matter."""

    def _run(self, *args, **kwargs):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("sync_events_to_echo", *args, stdout=out, stderr=out, **kwargs)
        return out.getvalue()

    @override_settings(ECHO_LU_SYNC_ENABLED=False, ECHO_LU_API_KEY="")
    def test_refuses_to_run_silently_when_disabled(self):
        # A scheduled job reporting "0 synced" is indistinguishable from
        # "nothing to do", which is how a missing key hides for weeks.
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run()

    @override_settings(ECHO_LU_SYNC_ENABLED=False, ECHO_LU_API_KEY="")
    def test_dry_run_works_without_credentials(self):
        make_event()
        output = self._run("--dry-run")
        self.assertIn("Would sync", output)

    @override_settings(**ENABLED)
    def test_withdraw_requires_an_explicit_target(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run("--withdraw")

    @override_settings(**ENABLED)
    def test_one_rejection_does_not_stop_the_rest(self):
        good = make_event(title="Good")
        bad = make_event(title="Bad")

        def fake_sync(event, client=None, force=False, dry_run=False):
            if event.pk == bad.pk:
                raise echo_lu.EchoLuError("rejected", status_code=422)
            return "created"

        with mock.patch.object(echo_lu, "sync_event", side_effect=fake_sync), \
                mock.patch.object(echo_lu, "EchoLuClient"):
            output = self._run()

        self.assertIn("Good", output)
        self.assertIn("rejected", output)

    @override_settings(**ENABLED)
    def test_unknown_event_id_is_an_error(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run("--event-id", "999999")


class TaxonomySlugExtractionTests(TestCase):
    """The vocabulary endpoints differ between sandbox and production."""

    def _extract(self, response):
        from crush_lu.management.commands.echo_taxonomy import _extract_slugs

        return _extract_slugs(response)

    def test_list_of_strings(self):
        self.assertEqual(self._extract(["music", "sport"]), {"music": "", "sport": ""})

    def test_list_of_objects(self):
        self.assertEqual(
            self._extract([{"slug": "music", "name": "Music"}]), {"music": "Music"}
        )

    def test_data_envelope(self):
        self.assertEqual(
            self._extract({"data": [{"id": "music", "label": "Music"}]}),
            {"music": "Music"},
        )

    def test_translated_labels(self):
        self.assertEqual(
            self._extract([{"slug": "music", "name": {"fr": "Musique", "en": "Music"}}]),
            {"music": "Music"},
        )

    def test_plain_mapping(self):
        self.assertEqual(self._extract({"music": "Music"}), {"music": "Music"})


class AuditTests(TestCase):
    """Finding listings echo.lu holds that we cannot address."""

    def _entries(self, response):
        from crush_lu.management.commands.sync_events_to_echo import (
            _experience_entries,
        )

        return _experience_entries(response)

    def test_bare_list(self):
        self.assertEqual(
            self._entries([{"id": "exp-1", "title": "Night"}]), [("exp-1", "Night")]
        )

    def test_data_envelope(self):
        self.assertEqual(
            self._entries({"data": [{"_id": "exp-1", "title": "Night"}]}),
            [("exp-1", "Night")],
        )

    def test_translated_title(self):
        self.assertEqual(
            self._entries([{"id": "exp-1", "title": {"fr": "Nuit", "en": "Night"}}]),
            [("exp-1", "Night")],
        )

    def test_entries_without_an_id_are_dropped(self):
        self.assertEqual(self._entries([{"title": "Night"}]), [])

    def test_unrecognised_envelope_yields_nothing(self):
        self.assertEqual(self._entries({"unexpected": 1}), [])

    @override_settings(**ENABLED)
    def test_audit_flags_an_untracked_listing(self):
        from io import StringIO

        from django.core.management import call_command

        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())  # tracked as exp-123

        out = StringIO()
        with mock.patch.object(
            echo_lu.EchoLuClient,
            "list_experiences",
            return_value=[
                {"id": "exp-123", "title": "Ours"},
                {"id": "exp-999", "title": "Orphan"},
            ],
        ):
            call_command("sync_events_to_echo", "--audit", stdout=out)

        output = out.getvalue()
        self.assertIn("UNTRACKED", output)
        self.assertIn("exp-999", output)
        self.assertNotIn("exp-123  UNTRACKED", output)

    @override_settings(**ENABLED)
    def test_audit_flags_a_listing_that_vanished_upstream(self):
        from io import StringIO

        from django.core.management import call_command

        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())

        out = StringIO()
        with mock.patch.object(
            echo_lu.EchoLuClient, "list_experiences", return_value=[]
        ):
            call_command("sync_events_to_echo", "--audit", stdout=out)

        self.assertIn("tracked but not", out.getvalue())
