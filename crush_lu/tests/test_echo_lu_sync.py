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
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu import signals as crush_signals
from crush_lu.models import MeetupEvent
from crush_lu.models.echo_lu import EchoExperienceSync
from crush_lu.services import echo_lu

ENABLED = {"ECHO_LU_SYNC_ENABLED": True, "ECHO_LU_API_KEY": "test-key"}


def _reset_echo_delete_burst():
    """Forget the delete take-down's wall-clock budget between tests."""
    for attribute in ("burst_started", "burst_last_finished"):
        crush_signals._echo_deletions.__dict__.pop(attribute, None)


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


def echo_response(record):
    """A response in echo.lu's real envelope: ``{"stats": …, "record": {…}}``.

    This suite used to hand back a bare ``{"id": …}`` — a shape the API never
    returns. Every test passed against it while the real thing would have
    parked each newly created listing as an untracked orphan. So the fake now
    speaks the documented envelope, and the bare spellings are pinned by their
    own fallback tests rather than being what the whole suite is built on.
    """
    return {"stats": {"start": 0, "end": 0, "duration": 1}, "record": record}


class FakeClient:
    """Records calls and returns canned responses, in place of EchoLuClient."""

    def __init__(self, create_response=None, error=None, venues=None):
        self.create_response = (
            create_response
            if create_response is not None
            else echo_response({"id": "exp-123"})
        )
        self.error = error
        # The venue registry this fake pretends echo.lu holds. It already
        # contains make_event's venue, because that is the ordinary case
        # against a national registry of 5k+ rows — the venue exists and we
        # match it, which costs no write. Pass `venues=[]` for the other case,
        # where we have to register one.
        self.venues = (
            list(venues)
            if venues is not None
            else [{"id": "venue-1", "title": "Café Konrad"}]
        )
        self.calls = []

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if self.error:
            raise self.error

    # --- venues ---
    # list_venues is deliberately NOT recorded: venue lookups are plumbing, and
    # threading them into every call-order assertion would bury the experience
    # calls those assertions exist to pin.
    def list_venues(self, **params):
        return {"stats": {}, "records": list(self.venues)}

    def create_venue(self, payload):
        self._record("create_venue", payload)
        venue_id = f"venue-{len(self.venues) + 1}"
        self.venues.append({"id": venue_id, "title": payload.get("title", "")})
        return echo_response({"id": venue_id})

    def create_experience(self, payload):
        self._record("create", payload)
        return self.create_response

    def update_experience(self, experience_id, payload):
        self._record("update", experience_id, payload)
        return echo_response({"id": experience_id})

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
        self.assertEqual(payload["location"]["address"]["postcode"], "2229")
        # `venues` carries echo.lu venue IDs, resolved by the caller against
        # echo.lu's own registry — never the venue name, which is what this
        # used to assert. The name goes nowhere near the field.
        self.assertEqual(payload["venues"], [])
        self.assertEqual(
            echo_lu.build_experience_payload(event, venue_ids=["venue-9"])["venues"],
            ["venue-9"],
        )

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
        self.assertEqual(
            tickets, [{"title": "Free entry", "price": 0, "currency": "EUR"}]
        )

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
        event = make_event(latitude=Decimal("49.611622"), longitude=Decimal("6.131935"))
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
    @override_settings(ECHO_LU_CONTACT_PHONE="+352123456")
    def test_contact_website_uses_the_configured_organiser_site(self):
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(payload["contact"]["website"], "https://crush.lu")

    @override_settings(ECHO_LU_CONTACT_WEBSITE="", ECHO_LU_CONTACT_PHONE="+352123456")
    def test_contact_website_falls_back_to_the_event_page(self):
        # Better than sending nothing: readers still get somewhere useful.
        event = make_event()
        payload = echo_lu.build_experience_payload(event)
        self.assertEqual(
            payload["contact"]["website"], f"https://crush.lu/en/events/{event.pk}/"
        )

    def test_a_partial_contact_is_omitted_entirely(self):
        # name/email/phone are all required *within* contact while contact
        # itself is optional, so a partial one is strictly worse than none: it
        # converts an optional block into a guaranteed 400. This used to send
        # whichever fields happened to be configured.
        with override_settings(ECHO_LU_CONTACT_PHONE=""):
            payload = echo_lu.build_experience_payload(make_event())
        self.assertNotIn("contact", payload)

    @override_settings(ECHO_LU_CONTACT_PHONE="+352123456")
    def test_a_complete_contact_is_sent(self):
        contact = echo_lu.build_experience_payload(make_event())["contact"]
        self.assertEqual({"name", "email", "phone", "company", "website"}, set(contact))
        self.assertTrue(all(contact.values()))

    @override_settings(
        ECHO_LU_DEFAULT_CATEGORIES="",
        ECHO_LU_DEFAULT_AUDIENCES="",
        ECHO_LU_DEFAULT_FORMATS="",
        ECHO_LU_DEFAULT_ENVIRONMENTS="",
    )
    def test_unconfigured_facets_travel_empty_and_are_reported_missing(self):
        # This used to assert the opposite — that an unconfigured facet was
        # omitted, "which is valid and safer than guessing". It is not valid:
        # all four are required, and POSTing without them answers 400 with
        # "Missing categories" and so on. So they travel as empty lists and
        # `missing_required_fields` names them before a round trip is spent.
        payload = echo_lu.build_experience_payload(make_event())
        for facet in ("categories", "audiences", "formats", "environments"):
            self.assertIn(facet, payload)
            self.assertEqual(payload[facet], [])
        self.assertEqual(
            set(echo_lu.missing_required_fields(payload))
            & {"categories", "audiences", "formats", "environments"},
            {"categories", "audiences", "formats", "environments"},
        )

    def test_a_complete_payload_reports_nothing_missing(self):
        # The settings defaults are real echo.lu ids, so an ordinary event
        # with a resolved venue is publishable as-is.
        payload = echo_lu.build_experience_payload(make_event(), venue_ids=["venue-1"])
        self.assertEqual(echo_lu.missing_required_fields(payload), [])

    def test_an_event_without_an_image_still_gets_a_picture(self):
        # `pictures` is required, so "no image" is a rejection rather than a
        # listing without a banner.
        # Asserted against the settings chain, not against the helper that
        # produced it — comparing the function under test to itself passes even
        # if both sides are wrong together.
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(
            payload["pictures"][0]["url"], settings.SOCIAL_PREVIEW_IMAGE_URL
        )
        self.assertTrue(payload["pictures"][0]["url"])

    def test_the_fallback_image_is_the_og_image(self):
        # Not a second hand-written branding URL. The first attempt at this
        # setting invented a path that 404s, and echo.lu fetches pictures
        # server-side — so a 404 is a rejected experience, not a missing
        # banner. Tying it to the og:image means one URL to keep true.
        #
        # Asserted through the payload, against whatever SOCIAL_PREVIEW_IMAGE_URL
        # resolves to in THIS environment. Comparing two settings constants
        # instead is what let the first version of this fix pass locally and
        # fail in CI: the URL is reassigned after the constant was bound (blob
        # storage here, the CDN domain in production.py), so the snapshot and
        # the live value were already different everywhere that matters.
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(
            payload["pictures"][0]["url"], settings.SOCIAL_PREVIEW_IMAGE_URL
        )

    @override_settings(ECHO_LU_FALLBACK_IMAGE="https://example.test/banner.jpg")
    def test_an_explicit_override_wins(self):
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(
            payload["pictures"][0]["url"], "https://example.test/banner.jpg"
        )

    @override_settings(ECHO_LU_DEFAULT_LANGUAGES="en,fr")
    def test_languages_fall_back_when_the_event_names_none(self):
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(payload["languages"], ["en", "fr"])
        payload = echo_lu.build_experience_payload(make_event(languages=["de"]))
        self.assertEqual(payload["languages"], ["de"])

    def test_status_is_not_part_of_the_payload(self):
        # It is a create-time instruction, not content. In the payload it would
        # join the fingerprint and ride every PUT.
        self.assertNotIn("status", echo_lu.build_experience_payload(make_event()))


@override_settings(**ENABLED)
class UnsendablePayloadTests(TestCase):
    """Refusing to send is not the same as a create whose answer was lost."""

    def test_an_unreadable_venue_registry_leaves_the_row_retryable(self):
        # This one bit on the very first production sync. The venue search
        # happens BEFORE the experience create, so a registry we cannot read
        # proves nothing was created — but a locally raised error carries no
        # status code, and a missing status otherwise reads as "the answer was
        # lost". The event was parked in ORPHANED: blocked from syncing, and
        # sent to an --audit with nothing to find.
        event = make_event()

        class BrokenRegistryClient(FakeClient):
            def list_venues(self, **params):
                raise echo_lu.EchoLuError("nope", status_code=400)

        client = BrokenRegistryClient(venues=[])
        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        self.assertEqual(client.calls, [])  # no venue and no experience created
        event.refresh_from_db()
        self.assertEqual(event.echo_sync.status, EchoExperienceSync.Status.FAILED)
        self.assertNotEqual(event.echo_sync.status, EchoExperienceSync.Status.ORPHANED)

    @override_settings(
        ECHO_LU_DEFAULT_CATEGORIES="",
        ECHO_LU_DEFAULT_AUDIENCES="",
        ECHO_LU_DEFAULT_FORMATS="",
        ECHO_LU_DEFAULT_ENVIRONMENTS="",
    )
    def test_a_misconfigured_facet_leaves_the_row_retryable(self):
        # This used to park the row in ORPHANED. A locally raised error carries
        # no status code, and "no status code" otherwise reads as "the answer
        # was lost" — so an event whose required facets were blanked out (which
        # the setup guide invites, per-environment) was blocked from ever
        # syncing, and pointed at an --audit that cannot find anything because
        # nothing was ever sent.
        event = make_event()
        client = FakeClient()
        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        self.assertEqual(client.calls, [])  # never reached the API
        event.refresh_from_db()
        self.assertEqual(event.echo_sync.status, EchoExperienceSync.Status.FAILED)
        self.assertIn("categories", event.echo_sync.last_error)

    @override_settings(
        ECHO_LU_DEFAULT_CATEGORIES="",
        ECHO_LU_DEFAULT_AUDIENCES="",
        ECHO_LU_DEFAULT_FORMATS="",
        ECHO_LU_DEFAULT_ENVIRONMENTS="",
    )
    def test_it_syncs_once_the_configuration_is_fixed(self):
        # The point of staying FAILED rather than ORPHANED: it recovers by
        # itself on the next sweep once somebody sets the ids.
        event = make_event()
        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=FakeClient())

        with override_settings(
            ECHO_LU_DEFAULT_CATEGORIES="nightlife",
            ECHO_LU_DEFAULT_AUDIENCES="adults",
            ECHO_LU_DEFAULT_FORMATS="networking",
            ECHO_LU_DEFAULT_ENVIRONMENTS="indoors",
        ):
            event.refresh_from_db()
            self.assertEqual(echo_lu.sync_event(event, client=FakeClient()), "created")

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
        self.assertEqual(echo_lu.extract_experience_id({"experienceId": "abc"}), "abc")

    def test_nested_in_data(self):
        self.assertEqual(echo_lu.extract_experience_id({"data": {"_id": "abc"}}), "abc")

    def test_absent_id_returns_empty(self):
        self.assertEqual(echo_lu.extract_experience_id({"ok": True}), "")

    def test_non_dict_returns_empty(self):
        self.assertEqual(echo_lu.extract_experience_id(["abc"]), "")

    def test_the_documented_record_envelope(self):
        # THE shape the API actually returns. Missing it meant every create
        # looked like it came back without an id, so every first publish was
        # parked as an untracked orphan needing manual recovery.
        self.assertEqual(
            echo_lu.extract_experience_id(
                {"stats": {"duration": 3}, "record": {"id": "exp_abc123"}}
            ),
            "exp_abc123",
        )


class ResponseRecordsTests(TestCase):
    """List responses are ``{"stats": …, "records": [...]}``."""

    def test_the_documented_records_envelope(self):
        # Missing this made every list parse as empty — and an empty portal
        # reads as "everything we track was deleted upstream".
        self.assertEqual(
            echo_lu.response_records({"stats": {}, "records": [{"id": "a"}]}),
            [{"id": "a"}],
        )

    def test_a_bare_list_still_works(self):
        self.assertEqual(echo_lu.response_records([{"id": "a"}]), [{"id": "a"}])

    def test_legacy_envelopes_still_work(self):
        self.assertEqual(
            echo_lu.response_records({"data": [{"id": "a"}]}), [{"id": "a"}]
        )

    def test_unrecognised_returns_empty(self):
        self.assertEqual(echo_lu.response_records({"stats": {}}), [])


@override_settings(**ENABLED)
class VenueResolutionTests(TestCase):
    """`venues` takes echo.lu venue IDs from a shared national registry."""

    def test_a_match_outside_the_commune_filter_is_still_found(self):
        # The commune pass is an optimisation, not the search. Our `canton` is
        # free text while echo.lu's commune filter is a controlled value, so
        # the narrowed pass can return unrelated venues while the real match
        # sits in another bucket. Skipping the wide pass because the narrow one
        # returned *something* registered a duplicate into a shared national
        # registry — the exact cost this lookup exists to avoid.
        event = make_event()
        real = {"id": "venue-real", "title": "Café Konrad"}
        unrelated = {"id": "venue-other", "title": "Somewhere Else"}

        class CommuneFilteringClient(FakeClient):
            def list_venues(self, **params):
                if "communes[]" in params:
                    # Narrowed pass: rows, but not the one we want.
                    return {"records": [unrelated]}
                return {"records": [unrelated, real]}

        client = CommuneFilteringClient(venues=[])
        self.assertEqual(echo_lu.resolve_venue_ids(event, client), ["venue-real"])
        self.assertEqual(client.calls, [])  # nothing was registered

    def test_pages_never_exceed_the_hundred_item_ceiling(self):
        # echo.lu rejects a bigger page outright rather than clamping it:
        # "It is not allowed to retrieve more than 100 items per page". The
        # first live sync asked for 200 and failed on this.
        seen = []

        class RecordingClient(FakeClient):
            def list_venues(self, **params):
                seen.append(params.get("pagination[perPage]"))
                return {"records": []}

        echo_lu.resolve_venue_ids(make_event(), RecordingClient(venues=[]))
        self.assertTrue(seen)
        self.assertTrue(all(p is not None and p <= 100 for p in seen), seen)

    def test_an_unknown_commune_falls_back_to_the_wide_search(self):
        # Our commune comes from `canton`, which is free text
        # ("Luxembourg - City"), while echo.lu wants one of its own ids
        # ("luxembourg") and 404s on anything else. The filter is an
        # optimisation, so an unusable one must demote to the unfiltered pass
        # rather than failing the sync.
        real = {"id": "venue-real", "title": "Café Konrad"}

        class PickyClient(FakeClient):
            def list_venues(self, **params):
                if "communes[]" in params:
                    raise echo_lu.EchoLuError("no such commune", status_code=404)
                return {"records": [real]}

        client = PickyClient(venues=[])
        self.assertEqual(
            echo_lu.resolve_venue_ids(make_event(), client), ["venue-real"]
        )
        self.assertEqual(client.calls, [])  # matched, nothing registered

    @override_settings(ECHO_LU_VENUE_SEARCH_PAGES=2)
    def test_the_search_is_capped_and_then_registers(self):
        # Pages cost 3-4s each and the admin action allows 5s per call, so an
        # uncapped scan of 5,000 venues is a hung request. Past the cap we
        # register rather than hunt — a duplicate is untidy, a hung admin
        # request is broken.
        pages = []

        class EndlessClient(FakeClient):
            def list_venues(self, **params):
                pages.append(params.get("pagination[page]"))
                return {"records": [{"id": f"v{len(pages)}", "title": "Nope"}] * 100}

        client = EndlessClient(venues=[])
        result = echo_lu.resolve_venue_ids(make_event(), client)
        self.assertTrue(result[0].startswith("venue-"))
        self.assertEqual([c[0] for c in client.calls], ["create_venue"])
        # 2 pages for the commune pass + 2 for the wide one, and no more.
        self.assertLessEqual(len(pages), 4)

    def test_an_existing_venue_is_matched_not_registered(self):
        # The registry is shared with every other organiser, so registering a
        # second "Café Konrad" beside the one already there is litter in a
        # public dataset.
        event = make_event()
        client = FakeClient(venues=[{"id": "venue-77", "title": "café  KONRAD"}])
        self.assertEqual(echo_lu.resolve_venue_ids(event, client), ["venue-77"])
        self.assertEqual(client.calls, [])

    def test_an_unknown_venue_is_registered(self):
        event = make_event()
        client = FakeClient(venues=[])
        self.assertEqual(echo_lu.resolve_venue_ids(event, client), ["venue-1"])
        self.assertEqual([c[0] for c in client.calls], ["create_venue"])
        self.assertEqual(client.calls[0][1]["title"], "Café Konrad")

    def test_a_similar_name_is_not_matched(self):
        # Attaching our event to somebody else's venue is worse than a
        # duplicate, and invisible from our side — so matching is normalised
        # equality, never a substring.
        event = make_event()
        client = FakeClient(venues=[{"id": "venue-9", "title": "Café Konrad Bar"}])
        # A fresh registration, not venue-9.
        self.assertEqual(echo_lu.resolve_venue_ids(event, client), ["venue-2"])
        self.assertEqual([c[0] for c in client.calls], ["create_venue"])

    def test_the_id_is_cached_so_the_next_event_costs_nothing(self):
        first = make_event()
        client = FakeClient(venues=[])
        echo_lu.resolve_venue_ids(first, client)
        second = make_event(title="Another night")
        after = FakeClient(venues=[])
        self.assertEqual(echo_lu.resolve_venue_ids(second, after), ["venue-1"])
        self.assertEqual(after.calls, [])

    def test_cached_ids_never_call_the_api(self):
        event = make_event()
        self.assertEqual(echo_lu.cached_venue_ids(event), [])
        echo_lu.resolve_venue_ids(event, FakeClient(venues=[]))
        self.assertEqual(echo_lu.cached_venue_ids(event), ["venue-1"])


@override_settings(**ENABLED)
class CreateStatusTests(TestCase):
    """`status` is a create-time instruction, and only that."""

    def test_a_create_asks_for_validation(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        create = [c for c in client.calls if c[0] == "create"][0]
        self.assertEqual(create[1]["status"], "pending")

    @override_settings(ECHO_LU_CREATE_STATUS="draft")
    def test_the_status_is_configurable(self):
        client = FakeClient()
        echo_lu.sync_event(make_event(), client=client)
        self.assertEqual(
            [c for c in client.calls if c[0] == "create"][0][1]["status"], "draft"
        )

    def test_an_update_never_carries_a_status(self):
        # What a status does to an already-validated listing is undocumented,
        # and a national portal is the wrong place to find out.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        event.refresh_from_db()
        event.title = "Renamed"
        event.save()
        echo_lu.sync_event(event, client=client)
        update = [c for c in client.calls if c[0] == "update"][0]
        self.assertNotIn("status", update[2])


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

    def test_a_concurrent_create_is_not_repeated(self):
        # Two of the three entry points (save-signal task, hourly sweep, admin
        # action) can reach an event's first sync at once. The loser holds an
        # instance that still says "no experience_id" — if that stale read
        # decided the call, it would POST a second time and echo.lu would show
        # the event twice under an id we never learn about. The decision is
        # re-made against the locked row, so it must come out as an update.
        event = make_event()
        sync = EchoExperienceSync.objects.create(event=event)
        event.echo_sync = sync  # the stale, pre-race instance

        # Stand in for the winner: the id lands in the database behind the
        # instance's back, exactly as a parallel process would leave it.
        EchoExperienceSync.objects.filter(pk=sync.pk).update(experience_id="exp-123")
        self.assertEqual(sync.experience_id, "")

        client = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=client), "updated")

        self.assertEqual([call[0] for call in client.calls], ["update"])
        self.assertEqual(client.calls[0][1], "exp-123")
        self.assertEqual(EchoExperienceSync.objects.filter(event=event).count(), 1)

    def test_a_rejected_create_is_still_recorded(self):
        # The create runs inside a transaction so the id cannot be written
        # after the lock drops. That transaction must not swallow the failure
        # row with it: a rejection the admin cannot see is a silent outage.
        event = make_event()
        client = FakeClient(
            error=echo_lu.EchoLuError("unknown category slug", status_code=422)
        )

        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.status, EchoExperienceSync.Status.FAILED)
        self.assertIn("unknown category slug", sync.last_error)

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

        with self.assertRaises(echo_lu.EchoLuOrphanedCreate):
            echo_lu.sync_event(event, client=client)

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.status, EchoExperienceSync.Status.ORPHANED)
        self.assertEqual(sync.experience_id, "")

    def test_an_orphaned_create_is_never_repeated(self):
        # The listing from the id-less create is already live and unreachable.
        # Creating again would put a second one beside it, so this state has to
        # stop the sweep, the signal and --force alike; recovery is --audit.
        event = make_event()
        client = FakeClient(create_response={"ok": True})
        with self.assertRaises(echo_lu.EchoLuOrphanedCreate):
            echo_lu.sync_event(event, client=client)
        self.assertEqual([call[0] for call in client.calls], ["create"])

        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "blocked")
        self.assertEqual(
            echo_lu.sync_event(event, client=client, force=True), "blocked"
        )
        self.assertEqual([call[0] for call in client.calls], ["create"])

    def test_a_dry_run_does_not_promise_to_create_an_orphaned_event(self):
        # --dry-run is the diagnostic somebody reaches for once a sweep starts
        # complaining. Answering "would create" for the one event that must
        # never be created again is the wrong answer to exactly that question.
        event = make_event()
        client = FakeClient(create_response={"ok": True})
        with self.assertRaises(echo_lu.EchoLuOrphanedCreate):
            echo_lu.sync_event(event, client=client)

        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, dry_run=True), "blocked")

    def test_an_orphaned_event_stays_visible_to_the_sweep(self):
        # Tempting to filter these out — no write may be made for one — but
        # `sync_event` already refuses the write, and "blocked" is the only
        # way the sweep ever reports the orphan. Dropped from the queryset,
        # an untracked public listing becomes invisible to the hourly job and
        # the timer reports green over it forever.
        event = make_event()
        client = FakeClient(create_response={"ok": True})
        with self.assertRaises(echo_lu.EchoLuOrphanedCreate):
            echo_lu.sync_event(event, client=client)

        self.assertIn(event, list(echo_lu.events_needing_sync()))

        selected = echo_lu.events_needing_sync().get(pk=event.pk)
        self.assertEqual(echo_lu.sync_event(selected, client=client), "blocked")
        self.assertEqual([call[0] for call in client.calls], ["create"])

    def test_an_orphan_stays_visible_after_its_event_stops_qualifying(self):
        # Selecting orphans through the eligibility filters is not enough:
        # every event eventually fails them, if only by happening. An orphan
        # that ages out takes the alerting with it and the hourly job goes
        # green over a live listing nobody can reach — so the row has to be
        # selected on its own status, not on what the event says.
        for change in (
            {"is_published": False},
            {"is_private_invitation": True, "invitation_code": "secret-1"},
            {"is_cancelled": True},
            {
                "date_time": timezone.now() - timedelta(days=30),
                "registration_deadline": timezone.now() - timedelta(days=31),
            },
        ):
            with self.subTest(change=change):
                event = make_event()
                client = FakeClient(create_response={"ok": True})
                with self.assertRaises(echo_lu.EchoLuOrphanedCreate):
                    echo_lu.sync_event(event, client=client)

                for field, value in change.items():
                    setattr(event, field, value)
                event.save()
                event.refresh_from_db()

                self.assertIn(event, list(echo_lu.events_needing_sync()))
                self.assertEqual(echo_lu.sync_event(event, client=client), "blocked")
                # Still no second call, whatever the event now says.
                self.assertEqual([call[0] for call in client.calls], ["create"])

    def test_an_ambiguous_create_failure_is_terminal(self):
        # Not retrying inside the call was only half of it: a 5xx does not say
        # whether echo.lu committed the experience, so a sweep coming back an
        # hour later to a row with no id would POST again and publish the
        # event twice — the duplicate the no-retry rule was meant to prevent,
        # just an hour later.
        event = make_event()
        client = FakeClient(error=echo_lu.EchoLuError("boom", status_code=503))

        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.status, EchoExperienceSync.Status.ORPHANED)

        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "blocked")
        self.assertEqual([call[0] for call in client.calls], ["create"])

    def test_a_transport_failure_on_create_is_terminal(self):
        # A dropped socket is the most ambiguous case of all — the request was
        # sent and the answer never arrived.
        event = make_event()
        client = FakeClient(error=echo_lu.EchoLuError("connection reset"))

        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).status,
            EchoExperienceSync.Status.ORPHANED,
        )

    def test_a_rejected_create_stays_retryable(self):
        # The other side of the rule: a 422 means echo.lu read the payload and
        # refused it, so nothing exists and the fix-and-retry loop this whole
        # integration depends on has to keep working.
        event = make_event()
        client = FakeClient(error=echo_lu.EchoLuError("bad slug", status_code=422))

        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).status,
            EchoExperienceSync.Status.FAILED,
        )

    def test_a_rate_limited_create_stays_retryable(self):
        # The transport retries a create on 429 precisely because that status
        # proves the request was not processed. The same fact has to hold when
        # the retries run out, or a busy hour parks the event as an orphan and
        # sends somebody to audit a listing that was never created.
        event = make_event()
        client = FakeClient(error=echo_lu.EchoLuError("slow down", status_code=429))

        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).status,
            EchoExperienceSync.Status.FAILED,
        )

    def test_a_failed_removal_is_retried_rather_than_republished(self):
        # The nastiest inversion in the module: the event still says "publish
        # me", so a take-down that failed and recorded only FAILED reads to
        # the next sweep as a failed *publish* — and it answers with a PUT,
        # putting back the listing somebody just asked to remove.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        failing = FakeClient(error=echo_lu.EchoLuError("boom", status_code=503))
        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.withdraw_event(event, client=failing, explicit=True)

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertTrue(sync.removal_requested)

        # The sweep still selects it, and what it does is retry the removal.
        event.refresh_from_db()
        self.assertIn(event, list(echo_lu.events_needing_sync()))
        retry = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=retry), "withdrawn")
        self.assertEqual([call[0] for call in retry.calls], ["unpublish"])

        sync.refresh_from_db()
        self.assertEqual(sync.status, EchoExperienceSync.Status.SUPPRESSED)
        self.assertFalse(sync.removal_requested)

    def test_a_pending_removal_can_still_be_overridden_deliberately(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        failing = FakeClient(error=echo_lu.EchoLuError("boom", status_code=503))
        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.withdraw_event(event, client=failing, explicit=True)

        event.refresh_from_db()
        self.assertEqual(
            echo_lu.sync_event(event, client=FakeClient(), force=True), "updated"
        )

    def test_a_missing_key_does_not_block_the_event_forever(self):
        # EchoLuNotConfigured is raised before anything is sent, so it proves
        # nothing was created. Treating it as ambiguous would let a
        # misconfigured environment permanently block every event it touched.
        event = make_event()
        client = FakeClient(error=echo_lu.EchoLuNotConfigured("no key"))

        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.sync_event(event, client=client)

        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).status,
            EchoExperienceSync.Status.FAILED,
        )

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

    def test_privacy_beats_a_cancellation_notice(self):
        # A cancellation notice is a published thing: it keeps the title,
        # venue and date on a national portal. An event that was cancelled AND
        # pulled from public view must come down properly instead — the
        # organiser asked for privacy, and that outranks telling people why.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.is_cancelled = True
        event.is_private_invitation = True
        event.save()
        event.refresh_from_db()
        echo_lu.sync_event(event, client=client)

        self.assertEqual([call[0] for call in client.calls], ["create", "unpublish"])

    def test_an_explicit_withdrawal_survives_the_sweep(self):
        # --withdraw and the admin's remove action take down a listing whose
        # event still qualifies. Nothing in the event's own fields records
        # that, so without a status the sweep respects, the next hourly pass
        # would put it straight back and the removal would look accidental.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        echo_lu.withdraw_event(event, client=client, explicit=True)
        event.refresh_from_db()

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.status, EchoExperienceSync.Status.SUPPRESSED)
        self.assertNotIn(event, list(echo_lu.events_needing_sync()))
        self.assertEqual(echo_lu.sync_event(event, client=client), "suppressed")
        self.assertEqual([call[0] for call in client.calls], ["create", "unpublish"])

    def test_an_explicit_withdrawal_takes_a_cancelled_event_down_properly(self):
        # A cancelled-but-still-published event withdrawn by hand was being
        # *cancelled* on echo.lu, leaving the notice publicly visible — and
        # the SUPPRESSED state it then recorded stopped the sweep ever
        # correcting it. Asking for removal means removal.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.is_cancelled = True
        event.save()
        event.refresh_from_db()
        echo_lu.withdraw_event(event, client=client, explicit=True)

        self.assertEqual([call[0] for call in client.calls], ["create", "unpublish"])

    def test_suppression_survives_the_event_becoming_ineligible(self):
        # Withdrawing again would rewrite SUPPRESSED to WITHDRAWN, and the
        # sweep re-publishes one of those as soon as the event qualifies
        # again — so an unrelated unpublish/republish cycle would quietly undo
        # a removal somebody asked for.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        echo_lu.withdraw_event(event, client=client, explicit=True)

        event.is_published = False
        event.save()
        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "suppressed")

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.status, EchoExperienceSync.Status.SUPPRESSED)
        self.assertEqual([call[0] for call in client.calls], ["create", "unpublish"])

        event.is_published = True
        event.save()
        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "suppressed")

    def test_a_cancellation_notice_comes_down_when_privacy_tightens_later(self):
        # A cancellation notice is not a take-down: title, venue and date stay
        # on the portal. Recording it as WITHDRAWN said the work was done, so
        # when the coach unpublished the event a save later, nothing came down
        # and the notice stayed public indefinitely.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.is_cancelled = True
        event.save()
        event.refresh_from_db()
        echo_lu.sync_event(event, client=client)
        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).status,
            EchoExperienceSync.Status.CANCELLED,
        )
        self.assertEqual([call[0] for call in client.calls], ["create", "cancel"])

        # Still cancelled and still public: nothing more to do.
        self.assertEqual(echo_lu.sync_event(event, client=client), "skipped")

        # Now the coach pulls it from public view — the notice must go.
        event.is_published = False
        event.save()
        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "withdrawn")
        self.assertEqual(
            [call[0] for call in client.calls], ["create", "cancel", "unpublish"]
        )

    def test_a_cancelled_listing_stays_visible_to_the_sweep(self):
        # It has to: the sweep is what notices privacy tightening later.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        event.is_cancelled = True
        event.save()
        event.refresh_from_db()
        echo_lu.sync_event(event, client=client)

        self.assertIn(event, list(echo_lu.events_needing_sync()))

    def test_a_forced_republish_clears_a_pending_removal(self):
        # --force and the admin sync action are the documented way to put a
        # removed listing back. Leaving the request set would have the next
        # sweep take it down again, so the override would last one hour.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        failing = FakeClient(error=echo_lu.EchoLuError("boom", status_code=503))
        with self.assertRaises(echo_lu.EchoLuError):
            echo_lu.withdraw_event(event, client=failing, explicit=True)

        event.refresh_from_db()
        echo_lu.sync_event(event, client=FakeClient(), force=True)

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertFalse(sync.removal_requested)

        # The event is still in the sweep — it is published and upcoming, so it
        # should be. What matters is that the next pass leaves it alone rather
        # than taking it down again an hour after the override.
        event.refresh_from_db()
        after = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=after), "unchanged")
        self.assertEqual(after.calls, [])

    def test_an_explicit_withdrawal_can_be_deliberately_undone(self):
        # The suppression holds against the sweep, not against a coach who
        # asks for the listing back through the admin's sync action.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        echo_lu.withdraw_event(event, client=client, explicit=True)
        event.refresh_from_db()

        self.assertEqual(
            echo_lu.sync_event(event, client=client, force=True), "updated"
        )
        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).status,
            EchoExperienceSync.Status.SYNCED,
        )

    def test_an_automatic_withdrawal_still_comes_back(self):
        # The other half of the distinction: an event withdrawn because it
        # stopped qualifying must re-publish by itself once it qualifies
        # again, with no manual step.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.is_published = False
        event.save()
        event.refresh_from_db()
        echo_lu.sync_event(event, client=client)
        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).status,
            EchoExperienceSync.Status.WITHDRAWN,
        )

        event.is_published = True
        event.save()
        event.refresh_from_db()
        self.assertIn(event, list(echo_lu.events_needing_sync()))
        self.assertEqual(echo_lu.sync_event(event, client=client), "updated")

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

    def test_a_dry_run_reports_the_no_op_the_real_run_would_make(self):
        # The preview used to answer "would update" for every synced event,
        # while the real sweep makes no call at all — so it described a sweep
        # nobody runs, and the fingerprint shortcut looked broken.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        event.refresh_from_db()

        self.assertEqual(echo_lu.sync_event(event, dry_run=True), "unchanged")

    def test_a_dry_run_reports_a_suppressed_event_as_suppressed(self):
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        echo_lu.withdraw_event(event, client=client, explicit=True)
        event.refresh_from_db()

        self.assertEqual(echo_lu.sync_event(event, dry_run=True), "suppressed")

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
        # An update is idempotent, so replaying it costs nothing but time.
        with mock.patch(
            "crush_lu.services.echo_lu.requests.request"
        ) as request, mock.patch("crush_lu.services.echo_lu.time.sleep"):
            request.return_value = self._response(status=503)
            with self.assertRaises(echo_lu.EchoLuError):
                echo_lu.EchoLuClient().update_experience("exp-1", {})

        self.assertGreater(request.call_count, 1)

    def test_a_create_is_not_retried_after_a_server_error(self):
        # A 503 does not say whether echo.lu committed the experience before
        # it fell over. There is no idempotency key and no external reference,
        # so a replay that guesses wrong publishes the event twice under an id
        # we never see — worse than the failure we are retrying.
        with mock.patch(
            "crush_lu.services.echo_lu.requests.request"
        ) as request, mock.patch("crush_lu.services.echo_lu.time.sleep"):
            request.return_value = self._response(status=503)
            with self.assertRaises(echo_lu.EchoLuError):
                echo_lu.EchoLuClient().create_experience({})

        self.assertEqual(request.call_count, 1)

    def test_a_create_is_not_retried_after_a_dropped_connection(self):
        # Same reasoning as the 503: a read timeout is the case where echo.lu
        # is most likely to have committed the write we did not hear about.
        import requests as requests_lib

        with mock.patch(
            "crush_lu.services.echo_lu.requests.request"
        ) as request, mock.patch("crush_lu.services.echo_lu.time.sleep"):
            request.side_effect = requests_lib.ConnectionError("reset")
            with self.assertRaises(echo_lu.EchoLuError):
                echo_lu.EchoLuClient().create_experience({})

        self.assertEqual(request.call_count, 1)

    def test_a_rate_limited_create_is_retried(self):
        # 429 is the one answer that says outright it was not processed.
        with mock.patch(
            "crush_lu.services.echo_lu.requests.request"
        ) as request, mock.patch("crush_lu.services.echo_lu.time.sleep"):
            request.side_effect = [
                self._response(status=429, headers={"Retry-After": "1"}),
                self._response(json_body={"id": "exp-9"}),
            ]
            self.assertEqual(
                echo_lu.EchoLuClient().create_experience({}), {"id": "exp-9"}
            )

    def test_a_no_retry_client_gives_up_immediately(self):
        # What the save-signal path uses, so an unreachable echo.lu cannot
        # hold an admin save open for a minute of retries.
        with mock.patch(
            "crush_lu.services.echo_lu.requests.request"
        ) as request, mock.patch("crush_lu.services.echo_lu.time.sleep"):
            request.return_value = self._response(status=503)
            with self.assertRaises(echo_lu.EchoLuError):
                echo_lu.EchoLuClient(max_retries=0).update_experience("exp-1", {})

        self.assertEqual(request.call_count, 1)

    def test_retry_succeeds_on_the_second_attempt(self):
        with mock.patch(
            "crush_lu.services.echo_lu.requests.request"
        ) as request, mock.patch("crush_lu.services.echo_lu.time.sleep"):
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
        # Both halves matter: every other event is still attempted, and the
        # command still exits non-zero. The Function counts a clean return as
        # a successful invocation, so a swallowed failure leaves a revoked key
        # showing green on the only monitoring anybody watches.
        from django.core.management.base import CommandError

        good = make_event(title="Good")
        bad = make_event(title="Bad")

        def fake_sync(event, client=None, force=False, dry_run=False):
            if event.pk == bad.pk:
                raise echo_lu.EchoLuError("rejected", status_code=422)
            return "created"

        out = StringIO()
        with mock.patch.object(
            echo_lu, "sync_event", side_effect=fake_sync
        ), mock.patch.object(echo_lu, "EchoLuClient"):
            with self.assertRaises(CommandError):
                call_command("sync_events_to_echo", stdout=out, stderr=out)

        output = out.getvalue()
        self.assertIn("Good", output)
        self.assertIn("rejected", output)

    @override_settings(**ENABLED)
    def test_a_blocked_event_fails_the_sweep(self):
        # An orphaned listing is the one state that never recovers on its own,
        # so a sweep that returned 0 over it would report green to the Azure
        # timer forever about a listing nobody can reach — worse than the
        # transient rejection the non-zero exit was added for.
        from django.core.management.base import CommandError

        make_event(title="Orphaned")

        out = StringIO()
        with mock.patch.object(
            echo_lu, "sync_event", return_value="blocked"
        ), mock.patch.object(echo_lu, "EchoLuClient"):
            with self.assertRaises(CommandError) as caught:
                call_command("sync_events_to_echo", stdout=out, stderr=out)

        self.assertIn("blocked", str(caught.exception))
        self.assertIn("--audit", str(caught.exception))

    @override_settings(**ENABLED)
    def test_the_sweep_stops_on_its_time_budget(self):
        # The EchoLuSync Function allows 110s. Running past it means being
        # killed part-way through an event rather than stopping cleanly, so
        # the pass bounds itself and lets the next hour take the remainder.
        make_event(title="First")
        make_event(title="Second")

        def slow_sync(event, client=None, force=False, dry_run=False):
            ticker[0] += 1000
            return "created"

        ticker = [0]
        out = StringIO()
        with mock.patch.object(
            echo_lu, "sync_event", side_effect=slow_sync
        ), mock.patch.object(echo_lu, "EchoLuClient"), mock.patch(
            "crush_lu.management.commands.sync_events_to_echo." "time.monotonic",
            side_effect=lambda: ticker[0],
        ):
            call_command("sync_events_to_echo", "--max-seconds", "25", stdout=out)

        self.assertIn("1 event(s) left", out.getvalue())

    @override_settings(**ENABLED)
    def test_the_sweep_leaves_room_for_the_call_it_starts(self):
        # Checking only "has the budget run out" lets an event start at 89s of
        # 90 and then spend a whole timeout more — which is how a bounded
        # sweep still overruns the Function it was bounded for. The budget has
        # to reserve one worst-case call, so a budget under that starts none.
        make_event(title="First")

        out = StringIO()
        with mock.patch.object(echo_lu, "sync_event") as sync_event, mock.patch.object(
            echo_lu, "EchoLuClient"
        ):
            call_command("sync_events_to_echo", "--max-seconds", "5", stdout=out)

        sync_event.assert_not_called()
        self.assertIn("1 event(s) left", out.getvalue())

    @override_settings(**ENABLED)
    def test_unknown_event_id_is_an_error(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run("--event-id", "999999")


@override_settings(
    ECHO_LU_API_KEY="test-key",
    ECHO_LU_DEFAULT_CATEGORIES="dating",
    ECHO_LU_DEFAULT_AUDIENCES="adults",
    ECHO_LU_DEFAULT_FORMATS="",
    ECHO_LU_DEFAULT_ENVIRONMENTS="",
    ECHO_LU_DEFAULT_TAGS="",
    ECHO_LU_CATEGORY_MAP="",
)
class TaxonomyCheckTests(TestCase):
    """`echo_taxonomy --check` is the gate before the sync switch goes on.

    One unknown slug makes echo.lu reject the whole experience, so a check
    that exits 0 is read as permission to enable syncing. It has to be honest
    about what it did not manage to look at.
    """

    def _run_check(self, responses, *args):
        from django.core.management.base import CommandError

        client = mock.Mock()
        client.TAXONOMIES = ("categories", "audiences")

        def list_taxonomy(kind):
            value = responses[kind]
            if isinstance(value, Exception):
                raise value
            return value

        client.list_taxonomy.side_effect = list_taxonomy
        out = StringIO()
        with mock.patch.object(echo_lu, "EchoLuClient") as client_class:
            # --kind's argparse choices read the attribute off the class, so
            # the stand-in has to carry it as well as the instance does.
            client_class.TAXONOMIES = client.TAXONOMIES
            client_class.return_value = client
            try:
                call_command("echo_taxonomy", "--check", *args, stdout=out, stderr=out)
            except CommandError as exc:
                return out.getvalue(), exc
        return out.getvalue(), None

    def test_configured_slugs_that_all_exist_pass(self):
        output, error = self._run_check(
            {"categories": ["dating"], "audiences": ["adults"]}
        )
        self.assertIsNone(error)
        self.assertIn("all configured slugs exist", output)

    def test_an_unknown_slug_fails_the_check(self):
        output, error = self._run_check(
            {"categories": ["music"], "audiences": ["adults"]}
        )
        self.assertIsNotNone(error)
        self.assertIn("not in echo.lu", output)

    def test_a_taxonomy_that_could_not_be_read_fails_the_check(self):
        # The dangerous case: nothing mismatched, because one facet was never
        # compared at all. Reporting that as success invites an operator to
        # turn sync on with slugs echo.lu has never confirmed.
        output, error = self._run_check(
            {
                "categories": echo_lu.EchoLuError("503 upstream"),
                "audiences": ["adults"],
            }
        )
        self.assertIsNotNone(error)
        self.assertIn("Not checked: categories", output)
        self.assertNotIn("all configured slugs exist", output)

    @override_settings(ECHO_LU_CATEGORY_MAP='{"speed_dating": 123}')
    def test_a_scalar_map_value_is_reported_not_raised(self):
        # The runtime drops this shape; the check is the thing that is supposed
        # to *tell* somebody about it, so crashing here is the worst outcome —
        # the gate fails in a way that looks like a broken command rather than
        # broken configuration.
        output, error = self._run_check(
            {"categories": ["dating"], "audiences": ["adults"]}
        )
        self.assertIsNotNone(error)
        self.assertIn("expected a string or a list", output)

    @override_settings(ECHO_LU_CATEGORY_MAP='["dating"]')
    def test_a_top_level_array_map_is_reported_not_raised(self):
        output, error = self._run_check(
            {"categories": ["dating"], "audiences": ["adults"]}
        )
        self.assertIsNotNone(error)
        self.assertIn("expected an object", output)

    @override_settings(ECHO_LU_CATEGORY_MAP="not json")
    def test_unparseable_map_fails_the_check(self):
        output, error = self._run_check(
            {"categories": ["dating"], "audiences": ["adults"]}
        )
        self.assertIsNotNone(error)
        self.assertNotIn("all configured slugs exist", output)

    def test_narrowing_to_one_kind_does_not_claim_the_others_passed(self):
        output, error = self._run_check(
            {"categories": ["dating"], "audiences": ["adults"]},
            "--kind",
            "categories",
        )
        self.assertIsNotNone(error)
        self.assertIn("Not checked: audiences", output)


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
            self._extract(
                [{"slug": "music", "name": {"fr": "Musique", "en": "Music"}}]
            ),
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


@override_settings(**ENABLED)
class AdminSyncActionTests(TestCase):
    """The bulk action is what a coach uses to retry and read the outcome.

    Its message is the only feedback that action gives, so it has to describe
    what actually reached echo.lu — the change form is where the detail lives,
    but nobody opens it after being told the sync succeeded.
    """

    def _run_action(self, events, outcome):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.events import MeetupEventAdmin

        request = RequestFactory().post("/crush-admin/")
        request.session = {}
        request._messages = FallbackStorage(request)

        model_admin = MeetupEventAdmin(MeetupEvent, AdminSite())
        queryset = MeetupEvent.objects.filter(pk__in=[e.pk for e in events])
        with mock.patch.object(
            echo_lu, "sync_event", return_value=outcome
        ), mock.patch.object(echo_lu, "EchoLuClient"):
            model_admin.sync_to_echo_lu(request, queryset)

        return [str(message) for message in request._messages]

    def test_a_blocked_event_is_not_reported_as_synced(self):
        # The bug this closes: an orphaned listing answered "Synced 1
        # event(s)", so the one state that needs a human looked done.
        messages = self._run_action([make_event()], "blocked")

        self.assertFalse(any("Synced" in message for message in messages))
        self.assertTrue(any("not sent (blocked)" in message for message in messages))

    def test_a_skipped_event_is_not_reported_as_synced(self):
        messages = self._run_action([make_event()], "skipped")

        self.assertFalse(any("Synced" in message for message in messages))
        self.assertTrue(any("not sent (skipped)" in message for message in messages))

    def test_a_real_sync_still_reports_success(self):
        messages = self._run_action([make_event()], "created")

        self.assertTrue(any("Synced 1 event(s)" in message for message in messages))
        self.assertFalse(any("not sent" in message for message in messages))


@override_settings(**ENABLED)
class OrphanRecoveryTests(TestCase):
    """The blocked state is immovable from code, so a person needs a lever.

    The doc used to point at editing the sync row in the admin, which was not
    registered anywhere — the documented recovery could not actually be done.
    """

    def _orphan(self):
        event = make_event()
        with self.assertRaises(echo_lu.EchoLuOrphanedCreate):
            echo_lu.sync_event(event, client=FakeClient(create_response={"ok": True}))
        event.refresh_from_db()
        return event

    def test_adopting_an_id_resumes_syncing_that_listing(self):
        event = self._orphan()

        out = StringIO()
        call_command(
            "sync_events_to_echo",
            "--event-id",
            str(event.pk),
            "--adopt",
            "exp-found",
            stdout=out,
        )

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.experience_id, "exp-found")
        self.assertEqual(sync.status, EchoExperienceSync.Status.PENDING)

        # And the next sync updates that listing rather than creating one.
        event.refresh_from_db()
        client = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=client), "updated")
        self.assertEqual(client.calls[0][1], "exp-found")

    def test_adopting_does_not_trust_an_unverified_fingerprint(self):
        # PENDING with no payload_hash, so the next sync sends a full update.
        # Marking it SYNCED would let the fingerprint shortcut skip the very
        # first write to a listing nobody has confirmed the content of.
        event = self._orphan()
        call_command(
            "sync_events_to_echo",
            "--event-id",
            str(event.pk),
            "--adopt",
            "exp-found",
            stdout=StringIO(),
        )

        self.assertEqual(EchoExperienceSync.objects.get(event=event).payload_hash, "")

    def test_forgetting_clears_the_block_for_a_deleted_listing(self):
        event = self._orphan()

        call_command(
            "sync_events_to_echo",
            "--event-id",
            str(event.pk),
            "--forget",
            stdout=StringIO(),
        )

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertEqual(sync.experience_id, "")
        self.assertEqual(sync.status, EchoExperienceSync.Status.PENDING)

        event.refresh_from_db()
        client = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=client), "created")

    def test_resolving_needs_an_event(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("sync_events_to_echo", "--adopt", "exp-1", stdout=StringIO())


@override_settings(**ENABLED)
class SweepFairnessTests(TestCase):
    """A bounded sweep with a fixed order starves whatever sits at the back."""

    def test_least_recently_attempted_events_come_first(self):
        # MeetupEvent orders by date. Combined with a wall-clock budget that
        # is what starvation looks like: the same early events consume every
        # hourly invocation and the tail is deferred forever — including
        # take-downs of events that have gone private, which would stay public
        # for as long as the queue is busy.
        now = timezone.now()
        first = make_event(
            title="Earliest",
            date_time=now + timedelta(days=1),
            registration_deadline=now,
        )
        second = make_event(
            title="Middle", date_time=now + timedelta(days=2), registration_deadline=now
        )
        third = make_event(
            title="Latest", date_time=now + timedelta(days=3), registration_deadline=now
        )

        # The two earliest by date were just attempted; the latest never was.
        EchoExperienceSync.objects.create(event=first, last_attempted_at=now)
        EchoExperienceSync.objects.create(
            event=second, last_attempted_at=now - timedelta(hours=2)
        )

        order = [event.pk for event in echo_lu.events_needing_sync()]
        self.assertEqual(order, [third.pk, second.pk, first.pk])


@override_settings(**ENABLED)
@override_settings(ECHO_LU_DEFAULT_CATEGORIES="")
class CategoryMapSafetyTests(TestCase):
    """Bad config must not raise out of a save that already committed.

    Defaults are cleared class-wide so each case measures the MAP alone; the
    real ECHO_LU_DEFAULT_CATEGORIES is now a live id rather than empty, and
    would otherwise show up in every expected list.
    """

    @override_settings(ECHO_LU_CATEGORY_MAP='{"speed_dating": 123}')
    def test_a_scalar_map_value_is_ignored_rather_than_raising(self):
        # list(123) is a TypeError, not an EchoLuError — so the save-signal
        # task would not swallow it, and an ordinary event save would surface
        # an error after its transaction had committed. Every sweep would die
        # here too, before reaching echo.lu at all.
        # An unconfigured facet is omitted entirely rather than sent empty, so
        # the assertion is that the payload builds at all — the bug was a
        # TypeError escaping into an already-committed save.
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(payload["categories"], [])

    @override_settings(ECHO_LU_CATEGORY_MAP='{"speed_dating": ["dating"]}')
    def test_a_list_map_value_is_used(self):
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(payload["categories"], ["dating"])

    @override_settings(ECHO_LU_CATEGORY_MAP='{"speed_dating": "dating"}')
    def test_a_string_map_value_is_wrapped(self):
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(payload["categories"], ["dating"])

    @override_settings(ECHO_LU_CATEGORY_MAP="not json at all")
    def test_unparseable_map_does_not_raise(self):
        payload = echo_lu.build_experience_payload(make_event())
        self.assertEqual(payload["categories"], [])


@override_settings(**ENABLED)
class OrphanRecoveryGuardTests(TestCase):
    """--adopt/--forget are sharp: pointed at a healthy row they strand it."""

    def test_forget_refuses_a_healthy_row(self):
        # A mistyped event id is all it takes: clearing a valid experience_id
        # leaves the listing live and unreachable, and the next sync posts a
        # second one beside it — the duplicate this command exists to clean up.
        from django.core.management.base import CommandError

        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())

        with self.assertRaises(CommandError) as caught:
            call_command(
                "sync_events_to_echo",
                "--event-id",
                str(event.pk),
                "--forget",
                stdout=StringIO(),
            )

        self.assertIn("not blocked", str(caught.exception))
        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).experience_id, "exp-123"
        )

    def test_adopt_refuses_a_healthy_row(self):
        from django.core.management.base import CommandError

        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())

        with self.assertRaises(CommandError):
            call_command(
                "sync_events_to_echo",
                "--event-id",
                str(event.pk),
                "--adopt",
                "exp-other",
                stdout=StringIO(),
            )


@override_settings(**ENABLED)
class LockedStateRereadTests(TestCase):
    """What the lock protects is the *decision*, not just the row."""

    def test_a_publish_that_lost_the_race_does_not_undo_the_winner(self):
        # Eligibility, payload and fingerprint are all computed before the wait
        # on the row lock. If the writer we were queued behind was a save that
        # made the event private and took the listing down, resuming on that
        # stale read would PUT the old public payload back onto a national
        # portal — with the title, venue and date the coach just hid.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        event.refresh_from_db()

        # Stand in for the winner: the event goes private in the database while
        # this caller holds a pre-wait copy that still says "publish me". The
        # stale copy also carries an edit, so the fingerprint differs and the
        # call actually reaches the write path rather than short-circuiting.
        event.title = "Renamed while we waited"
        MeetupEvent.objects.filter(pk=event.pk).update(
            is_published=False, is_private_invitation=True
        )
        self.assertTrue(event.is_published)  # the stale copy, as the sweep had it

        after = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=after), "skipped")
        self.assertEqual(after.calls, [])

    def test_a_cancellation_notice_comes_down_once_the_event_ends(self):
        # The notice is a published thing, so everything that ends an ordinary
        # listing ends it too — including the event simply happening. Checking
        # only publication and privacy left it up forever, and the sweep
        # repeated the no-op every hour without noticing.
        start = timezone.now() + timedelta(hours=1)
        event = make_event(date_time=start, registration_deadline=timezone.now())
        client = FakeClient()
        echo_lu.sync_event(event, client=client)

        event.is_cancelled = True
        event.save()
        event.refresh_from_db()
        echo_lu.sync_event(event, client=client)
        self.assertEqual([call[0] for call in client.calls], ["create", "cancel"])

        # Still upcoming: the notice is wanted.
        self.assertEqual(echo_lu.sync_event(event, client=client), "skipped")

        # Now it has been and gone.
        MeetupEvent.objects.filter(pk=event.pk).update(
            date_time=timezone.now() - timedelta(days=2)
        )
        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "withdrawn")
        self.assertEqual(
            [call[0] for call in client.calls], ["create", "cancel", "unpublish"]
        )


@override_settings(**ENABLED)
class RemovalBeforeAnyListingTests(TestCase):
    """Removing an event that was never listed still has to mean something."""

    def test_removing_an_unlisted_event_stops_it_being_created(self):
        # No experience_id yet — the first sync is pending, or its create was
        # refused. Returning "skipped" recorded nothing, so the next sweep
        # created the listing the coach had just removed.
        event = make_event()
        client = FakeClient()

        self.assertEqual(
            echo_lu.withdraw_event(event, client=client, explicit=True), "skipped"
        )
        self.assertEqual(client.calls, [])

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertTrue(sync.removal_requested)

        event.refresh_from_db()
        self.assertEqual(echo_lu.sync_event(event, client=client), "suppressed")
        self.assertEqual(client.calls, [])

    def test_it_can_still_be_published_deliberately(self):
        event = make_event()
        echo_lu.withdraw_event(event, client=FakeClient(), explicit=True)
        event.refresh_from_db()

        client = FakeClient()
        self.assertEqual(
            echo_lu.sync_event(event, client=client, force=True), "created"
        )


@override_settings(**ENABLED)
class AdoptionClashTests(TestCase):
    """Adopting is manual and untyped, so it has to refuse the obvious slip."""

    def test_adopting_an_id_another_event_already_holds_is_refused(self):
        # Two events on one listing is not a smaller problem than the orphan:
        # each sweep PUTs a different payload to the same id, so the listing
        # flips and one event is never really published.
        from django.core.management.base import CommandError

        healthy = make_event(title="Healthy")
        echo_lu.sync_event(healthy, client=FakeClient())

        orphan = make_event(title="Orphan")
        with self.assertRaises(echo_lu.EchoLuOrphanedCreate):
            echo_lu.sync_event(orphan, client=FakeClient(create_response={"ok": 1}))

        with self.assertRaises(CommandError) as caught:
            call_command(
                "sync_events_to_echo",
                "--event-id",
                str(orphan.pk),
                "--adopt",
                "exp-123",
                stdout=StringIO(),
            )

        self.assertIn("already tracked", str(caught.exception))
        self.assertEqual(
            EchoExperienceSync.objects.get(event=orphan).status,
            EchoExperienceSync.Status.ORPHANED,
        )


@override_settings(**ENABLED)
class RemoveActionDeferralTests(TestCase):
    """The action's own fallback has to keep the promise its message makes."""

    def _run_remove(self, events, budget):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.events import MeetupEventAdmin

        request = RequestFactory().post("/crush-admin/")
        request.session = {}
        request._messages = FallbackStorage(request)

        model_admin = MeetupEventAdmin(MeetupEvent, AdminSite())
        queryset = MeetupEvent.objects.filter(pk__in=[e.pk for e in events])
        with override_settings(ECHO_LU_ADMIN_BUDGET_SECONDS=budget), mock.patch.object(
            echo_lu, "EchoLuClient"
        ), mock.patch.object(echo_lu, "withdraw_event", return_value="skipped"):
            model_admin.withdraw_from_echo_lu(request, queryset)
        return [str(message) for message in request._messages]

    def test_a_deferred_event_with_no_listing_still_records_the_removal(self):
        # The events with nothing on echo.lu yet are exactly the ones a bulk
        # update over rows-with-an-id skips — and for them "removed" means
        # "never create it", the one instruction that can still be lost.
        event = make_event()
        self.assertFalse(EchoExperienceSync.objects.filter(event=event).exists())

        # Budget of 0 defers everything without attempting any of it.
        messages = self._run_remove([event], budget=0)

        sync = EchoExperienceSync.objects.get(event=event)
        self.assertTrue(sync.removal_requested)
        self.assertTrue(any("not attempted here" in m for m in messages))

        # And the promise holds: the next sync does not create the listing.
        event.refresh_from_db()
        client = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=client), "suppressed")
        self.assertEqual(client.calls, [])

    def test_a_deferred_event_with_a_blank_id_records_the_removal(self):
        # A previously refused create leaves a row with no experience_id.
        event = make_event()
        EchoExperienceSync.objects.create(
            event=event, status=EchoExperienceSync.Status.FAILED
        )

        self._run_remove([event], budget=0)

        self.assertTrue(EchoExperienceSync.objects.get(event=event).removal_requested)


@override_settings(**ENABLED)
class WithdrawLockRereadTests(TestCase):
    """The take-down path decides from the event too, so it re-reads it."""

    def test_a_cancellation_that_lost_the_race_does_not_republish(self):
        # An automatic cancellation queues behind a privacy change. That change
        # unpublishes the listing; this caller then wakes holding a pre-lock
        # copy that still says "public", sends `cancel`, and puts the title,
        # venue and date of a now-private event back on public display.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        event.refresh_from_db()

        # The stale copy says cancelled-but-public, as the caller had it.
        event.is_cancelled = True
        # The database says private, as the winner left it.
        MeetupEvent.objects.filter(pk=event.pk).update(
            is_cancelled=True, is_published=False, is_private_invitation=True
        )

        after = FakeClient()
        self.assertEqual(echo_lu.withdraw_event(event, client=after), "withdrawn")
        self.assertEqual([call[0] for call in after.calls], ["unpublish"])
        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).status,
            EchoExperienceSync.Status.WITHDRAWN,
        )


@override_settings(**ENABLED)
class AdoptBlankIdTests(TestCase):
    """--adopt with no id is --forget wearing the wrong name."""

    def test_a_blank_id_is_refused(self):
        from django.core.management.base import CommandError

        event = make_event()
        with self.assertRaises(echo_lu.EchoLuOrphanedCreate):
            echo_lu.sync_event(event, client=FakeClient(create_response={"ok": 1}))

        with self.assertRaises(CommandError) as caught:
            call_command(
                "sync_events_to_echo",
                "--event-id",
                str(event.pk),
                "--adopt",
                "   ",
                stdout=StringIO(),
            )

        self.assertIn("--forget", str(caught.exception))
        # And the orphan is untouched, so the block still holds.
        self.assertEqual(
            EchoExperienceSync.objects.get(event=event).status,
            EchoExperienceSync.Status.ORPHANED,
        )


@override_settings(**ENABLED)
class DryRunNeverWritesTests(TestCase):
    """--dry-run is documented as writing nothing. Two options bypassed it."""

    def test_dry_run_refuses_to_resolve_an_orphan(self):
        # --adopt/--forget are dispatched before any dry-run handling, so
        # `--dry-run --forget` cleared the one state standing between an
        # untracked listing and a second one beside it.
        from django.core.management.base import CommandError

        event = make_event()
        sync = EchoExperienceSync.objects.create(
            event=event,
            status=EchoExperienceSync.Status.ORPHANED,
            last_error="no id came back",
        )

        with self.assertRaises(CommandError):
            call_command(
                "sync_events_to_echo",
                "--dry-run",
                "--forget",
                f"--event-id={event.pk}",
                stdout=StringIO(),
            )

        sync.refresh_from_db()
        self.assertEqual(sync.status, EchoExperienceSync.Status.ORPHANED)

    def test_dry_run_refuses_to_adopt_an_id(self):
        from django.core.management.base import CommandError

        event = make_event()
        sync = EchoExperienceSync.objects.create(
            event=event, status=EchoExperienceSync.Status.ORPHANED
        )

        with self.assertRaises(CommandError):
            call_command(
                "sync_events_to_echo",
                "--dry-run",
                "--adopt=exp-999",
                f"--event-id={event.pk}",
                stdout=StringIO(),
            )

        sync.refresh_from_db()
        self.assertEqual(sync.experience_id, "")
        self.assertEqual(sync.status, EchoExperienceSync.Status.ORPHANED)


@override_settings(**ENABLED)
class WithdrawnThenCancelledTests(TestCase):
    """The one lifecycle transition that reached no sweep at all."""

    def _withdrawn_then_republished_as_cancelled(self):
        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()

        # Unpublished, so the listing comes down and the row reads WITHDRAWN.
        event.is_published = False
        event.save()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()
        self.assertEqual(event.echo_sync.status, EchoExperienceSync.Status.WITHDRAWN)

        # Then published again — but cancelled. The event is public and
        # upcoming, so a cancellation notice belongs on echo.lu.
        MeetupEvent.objects.filter(pk=event.pk).update(
            is_published=True, is_cancelled=True
        )
        return MeetupEvent.objects.select_related("echo_sync").get(pk=event.pk)

    def test_a_withdrawn_listing_still_gets_its_cancellation_notice(self):
        # `should_publish` is false (cancelled) and the row is WITHDRAWN, so
        # this used to short-circuit to "skipped" and the notice was never
        # sent — the listing stayed down with nothing explaining why.
        event = self._withdrawn_then_republished_as_cancelled()

        client = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=client), "withdrawn")

        self.assertEqual([call[0] for call in client.calls], ["cancel"])
        event.refresh_from_db()
        self.assertEqual(event.echo_sync.status, EchoExperienceSync.Status.CANCELLED)

    def test_the_sweep_selects_it(self):
        # `publishable` drops it for being cancelled and `withdrawable` drops
        # it for being withdrawn, so without a term of its own the save-signal
        # is the only thing that could ever send the notice — and an admin
        # bulk publish emits no signal at all.
        event = self._withdrawn_then_republished_as_cancelled()
        self.assertIn(event, list(echo_lu.events_needing_sync()))

    def test_an_ordinary_withdrawn_event_is_still_left_alone(self):
        # The short-circuit still has to hold for every other withdrawn row,
        # or each sweep would re-send a take-down that already happened.
        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()
        event.is_published = False
        event.save()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()

        client = FakeClient()
        self.assertEqual(echo_lu.sync_event(event, client=client), "skipped")
        self.assertEqual(client.calls, [])


@override_settings(**ENABLED)
class StaleWithdrawalTests(TestCase):
    """The withdraw path lacked the publish path's own "never mind" check."""

    def test_an_automatic_withdrawal_aborts_if_the_event_became_eligible(self):
        # An automatic take-down decided from an older save can queue behind a
        # sync that republished the listing and left it correct. Going ahead
        # unpublishes what that newer save just put up and writes WITHDRAWN
        # over its SYNCED, so the listing stays missing for up to an hour.
        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()

        # Stand in for the caller that decided to withdraw: its copy says
        # unpublished, while the winner has since republished the event.
        stale = MeetupEvent.objects.get(pk=event.pk)
        stale.is_published = False
        self.assertTrue(MeetupEvent.objects.get(pk=event.pk).is_published)

        client = FakeClient()
        self.assertEqual(echo_lu.withdraw_event(stale, client=client), "skipped")
        self.assertEqual(client.calls, [])
        event.refresh_from_db()
        self.assertEqual(event.echo_sync.status, EchoExperienceSync.Status.SYNCED)

    def test_an_explicit_withdrawal_is_not_talked_out_of_it(self):
        # The whole point of an explicit take-down is that it removes a
        # listing whose event still qualifies, so eligibility must not abort
        # it — otherwise the coach's Remove action would become a no-op on
        # exactly the events they use it for.
        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()

        client = FakeClient()
        self.assertEqual(
            echo_lu.withdraw_event(event, client=client, explicit=True),
            "withdrawn",
        )
        self.assertEqual([call[0] for call in client.calls], ["unpublish"])
        event.refresh_from_db()
        self.assertEqual(event.echo_sync.status, EchoExperienceSync.Status.SUPPRESSED)

    def test_a_pending_removal_still_goes_through(self):
        # `removal_requested` is an explicit instruction that survived a failed
        # attempt. The retry arrives with the event still saying "publish me" —
        # that is the state the flag exists for — so it must not abort either.
        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()
        EchoExperienceSync.objects.filter(event=event).update(removal_requested=True)
        event = MeetupEvent.objects.select_related("echo_sync").get(pk=event.pk)

        client = FakeClient()
        self.assertEqual(echo_lu.withdraw_event(event, client=client), "withdrawn")
        self.assertEqual([call[0] for call in client.calls], ["unpublish"])


@override_settings(**ENABLED)
class DeleteEventTests(TestCase):
    """Deleting the event destroys the only record of the listing's id."""

    def setUp(self):
        # The take-down budget lives in a module-level threading.local with no
        # transaction or request boundary of its own, so it survives between
        # tests on the same worker. The fake-clock tests below would otherwise
        # inherit real monotonic values — or leave them behind — and the
        # outcome would depend on which test happened to run first.
        _reset_echo_delete_burst()

    def _delete(self, *events):
        """Delete and run the on-commit hook, as a real transaction would."""
        with self.captureOnCommitCallbacks(execute=True):
            for event in events:
                event.delete()

    def test_deleting_an_event_takes_its_listing_down(self):
        # EchoExperienceSync cascades with the event, and the experience id is
        # the only handle we have. Once the row is gone the listing is still
        # public and nothing left in the database can name it.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        event.refresh_from_db()

        with mock.patch.object(echo_lu, "EchoLuClient", return_value=client):
            self._delete(event)

        self.assertEqual([call[0] for call in client.calls], ["create", "unpublish"])
        self.assertFalse(MeetupEvent.objects.filter(pk=event.pk).exists())

    def test_a_rolled_back_delete_withdraws_nothing(self):
        # Doing the take-down at pre_delete meant an aborted delete still
        # pulled the listing, leaving an event that exists and a listing that
        # does not — and no row saying so, since the rollback restored it.
        event = make_event()
        client = FakeClient()
        echo_lu.sync_event(event, client=client)
        event.refresh_from_db()

        class Rollback(Exception):
            pass

        # Model.delete() blanks the instance pk, so hold on to it.
        event_pk = event.pk
        with mock.patch.object(echo_lu, "EchoLuClient", return_value=client):
            with self.assertRaises(Rollback):
                with transaction.atomic():
                    event.delete()
                    raise Rollback()

        self.assertEqual([call[0] for call in client.calls], ["create"])
        self.assertTrue(MeetupEvent.objects.filter(pk=event_pk).exists())

    def test_a_bulk_delete_is_bounded_and_names_what_it_could_not_reach(self):
        # A bulk delete arrives as N take-downs, back to back. A per-call
        # timeout alone would multiply by N, so they share a wall-clock budget.
        # The events are gone by then, so anything the budget misses has
        # nowhere left to be retried from — the log is the last copy of the id.
        #
        # Budget 30s, per-call timeout 5s, so the cut-off is 25s in. At 13s a
        # call, the third one finds it spent.
        events = []
        for index in range(3):
            event = make_event(title=f"Event {index}")
            echo_lu.sync_event(
                event, client=FakeClient(create_response={"id": f"exp-{index}"})
            )
            event.refresh_from_db()
            events.append(event)

        clock = [0]
        sent = []

        slow = FakeClient()
        slow.unpublish_experience = lambda experience_id: (
            clock.__setitem__(0, clock[0] + 13),
            sent.append(experience_id),
            {},
        )[-1]

        with mock.patch.object(echo_lu, "EchoLuClient", return_value=slow), mock.patch(
            "crush_lu.signals.time.monotonic", side_effect=lambda: clock[0]
        ), self.assertLogs("crush_lu.signals", level="ERROR") as logs:
            self._delete(*events)

        self.assertEqual(sent, ["exp-0", "exp-1"])
        self.assertTrue(any("exp-2" in line for line in logs.output))
        self.assertFalse(
            MeetupEvent.objects.filter(pk__in=[e.pk for e in events]).exists()
        )

    def test_a_later_unrelated_delete_gets_its_own_budget(self):
        # The budget is a thread-local with no transaction boundary, so a
        # second delete arriving on the same worker inherits whatever the
        # first one spent. Delimiting the burst by time-since-its-first-call
        # left a window — from `budget - timeout` to `budget` — in which that
        # second delete was refused AND did not start a burst of its own, so
        # it silently skipped a take-down whose row was already gone. A burst
        # is an unbroken run of callbacks, so an idle gap ends it.
        first = make_event(title="First")
        echo_lu.sync_event(first, client=FakeClient(create_response={"id": "exp-a"}))
        first.refresh_from_db()
        second = make_event(title="Second")
        echo_lu.sync_event(second, client=FakeClient(create_response={"id": "exp-b"}))
        second.refresh_from_db()

        clock = [0]
        sent = []

        client = FakeClient()
        client.unpublish_experience = lambda experience_id: (
            clock.__setitem__(0, clock[0] + 1),
            sent.append(experience_id),
            {},
        )[-1]

        with mock.patch.object(
            echo_lu, "EchoLuClient", return_value=client
        ), mock.patch("crush_lu.signals.time.monotonic", side_effect=lambda: clock[0]):
            self._delete(first)
            # A coach deletes another event 25 seconds later. That lands 26s
            # after the first burst started: past the 25s cut-off (budget 30,
            # timeout 5) but short of the 30s that used to start a fresh one,
            # so it was refused and its listing left live and untracked.
            clock[0] += 25
            self._delete(second)

        self.assertEqual(sent, ["exp-a", "exp-b"])

    def test_a_failed_take_down_still_lets_the_delete_through(self):
        # Refusing the delete would be the worse failure — a coach unable to
        # remove an event they need gone. It has to be loud instead.
        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()

        failing = FakeClient(error=echo_lu.EchoLuError("down", status_code=503))
        with mock.patch.object(
            echo_lu, "EchoLuClient", return_value=failing
        ), self.assertLogs("crush_lu.signals", level="ERROR") as logs:
            self._delete(event)

        self.assertFalse(MeetupEvent.objects.filter(pk=event.pk).exists())
        self.assertTrue(any("exp-123" in line for line in logs.output))

    def test_an_unlisted_event_deletes_without_calling_echo(self):
        event = make_event()
        client = FakeClient()
        with mock.patch.object(echo_lu, "EchoLuClient", return_value=client):
            self._delete(event)
        self.assertEqual(client.calls, [])

    def test_an_already_withdrawn_listing_is_not_taken_down_again(self):
        # The id is kept on a withdrawn row on purpose, so "has an id" is not
        # "is up". Dialling anyway spends the batch's shared budget on a no-op,
        # and in a bulk delete enough of them push a live listing's callback
        # past the budget — after its row is gone.
        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()
        event.is_published = False
        event.save()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()
        self.assertEqual(event.echo_sync.status, EchoExperienceSync.Status.WITHDRAWN)

        client = FakeClient()
        with mock.patch.object(echo_lu, "EchoLuClient", return_value=client):
            self._delete(event)
        self.assertEqual(client.calls, [])

    def test_a_cancelled_notice_is_still_taken_down_on_delete(self):
        # The counter-case, and the reason the skip above is a two-item list
        # rather than "anything not synced": a cancelled listing is still on
        # public display, and the delete is the last moment anything can reach
        # it. Miss this and the country keeps a cancellation notice for an
        # event that no longer exists.
        event = make_event()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()
        MeetupEvent.objects.filter(pk=event.pk).update(is_cancelled=True)
        event.refresh_from_db()
        echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()
        self.assertEqual(event.echo_sync.status, EchoExperienceSync.Status.CANCELLED)

        client = FakeClient()
        with mock.patch.object(echo_lu, "EchoLuClient", return_value=client):
            self._delete(event)
        self.assertEqual([call[0] for call in client.calls], ["unpublish"])

    def test_an_id_written_since_the_event_was_loaded_is_still_found(self):
        # The receiver used to read `instance.echo_sync`, which can be a copy
        # cached when the event was loaded. A first publish that lands after
        # that — it holds the row locked across its POST, so a racing delete
        # reads the pre-create blank id — left the receiver concluding there
        # was no listing, and the cascade then deleted the row the winner had
        # just written the id into. Reading the row fresh, under its lock, is
        # what makes the answer current.
        event = make_event()
        stale = EchoExperienceSync.objects.create(event=event)
        event.echo_sync = stale  # the copy from before the create landed
        EchoExperienceSync.objects.filter(pk=stale.pk).update(
            experience_id="exp-late", status=EchoExperienceSync.Status.SYNCED
        )
        self.assertEqual(event.echo_sync.experience_id, "")

        client = FakeClient()
        with mock.patch.object(echo_lu, "EchoLuClient", return_value=client):
            self._delete(event)

        self.assertEqual(client.calls, [("unpublish", "exp-late")])

    @override_settings(ECHO_LU_SYNC_ENABLED=False)
    def test_a_disabled_environment_still_names_the_stranded_listing(self):
        # Nothing can be taken down with the switch off, so the id has to reach
        # the log or it is lost with the row.
        event = make_event()
        with override_settings(**ENABLED):
            echo_lu.sync_event(event, client=FakeClient())
        event.refresh_from_db()

        with self.assertLogs("crush_lu.signals", level="WARNING") as logs:
            self._delete(event)

        self.assertTrue(any("exp-123" in line for line in logs.output))
