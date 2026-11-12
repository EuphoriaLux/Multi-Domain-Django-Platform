from datetime import date, time

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from crush_lu.models.crush_connect_cycle import (
    ConnectCoffeeDate,
    ConnectTemporaryChat,
    ConnectWeekSession,
    ConnectWeeklyRequest,
)
from crush_lu.models.events import MeetupEvent
from hub.admin import LocationAdmin
from hub.models import (
    OFFER_EVENT_TYPE_CHOICES,
    Location,
    LocationContact,
    PartnerOffer,
    PartnerOnboardingStep,
    PaymentOut,
)
from hub.partner_services import build_event_prefill

User = get_user_model()


class ThrottleIsolatedTestCase(TestCase):
    """DRF's user throttle counts per user id in the shared cache.

    SQLite rolls back primary keys but not the cache, so every test's staff user
    is user 1 and their request counts pile up on one xdist worker until an
    unrelated test (here a hub social test) gets a 429. Clear it before and
    after (AGENTS.md "Traps").
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)


def make_partner(**overrides):
    values = {
        "name": "Café Konrad",
        "address": "1 Rue Test, L-2229 Luxembourg",
        "city": "Luxembourg",
        "max_capacity": 60,
    }
    values.update(overrides)
    return Location.objects.create(**values)


def make_offer(partner, **overrides):
    values = {
        "location": partner,
        "name": "Thursday speed dating",
        "event_type": "speed_dating",
        "title_en": "Speed dating at Café Konrad",
    }
    values.update(overrides)
    return PartnerOffer.objects.create(**values)


class OfferContractTests(TestCase):
    def test_event_types_match_meetup_event(self):
        # The hub copy exists only to avoid an import cycle; it must not drift.
        self.assertEqual(OFFER_EVENT_TYPE_CHOICES, MeetupEvent.EVENT_TYPE_CHOICES)

    def test_prefill_fields_are_real_meetup_event_fields(self):
        offer = make_offer(make_partner())
        prefill = build_event_prefill(offer)
        meetup_fields = {field.name for field in MeetupEvent._meta.get_fields()}
        self.assertLessEqual(set(prefill["fields"]), meetup_fields)


class PartnerModelTests(TestCase):
    def test_deal_amounts_and_fee_cannot_be_negative_on_the_model(self):
        from django.core.exceptions import ValidationError

        for field in ("minimum_spend", "deposit_amount"):
            partner = make_partner(**{field: "-1.00"})
            with self.assertRaises(ValidationError) as caught:
                partner.full_clean()
            self.assertIn(field, caught.exception.message_dict)
            partner.delete()
        offer = make_offer(make_partner(), registration_fee="-5.00")
        with self.assertRaises(ValidationError) as caught:
            offer.full_clean()
        self.assertIn("registration_fee", caught.exception.message_dict)

    def test_unknown_canton_is_rejected_on_the_model(self):
        from django.core.exceptions import ValidationError

        partner = make_partner(canton="atlantis")
        with self.assertRaises(ValidationError) as caught:
            partner.full_clean()
        self.assertIn("canton", caught.exception.message_dict)
        partner.canton = "Luxembourg"
        partner.full_clean()
        partner.canton = ""
        partner.full_clean()

    def test_offer_capacity_is_checked_against_the_venue_in_model_clean(self):
        from django.core.exceptions import ValidationError

        partner = make_partner(max_capacity=20)
        offer = make_offer(partner, max_participants=100)
        with self.assertRaises(ValidationError) as caught:
            offer.full_clean()
        self.assertIn("max_participants", caught.exception.message_dict)

        offer.max_participants = 20
        offer.full_clean()

    def test_venue_capacity_cannot_drop_below_an_offer_in_model_clean(self):
        from django.core.exceptions import ValidationError

        partner = make_partner(max_capacity=60)
        make_offer(partner, max_participants=40)
        partner.max_capacity = 30
        with self.assertRaises(ValidationError) as caught:
            partner.full_clean()
        self.assertIn("max_capacity", caught.exception.message_dict)

        partner.max_capacity = 40
        partner.full_clean()

    def test_coordinates_are_bounded_on_the_model(self):
        from django.core.exceptions import ValidationError

        for field, value in (
            ("latitude", 999),
            ("latitude", -91),
            ("longitude", -500),
            ("longitude", 181),
        ):
            partner = make_partner(**{field: value})
            with self.assertRaises(ValidationError) as caught:
                partner.full_clean()
            self.assertIn(field, caught.exception.message_dict)
            partner.delete()
        valid = make_partner(latitude="49.6116", longitude="6.1319")
        valid.full_clean()

    def test_only_one_primary_contact_per_partner(self):
        partner = make_partner()
        LocationContact.objects.create(location=partner, name="A", is_primary=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocationContact.objects.create(location=partner, name="B", is_primary=True)

    def test_primary_contact_falls_back_to_first_contact(self):
        partner = make_partner()
        self.assertIsNone(partner.primary_contact)
        LocationContact.objects.create(location=partner, name="Zed")
        self.assertEqual(partner.primary_contact.name, "Zed")
        LocationContact.objects.create(location=partner, name="Amy", is_primary=True)
        self.assertEqual(partner.primary_contact.name, "Amy")


class PartnerAPITests(ThrottleIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username="partner_coach", password="password123", is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)

    def test_create_composes_address_and_city_from_structured_fields(self):
        response = self.client.post(
            "/hub/locations",
            {
                "name": "Bar Neuf",
                "maxCapacity": 40,
                "addressStreet": "rue du Nord",
                "addressNumber": "7",
                "addressPostcode": "2229",
                "addressTown": "Luxembourg",
                "canton": "Luxembourg",
                "latitude": "49.611600",
                "longitude": "6.131900",
                "minimumSpend": "250.00",
                "revenueSharePercent": "15",
                "contacts": [
                    {"name": "Lina", "role": "Manager", "email": "l@bar.example"},
                    {"name": "Tom", "isPrimary": True},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["address"], "rue du Nord 7, L-2229 Luxembourg")
        self.assertEqual(response.data["city"], "Luxembourg")
        self.assertEqual(response.data["minimumSpend"], 250.0)
        self.assertEqual(response.data["primaryContact"]["name"], "Tom")
        self.assertEqual(len(response.data["contacts"]), 2)
        self.assertEqual(response.data["partnershipStage"], "Prospect")

    def test_create_requires_an_address_and_capacity(self):
        response = self.client.post("/hub/locations", {"name": "Empty"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("maxCapacity", response.data)
        self.assertIn("address", response.data)

    def test_create_rejects_unknown_canton_and_bad_postcode(self):
        response = self.client.post(
            "/hub/locations",
            {
                "name": "Bad",
                "maxCapacity": 10,
                "address": "x",
                "city": "y",
                "canton": "atlantis",
                "addressPostcode": "22",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("canton", response.data)
        self.assertIn("addressPostcode", response.data)

    def test_patch_updates_and_validates_seated_against_stored_capacity(self):
        partner = make_partner(max_capacity=40)
        bad = self.client.patch(
            f"/hub/locations/{partner.pk}", {"seatedCapacity": 50}, format="json"
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("seatedCapacity", bad.data)

        ok = self.client.patch(
            f"/hub/locations/{partner.pk}",
            {"partnershipStage": "Active", "lastContactDate": ""},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(ok.data["partnershipStage"], "Active")
        self.assertEqual(ok.data["lastContactDate"], "")

    def test_patch_contacts_replaces_the_set_and_moves_the_primary(self):
        partner = make_partner()
        keep = LocationContact.objects.create(
            location=partner, name="Keep", is_primary=True
        )
        LocationContact.objects.create(location=partner, name="Drop")

        response = self.client.patch(
            f"/hub/locations/{partner.pk}",
            {
                "contacts": [
                    {"id": keep.pk, "name": "Keep", "isPrimary": False},
                    {"name": "New", "isPrimary": True},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        names = {c["name"]: c["isPrimary"] for c in response.data["contacts"]}
        self.assertEqual(names, {"Keep": False, "New": True})

    def test_patch_contacts_rejects_foreign_ids_and_two_primaries(self):
        partner = make_partner()
        other = make_partner(name="Other")
        stranger = LocationContact.objects.create(location=other, name="Stranger")

        foreign = self.client.patch(
            f"/hub/locations/{partner.pk}",
            {"contacts": [{"id": stranger.pk, "name": "x"}]},
            format="json",
        )
        two = self.client.patch(
            f"/hub/locations/{partner.pk}",
            {
                "contacts": [
                    {"name": "a", "isPrimary": True},
                    {"name": "b", "isPrimary": True},
                ]
            },
            format="json",
        )
        self.assertEqual(foreign.status_code, 400)
        self.assertEqual(two.status_code, 400)
        self.assertTrue(LocationContact.objects.filter(pk=stranger.pk).exists())

    def test_put_is_not_allowed_on_detail(self):
        partner = make_partner()
        response = self.client.put(f"/hub/locations/{partner.pk}", {}, format="json")
        self.assertEqual(response.status_code, 405)

    def test_delete_blocked_when_payments_exist_otherwise_removes(self):
        with_payments = make_partner(name="Paid")
        PaymentOut.objects.create(
            date="2026-09-01",
            amount="100.00",
            payee="Paid",
            category="Venue",
            location=with_payments,
        )
        blocked = self.client.delete(f"/hub/locations/{with_payments.pk}")
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue(Location.objects.filter(pk=with_payments.pk).exists())

        plain = make_partner(name="Plain")
        removed = self.client.delete(f"/hub/locations/{plain.pk}")
        self.assertEqual(removed.status_code, 204)
        self.assertFalse(Location.objects.filter(pk=plain.pk).exists())

    def test_partner_endpoints_are_staff_only(self):
        partner = make_partner()
        self.client.force_authenticate(user=User.objects.create_user("member"))
        for method, path in (
            ("get", "/hub/locations"),
            ("post", "/hub/locations"),
            ("get", f"/hub/locations/{partner.pk}"),
            ("get", f"/hub/locations/{partner.pk}/offers"),
            ("get", f"/hub/locations/{partner.pk}/onboarding"),
        ):
            self.assertEqual(getattr(self.client, method)(path).status_code, 403, path)

    def test_list_does_not_query_per_partner(self):
        def add_partner(index):
            partner = make_partner(name=f"Bar {index}")
            LocationContact.objects.create(location=partner, name="c", is_primary=True)
            make_offer(partner)

        def count_list_queries():
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get("/hub/locations")
            return len(queries), response

        add_partner(0)
        count_list_queries()  # warm one-off lookups (Sites framework)
        one_partner, _ = count_list_queries()
        for index in range(1, 5):
            add_partner(index)
        five_partners, response = count_list_queries()

        self.assertEqual(five_partners, one_partner)
        self.assertEqual(len(response.data["items"]), 5)
        self.assertEqual(response.data["items"][0]["offerCount"], 1)


class PartnerOfferAPITests(ThrottleIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username="offer_coach", password="password123", is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)
        self.partner = make_partner(
            address_street="rue du Nord",
            address_number="7",
            address_postcode="2229",
            address_town="Luxembourg",
            canton="Luxembourg",
        )

    def url(self, suffix=""):
        return f"/hub/locations/{self.partner.pk}/offers{suffix}"

    def test_a_partner_can_hold_several_offers_of_the_same_type(self):
        for name, fee in (
            ("Weekday speed dating", "15"),
            ("Weekend speed dating", "25"),
        ):
            response = self.client.post(
                self.url(),
                {
                    "name": name,
                    "eventType": "speed_dating",
                    "title": {"en": name, "fr": f"{name} FR"},
                    "registrationFee": fee,
                    "weekdays": [3] if "Weekday" in name else [5, 6],
                    "startTime": "19:30",
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)

        listing = self.client.get(self.url())
        self.assertEqual(listing.status_code, 200)
        fees = sorted(item["registrationFee"] for item in listing.data["items"])
        self.assertEqual(fees, [15.0, 25.0])
        first = listing.data["items"][0]
        self.assertEqual(first["locationId"], str(self.partner.pk))
        self.assertEqual(set(first["title"]), {"en", "de", "fr"})
        self.assertEqual(first["startTime"], "19:30")

    def test_offer_reads_do_not_depend_on_the_request_language(self):
        offer = make_offer(self.partner, title_fr="Titre")
        response = self.client.get(self.url(f"/{offer.pk}"), HTTP_ACCEPT_LANGUAGE="fr")
        self.assertEqual(response.data["title"]["en"], "Speed dating at Café Konrad")
        self.assertEqual(response.data["title"]["fr"], "Titre")
        self.assertEqual(response.data["title"]["de"], "")

    def test_create_validates_fields(self):
        response = self.client.post(
            self.url(),
            {
                "eventType": "nope",
                "durationMinutes": 10**7,
                "weekdays": [9],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        for key in ("name", "eventType", "durationMinutes", "weekdays"):
            self.assertIn(key, response.data, key)

    def test_create_validates_title_and_age_range(self):
        response = self.client.post(
            self.url(),
            {
                "name": "x",
                "eventType": "mixer",
                "title": {"fr": "seulement"},
                "minAge": 40,
                "maxAge": 30,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.data)
        self.assertIn("maxAge", response.data)

    def test_patch_one_language_keeps_the_others_and_protects_english(self):
        offer = make_offer(self.partner, title_fr="Titre")
        ok = self.client.patch(
            self.url(f"/{offer.pk}"), {"title": {"de": "Titel"}}, format="json"
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(
            ok.data["title"],
            {"en": "Speed dating at Café Konrad", "de": "Titel", "fr": "Titre"},
        )
        blank = self.client.patch(
            self.url(f"/{offer.pk}"), {"title": {"en": " "}}, format="json"
        )
        self.assertEqual(blank.status_code, 400)

    def test_offer_is_scoped_to_its_partner(self):
        other = make_partner(name="Other")
        offer = make_offer(other)
        response = self.client.get(self.url(f"/{offer.pk}"))
        self.assertEqual(response.status_code, 404)

    def test_offers_of_an_unknown_partner_404(self):
        self.assertEqual(
            self.client.get("/hub/locations/99999/offers").status_code, 404
        )

    def test_delete_offer(self):
        offer = make_offer(self.partner)
        response = self.client.delete(self.url(f"/{offer.pk}"))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(PartnerOffer.objects.filter(pk=offer.pk).exists())

    def test_event_draft_prefills_meetup_event_fields_and_creates_nothing(self):
        offer = make_offer(
            self.partner,
            title_fr="Titre FR",
            description_en="Fun night",
            registration_fee="15.00",
            max_participants=24,
            weekdays=[3],
            start_time=time(19, 30),
            languages=["en", "fr"],
        )
        before = MeetupEvent.objects.count()

        response = self.client.get(f"/hub/offers/{offer.pk}/event-draft")

        self.assertEqual(response.status_code, 200)
        fields = response.data["fields"]
        self.assertEqual(fields["event_type"], "speed_dating")
        self.assertEqual(fields["location"], "Café Konrad")
        self.assertEqual(fields["address_street"], "rue du Nord")
        self.assertEqual(fields["address_postcode"], "2229")
        self.assertEqual(fields["canton"], "Luxembourg")
        self.assertEqual(fields["max_participants"], 24)
        self.assertEqual(fields["title_fr"], "Titre FR")
        self.assertEqual(fields["description_en"], "Fun night")
        self.assertEqual(fields["languages"], ["en", "fr"])
        self.assertEqual(response.data["suggestedWeekdays"], [3])
        self.assertEqual(response.data["suggestedStartTime"], "19:30")
        self.assertEqual(MeetupEvent.objects.count(), before)


class PartnerOnboardingAPITests(ThrottleIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username="onboard_coach", password="password123", is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)
        self.partner = make_partner()
        self.url = f"/hub/locations/{self.partner.pk}/onboarding"

    def test_a_new_partner_has_every_step_open(self):
        response = self.client.get(self.url)
        items = response.data["items"]
        self.assertEqual(
            [item["key"] for item in items], list(PartnerOnboardingStep.Key.values)
        )
        self.assertFalse(any(item["done"] for item in items))
        self.assertEqual(PartnerOnboardingStep.objects.count(), 0)

    def test_marking_done_records_who_and_when_and_can_be_undone(self):
        response = self.client.patch(
            self.url,
            {"steps": [{"key": "terms_agreed", "done": True, "notes": "Signed"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        step = next(i for i in response.data["items"] if i["key"] == "terms_agreed")
        self.assertTrue(step["done"])
        self.assertEqual(step["doneBy"], "onboard_coach")
        self.assertEqual(step["notes"], "Signed")
        first_done_at = PartnerOnboardingStep.objects.get(key="terms_agreed").done_at

        # Re-sending done=True must not restamp who/when.
        self.client.patch(
            self.url, {"steps": [{"key": "terms_agreed", "done": True}]}, format="json"
        )
        self.assertEqual(
            PartnerOnboardingStep.objects.get(key="terms_agreed").done_at, first_done_at
        )

        undone = self.client.patch(
            self.url, {"steps": [{"key": "terms_agreed", "done": False}]}, format="json"
        )
        step = next(i for i in undone.data["items"] if i["key"] == "terms_agreed")
        self.assertFalse(step["done"])
        self.assertEqual(step["notes"], "Signed")

    def test_unknown_step_key_is_rejected_and_progress_is_reported(self):
        bad = self.client.patch(
            self.url, {"steps": [{"key": "bogus", "done": True}]}, format="json"
        )
        self.assertEqual(bad.status_code, 400)

        self.client.patch(
            self.url,
            {"steps": [{"key": "contact_made", "done": True}]},
            format="json",
        )
        listing = self.client.get("/hub/locations")
        self.assertEqual(
            listing.data["items"][0]["onboardingProgress"], {"done": 1, "total": 8}
        )


class PartnerReviewRegressionTests(ThrottleIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username="review_coach", password="password123", is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)

    def offer_payload(self, **overrides):
        payload = {"name": "Offer", "eventType": "mixer", "title": {"en": "Mixer"}}
        payload.update(overrides)
        return payload

    def test_delete_blocked_when_a_connect_coffee_date_uses_the_partner(self):
        partner = make_partner(name="Coffee bar")
        alice = User.objects.create_user("alice", "a@example.com")
        bob = User.objects.create_user("bob", "b@example.com")
        request = ConnectWeeklyRequest.objects.create(
            session=ConnectWeekSession.objects.create(user=alice),
            requester=alice,
            recipient=bob,
            status=ConnectWeeklyRequest.Status.ACCEPTED,
        )
        chat = ConnectTemporaryChat.objects.create(
            request=request, participant_1=alice, participant_2=bob
        )
        ConnectCoffeeDate.objects.create(
            chat=chat,
            proposer=alice,
            venue_location=partner,
            proposed_date=date(2026, 12, 1),
        )

        response = self.client.delete(f"/hub/locations/{partner.pk}")

        self.assertEqual(response.status_code, 409)
        self.assertTrue(Location.objects.filter(pk=partner.pk).exists())

    def test_patching_the_town_moves_the_legacy_city_unless_given(self):
        partner = make_partner(city="Luxembourg", address_street="rue du Nord")
        moved = self.client.patch(
            f"/hub/locations/{partner.pk}", {"addressTown": "Esch"}, format="json"
        )
        self.assertEqual(moved.data["city"], "Esch")
        self.assertIn("Esch", moved.data["address"])

        explicit = self.client.patch(
            f"/hub/locations/{partner.pk}",
            {"addressTown": "Differdange", "city": "Custom"},
            format="json",
        )
        self.assertEqual(explicit.data["city"], "Custom")

    def test_partner_numbers_are_bounded(self):
        partner = make_partner()
        for payload in (
            {"minimumSpend": "-1"},
            {"depositAmount": "-0.01"},
            {"latitude": "91"},
            {"latitude": "-91"},
            {"longitude": "181"},
            {"longitude": "-181"},
        ):
            response = self.client.patch(
                f"/hub/locations/{partner.pk}", payload, format="json"
            )
            self.assertEqual(response.status_code, 400, payload)
        ok = self.client.patch(
            f"/hub/locations/{partner.pk}",
            {"latitude": "49.6116", "longitude": "-6.1319", "minimumSpend": "0"},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.data)

    def test_offer_title_length_is_capped_per_language(self):
        partner = make_partner()
        response = self.client.post(
            f"/hub/locations/{partner.pk}/offers",
            self.offer_payload(title={"en": "ok", "fr": "x" * 201}),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.data)

    def test_offer_ages_follow_meetup_event_bounds(self):
        partner = make_partner()
        for payload in ({"minAge": 17}, {"maxAge": 121}):
            response = self.client.post(
                f"/hub/locations/{partner.pk}/offers",
                self.offer_payload(**payload),
                format="json",
            )
            self.assertEqual(response.status_code, 400, payload)

    def test_offer_gender_caps_are_all_or_none_and_within_total(self):
        partner = make_partner()
        url = f"/hub/locations/{partner.pk}/offers"
        partial = self.client.post(
            url, self.offer_payload(maxParticipantsM=5), format="json"
        )
        too_many = self.client.post(
            url,
            self.offer_payload(
                maxParticipants=10,
                maxParticipantsM=5,
                maxParticipantsF=5,
                maxParticipantsNb=5,
            ),
            format="json",
        )
        fine = self.client.post(
            url,
            self.offer_payload(
                maxParticipants=15,
                maxParticipantsM=5,
                maxParticipantsF=5,
                maxParticipantsNb=5,
            ),
            format="json",
        )
        self.assertEqual(partial.status_code, 400)
        self.assertEqual(too_many.status_code, 400)
        self.assertEqual(fine.status_code, 201, fine.data)

        # PATCH is validated against the stored values too.
        shrink = self.client.patch(
            f"{url}/{fine.data['id']}", {"maxParticipants": 12}, format="json"
        )
        self.assertEqual(shrink.status_code, 400)

    def test_model_clean_mirrors_the_serializer_rules(self):
        from django.core.exceptions import ValidationError

        offer = make_offer(make_partner(), min_age=17)
        with self.assertRaises(ValidationError):
            offer.full_clean(exclude=["location"])

    def test_gender_caps_without_total_use_the_model_default_capacity(self):
        partner = make_partner()
        url = f"/hub/locations/{partner.pk}/offers"
        # Default capacity is 20: 5+5+5 fits, 8+8+8 does not (400, not a 500).
        fits = self.client.post(
            url,
            self.offer_payload(
                maxParticipantsM=5, maxParticipantsF=5, maxParticipantsNb=5
            ),
            format="json",
        )
        over = self.client.post(
            url,
            self.offer_payload(
                maxParticipantsM=8, maxParticipantsF=8, maxParticipantsNb=8
            ),
            format="json",
        )
        self.assertEqual(fits.status_code, 201, fits.data)
        self.assertEqual(over.status_code, 400)

    def test_text_fields_respect_the_model_column_lengths(self):
        partner = make_partner()
        for payload in (
            {"website": "https://example.com/" + "a" * 200},
            {"city": "c" * 256},
            {"country": "c" * 101},
            {"canton": "c" * 201},
        ):
            response = self.client.patch(
                f"/hub/locations/{partner.pk}", payload, format="json"
            )
            self.assertEqual(response.status_code, 400, list(payload))

    def test_name_and_city_fit_the_event_fields_they_prefill(self):
        base = {"maxCapacity": 10, "address": "x", "city": "y"}
        long_name = self.client.post(
            "/hub/locations", {**base, "name": "n" * 201}, format="json"
        )
        long_city = self.client.post(
            "/hub/locations", {**base, "name": "ok", "city": "c" * 101}, format="json"
        )
        self.assertEqual(long_name.status_code, 400)
        self.assertEqual(long_city.status_code, 400)

    def test_offer_capacity_cannot_exceed_the_venue(self):
        partner = make_partner(max_capacity=20)
        url = f"/hub/locations/{partner.pk}/offers"
        too_big = self.client.post(
            url, self.offer_payload(maxParticipants=100), format="json"
        )
        fits = self.client.post(
            url, self.offer_payload(maxParticipants=20), format="json"
        )
        self.assertEqual(too_big.status_code, 400)
        self.assertEqual(fits.status_code, 201, fits.data)

        patched = self.client.patch(
            f"{url}/{fits.data['id']}", {"maxParticipants": 21}, format="json"
        )
        self.assertEqual(patched.status_code, 400)

    def test_lowering_venue_capacity_below_an_offer_is_rejected(self):
        partner = make_partner(max_capacity=60)
        make_offer(partner, max_participants=40)
        lower = self.client.patch(
            f"/hub/locations/{partner.pk}", {"maxCapacity": 30}, format="json"
        )
        same = self.client.patch(
            f"/hub/locations/{partner.pk}", {"maxCapacity": 40}, format="json"
        )
        self.assertEqual(lower.status_code, 400)
        self.assertIn("maxCapacity", lower.data)
        self.assertEqual(same.status_code, 200, same.data)

    def test_age_range_uses_model_defaults_on_create(self):
        partner = make_partner()
        url = f"/hub/locations/{partner.pk}/offers"
        # Default maxAge is 99 and default minAge is 18: both would violate the
        # DB constraint if only one side were supplied.
        for payload in ({"minAge": 100}, {"maxAge": 17}):
            response = self.client.post(
                url, self.offer_payload(**payload), format="json"
            )
            self.assertEqual(response.status_code, 400, payload)

    def test_blank_legacy_address_is_treated_as_absent(self):
        created = self.client.post(
            "/hub/locations",
            {
                "name": "Structured",
                "maxCapacity": 10,
                "address": "",
                "addressStreet": "rue du Nord",
                "addressNumber": "7",
                "addressPostcode": "2229",
                "addressTown": "Luxembourg",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["address"], "rue du Nord 7, L-2229 Luxembourg")

        partner = make_partner(address="Keep me, 1234 Town")
        kept = self.client.patch(
            f"/hub/locations/{partner.pk}", {"address": ""}, format="json"
        )
        self.assertEqual(kept.status_code, 200, kept.data)
        self.assertEqual(kept.data["address"], "Keep me, 1234 Town")

    def test_offer_languages_include_luxembourgish_but_nothing_else(self):
        partner = make_partner()
        url = f"/hub/locations/{partner.pk}/offers"
        lux = self.client.post(
            url, self.offer_payload(languages=["lu", "en"]), format="json"
        )
        bad = self.client.post(url, self.offer_payload(languages=["es"]), format="json")
        self.assertEqual(lux.status_code, 201, lux.data)
        self.assertEqual(lux.data["languages"], ["lu", "en"])
        self.assertEqual(bad.status_code, 400)
        # The draft hands the same list to MeetupEvent.languages.
        draft = self.client.get(f"/hub/offers/{lux.data['id']}/event-draft")
        self.assertEqual(draft.data["fields"]["languages"], ["lu", "en"])

    def test_a_lone_structured_field_is_not_a_complete_address(self):
        base = {"name": "Stub", "maxCapacity": 10, "city": "Luxembourg"}
        number_only = self.client.post(
            "/hub/locations", {**base, "addressNumber": "7"}, format="json"
        )
        self.assertEqual(number_only.status_code, 400)
        self.assertIn("address", number_only.data)

        street_only = self.client.post(
            "/hub/locations",
            {**base, "name": "Street", "addressStreet": "rue du Nord"},
            format="json",
        )
        self.assertEqual(street_only.status_code, 201, street_only.data)
        # No structured town: the city stands in for it.
        self.assertEqual(street_only.data["address"], "rue du Nord, Luxembourg")

    def test_patching_a_lone_structured_field_keeps_the_legacy_address(self):
        partner = make_partner(address="Keep me, 1234 Town")
        response = self.client.patch(
            f"/hub/locations/{partner.pk}", {"addressNumber": "7"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["address"], "Keep me, 1234 Town")
        self.assertEqual(response.data["addressNumber"], "7")

    def test_new_contacts_need_a_name_even_on_patch(self):
        partner = make_partner()
        LocationContact.objects.create(location=partner, name="Keep", is_primary=True)
        for contacts in ([{}], [{"role": "Chef"}], [{"name": "  "}]):
            response = self.client.patch(
                f"/hub/locations/{partner.pk}", {"contacts": contacts}, format="json"
            )
            self.assertEqual(response.status_code, 400, contacts)
        # Nothing was replaced.
        self.assertEqual(
            list(partner.contacts.values_list("name", flat=True)), ["Keep"]
        )

    def test_contact_sync_refreshes_updated_at(self):
        from datetime import timedelta

        from django.utils import timezone

        partner = make_partner()
        old = timezone.now() - timedelta(days=30)
        first = LocationContact.objects.create(
            location=partner, name="First", is_primary=True
        )
        second = LocationContact.objects.create(location=partner, name="Second")
        LocationContact.objects.filter(pk__in=[first.pk, second.pk]).update(
            updated_at=old
        )

        response = self.client.patch(
            f"/hub/locations/{partner.pk}",
            {
                "contacts": [
                    {"id": first.pk, "name": "First", "isPrimary": False},
                    {"id": second.pk, "name": "Second renamed", "isPrimary": True},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_primary)  # flag moved
        self.assertGreater(first.updated_at, old)
        self.assertGreater(second.updated_at, old)  # renamed + now primary

    def test_integers_beyond_the_database_range_are_a_400_not_a_500(self):
        # PostgreSQL integer columns top out at 2**31 - 1; SQLite does not
        # care, so only the serializer bound can catch this in tests.
        too_big = 2**31
        partner = make_partner()
        for payload in ({"maxCapacity": too_big}, {"seatedCapacity": too_big}):
            response = self.client.patch(
                f"/hub/locations/{partner.pk}", payload, format="json"
            )
            self.assertEqual(response.status_code, 400, payload)

        created = self.client.post(
            "/hub/locations",
            {"name": "Big", "address": "x", "city": "y", "maxCapacity": too_big},
            format="json",
        )
        self.assertEqual(created.status_code, 400)

        url = f"/hub/locations/{partner.pk}/offers"
        for field in (
            "maxParticipants",
            "maxParticipantsM",
            "maxParticipantsF",
            "maxParticipantsNb",
            "durationMinutes",
            "minAge",
        ):
            response = self.client.post(
                url, self.offer_payload(**{field: too_big}), format="json"
            )
            self.assertEqual(response.status_code, 400, field)

    def test_blank_city_falls_back_to_the_stored_town_or_is_rejected(self):
        with_town = make_partner(city="Luxembourg", address_town="Esch")
        fallback = self.client.patch(
            f"/hub/locations/{with_town.pk}", {"city": ""}, format="json"
        )
        self.assertEqual(fallback.status_code, 200, fallback.data)
        self.assertEqual(fallback.data["city"], "Esch")

        without_town = make_partner(name="No town", city="Luxembourg")
        rejected = self.client.patch(
            f"/hub/locations/{without_town.pk}", {"city": ""}, format="json"
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("city", rejected.data)
        without_town.refresh_from_db()
        self.assertEqual(without_town.city, "Luxembourg")

        # Omitting city leaves it alone.
        untouched = self.client.patch(
            f"/hub/locations/{without_town.pk}", {"notes": "hello"}, format="json"
        )
        self.assertEqual(untouched.status_code, 200)
        self.assertEqual(untouched.data["city"], "Luxembourg")


class ContactInlineAdminTests(ThrottleIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = User.objects.create_superuser("root", "root@example.com", "x")
        self.partner = make_partner()
        self.first = LocationContact.objects.create(
            location=self.partner, name="First", is_primary=True
        )
        self.second = LocationContact.objects.create(
            location=self.partner, name="Second"
        )

    def formset(self, rows):
        """The real inline formset, bound to ``rows`` (one dict per contact form)."""
        request = RequestFactory().post("/")
        request.user = self.admin_user
        inline = LocationAdmin(Location, admin.site).get_inline_instances(
            request, self.partner
        )[0]
        data = {
            "contacts-TOTAL_FORMS": str(len(rows)),
            "contacts-INITIAL_FORMS": str(sum(1 for row in rows if "id" in row)),
            "contacts-MIN_NUM_FORMS": "0",
            "contacts-MAX_NUM_FORMS": "1000",
        }
        for index, row in enumerate(rows):
            for key, value in row.items():
                data[f"contacts-{index}-{key}"] = value
            data[f"contacts-{index}-location"] = str(self.partner.pk)
        return inline.get_formset(request, self.partner)(
            data, instance=self.partner, prefix="contacts"
        )

    def test_primary_can_move_between_saved_contacts(self):
        formset = self.formset(
            [
                {"id": self.first.pk, "name": "First"},
                {"id": self.second.pk, "name": "Second", "is_primary": "on"},
            ]
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertFalse(self.first.is_primary)
        self.assertTrue(self.second.is_primary)

    def test_an_unchanged_primary_stays_primary(self):
        formset = self.formset(
            [
                {"id": self.first.pk, "name": "First", "is_primary": "on"},
                {"id": self.second.pk, "name": "Second renamed"},
            ]
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_primary)

    def test_two_primaries_in_one_submission_are_rejected(self):
        formset = self.formset(
            [
                {"id": self.first.pk, "name": "First", "is_primary": "on"},
                {"id": self.second.pk, "name": "Second", "is_primary": "on"},
            ]
        )
        self.assertFalse(formset.is_valid())
        self.assertIn(
            "Only one contact can be primary.", str(formset.non_form_errors())
        )


class PartnerClaimAPITests(ThrottleIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.coach_a = User.objects.create_user(
            username="wesley",
            email="wesley@crush.lu",
            first_name="Wesley",
            last_name="Coach",
            password="password123",
            is_staff=True,
        )
        self.coach_b = User.objects.create_user(
            username="natascha",
            email="natascha@crush.lu",
            first_name="Natascha",
            last_name="Coach",
            password="password123",
            is_staff=True,
        )
        self.superuser = User.objects.create_superuser(
            username="tom",
            email="tom@crush.lu",
            first_name="Tom",
            last_name="Admin",
            password="password123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.coach_a)
        self.venue = make_partner(
            name="De Gudde Wëllen",
            address="17 Rue du Saint-Esprit, L-1475 Luxembourg",
            city="Luxembourg",
            address_postcode="1475",
            partnership_stage=Location.PartnershipStage.PROSPECT,
        )

    def test_coach_can_claim_unclaimed_prospect_and_second_coach_gets_409(self):
        claim_url = f"/hub/locations/{self.venue.pk}/claim"
        res = self.client.post(claim_url, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["claimedById"], str(self.coach_a.pk))
        self.assertEqual(res.data["claimedByName"], "Wesley Coach")
        self.assertEqual(res.data["claimedByEmail"], "wesley@crush.lu")
        self.assertTrue(res.data["claimedByMe"])
        self.assertEqual(res.data["accountManager"], "Wesley Coach")
        self.assertIsNotNone(res.data.get("claimedAt"))

        # Second coach attempting to claim the same venue receives 409 Conflict
        self.client.force_authenticate(user=self.coach_b)
        conflict = self.client.post(claim_url, format="json")
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("Wesley Coach", conflict.data["detail"])
        self.assertEqual(conflict.data["claimedByName"], "Wesley Coach")

        # Listing as coach_b shows claimedByMe=False
        listing = self.client.get("/hub/locations")
        item = next(i for i in listing.data["items"] if i["id"] == str(self.venue.pk))
        self.assertFalse(item["claimedByMe"])
        self.assertEqual(item["claimedByName"], "Wesley Coach")

    def test_only_claiming_coach_or_superuser_can_release_or_force_claim(self):
        claim_url = f"/hub/locations/{self.venue.pk}/claim"
        self.client.post(claim_url, format="json")

        # Coach B cannot release Coach A's venue
        self.client.force_authenticate(user=self.coach_b)
        forbidden = self.client.delete(claim_url)
        self.assertEqual(forbidden.status_code, 403)

        # Superuser can force-claim
        self.client.force_authenticate(user=self.superuser)
        forced = self.client.post(claim_url, {"force": True}, format="json")
        self.assertEqual(forced.status_code, 200)
        self.assertEqual(forced.data["claimedById"], str(self.superuser.pk))
        self.assertEqual(forced.data["accountManager"], "Tom Admin")

        # Superuser can release
        released = self.client.delete(claim_url)
        self.assertEqual(released.status_code, 200)
        self.assertNotIn("claimedById", released.data)
        self.assertEqual(released.data["claimedByName"], "")
        self.assertEqual(released.data["accountManager"], "")
        self.assertFalse(released.data["claimedByMe"])

    def test_duplicate_guard_blocks_manual_recreation_of_existing_venue(self):
        self.client.post(f"/hub/locations/{self.venue.pk}/claim", format="json")
        dup = self.client.post(
            "/hub/locations",
            {
                "name": "de gudde wëllen",
                "maxCapacity": 50,
                "addressStreet": "Rue du Saint-Esprit",
                "addressNumber": "17",
                "addressPostcode": "1475",
                "addressTown": "Luxembourg",
            },
            format="json",
        )
        self.assertEqual(dup.status_code, 400)
        self.assertIn("name", dup.data)
        self.assertIn("Wesley Coach", str(dup.data["name"]))

    def test_partner_edit_from_a_stale_read_keeps_a_claim_committed_meanwhile(self):
        from hub.serializers_partners import LocationSerializer

        # PartnerDetailView reads without a row lock: model a PATCH whose read
        # happened before the claim committed (SQLite ignores locks anyway).
        stale = Location.objects.get(pk=self.venue.pk)
        claimed = self.client.post(
            f"/hub/locations/{self.venue.pk}/claim", format="json"
        )
        self.assertEqual(claimed.status_code, 200, claimed.data)

        serializer = LocationSerializer(
            stale, data={"notes": "Called the owner"}, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        self.venue.refresh_from_db()
        self.assertEqual(self.venue.notes, "Called the owner")
        self.assertEqual(self.venue.claimed_by, self.coach_a)
        self.assertIsNotNone(self.venue.claimed_at)
        self.assertEqual(self.venue.account_manager, "Wesley Coach")


class LuxembourgVenueMatchingTests(TestCase):
    """Import matching must never fold two distinct premises into one row."""

    @staticmethod
    def _osm(osm_id, name, **tags):
        from hub.venue_import import parse_osm_element

        return parse_osm_element(
            {
                "type": "node",
                "id": osm_id,
                "tags": {"name": name, "amenity": "restaurant", **tags},
            }
        )

    def test_known_but_different_postcodes_stay_two_venues(self):
        from hub.venue_import import upsert_venue_record

        manual = make_partner(
            name="Brasserie du Centre",
            city="Luxembourg",
            address_postcode="1111",
        )
        action, created = upsert_venue_record(
            self._osm(
                11,
                "Brasserie du Centre",
                **{"addr:postcode": "2222", "addr:city": "Luxembourg"},
            )
        )
        self.assertEqual(action, "created")
        self.assertNotEqual(created.pk, manual.pk)
        manual.refresh_from_db()
        self.assertEqual(manual.external_id, "")

    def test_city_fallback_still_matches_when_one_postcode_is_missing(self):
        from hub.venue_import import upsert_venue_record

        manual = make_partner(name="Brasserie du Centre", city="Luxembourg")
        action, matched = upsert_venue_record(
            self._osm(
                12,
                "Brasserie du Centre",
                **{"addr:postcode": "2222", "addr:city": "Luxembourg"},
            )
        )
        self.assertEqual(action, "updated")
        self.assertEqual(matched.pk, manual.pk)
        manual.refresh_from_db()
        self.assertEqual(manual.external_id, "osm:node:12")

    def test_two_osm_outlets_in_one_town_without_postcodes_stay_two_venues(self):
        from hub.venue_import import parse_osm_element, upsert_venue_record

        # Same name, same commune, no postcode: only the differing OSM ids say
        # these are two premises.
        def outlet(osm_id):
            return parse_osm_element(
                {"type": "node", "id": osm_id, "tags": {"name": "Café de la Gare"}},
                ["Ettelbruck"],
            )

        _, first = upsert_venue_record(outlet(21))
        action, second = upsert_venue_record(outlet(22))
        self.assertEqual(action, "created")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.city, "Ettelbruck")
        # Re-running the same element is still idempotent.
        action, again = upsert_venue_record(outlet(22))
        self.assertEqual(action, "skipped")
        self.assertEqual(again.pk, second.pk)


class LuxembourgVenueTownAndCantonTests(TestCase):
    """Town and canton come from the data, never from a "Luxembourg" default."""

    # Shape of OVERPASS_LUXEMBOURG_QUERY's output: the venues, then each
    # commune's boundary followed by the bare ids of the venues inside it.
    PAYLOAD = [
        {"type": "node", "id": 1, "tags": {"name": "Op der Bréck", "amenity": "pub"}},
        {
            "type": "node",
            "id": 2,
            "tags": {"name": "Grenzcafé", "amenity": "cafe", "addr:postcode": "9081"},
        },
        {"type": "node", "id": 3, "tags": {"name": "Nowhere Bar", "amenity": "bar"}},
        {
            "type": "relation",
            "id": 900,
            "tags": {"boundary": "administrative", "admin_level": "8", "name": "Wiltz"},
        },
        {"type": "node", "id": 1},
        {"type": "node", "id": 2},
        {
            "type": "relation",
            "id": 901,
            "tags": {
                "boundary": "administrative",
                "admin_level": "8",
                "name": "Ettelbruck",
            },
        },
        {"type": "node", "id": 2},
    ]

    def test_boundary_pass_is_split_from_the_venues(self):
        from hub.venue_import import split_osm_elements

        venues, communes = split_osm_elements(self.PAYLOAD)
        self.assertEqual([v["id"] for v in venues], [1, 2, 3])
        self.assertEqual(
            communes,
            {"osm:node:1": ["Wiltz"], "osm:node:2": ["Wiltz", "Ettelbruck"]},
        )

    def test_untagged_venue_takes_town_and_canton_from_its_commune(self):
        from hub.venue_import import parse_osm_element, split_osm_elements

        venues, communes = split_osm_elements(self.PAYLOAD)
        parsed = parse_osm_element(venues[0], communes["osm:node:1"])
        self.assertEqual(parsed["city"], "Wiltz")
        self.assertEqual(parsed["canton"], "Wiltz")
        self.assertEqual(parsed["address"], "Wiltz")

    def test_border_venue_prefers_the_commune_its_postcode_names(self):
        from crush_lu.services.echo_lu_postcodes import POSTCODE_TO_COMMUNE
        from hub.venue_import import parse_osm_element, split_osm_elements

        self.assertEqual(POSTCODE_TO_COMMUNE["9081"], "Ettelbruck")
        venues, communes = split_osm_elements(self.PAYLOAD)
        parsed = parse_osm_element(venues[1], communes["osm:node:2"])
        self.assertEqual(parsed["city"], "Ettelbruck")
        self.assertEqual(parsed["canton"], "Diekirch")

    def test_unknown_town_stays_blank_and_a_later_run_fills_it(self):
        from hub.venue_import import (
            parse_osm_element,
            split_osm_elements,
            upsert_venue_record,
        )

        venues, communes = split_osm_elements(self.PAYLOAD)
        # A file without the commune pass: nothing says where the venue is.
        action, row = upsert_venue_record(parse_osm_element(venues[0]))
        self.assertEqual(action, "created")
        self.assertEqual((row.city, row.address_town, row.canton), ("", "", ""))
        self.assertEqual(row.address, "")

        action, row = upsert_venue_record(
            parse_osm_element(venues[0], communes["osm:node:1"])
        )
        self.assertEqual(action, "updated")
        row.refresh_from_db()
        self.assertEqual(
            (row.city, row.address_town, row.canton, row.address),
            ("Wiltz", "Wiltz", "Wiltz", "Wiltz"),
        )

    def test_import_command_reads_the_commune_pass(self):
        import json
        import os
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump({"elements": self.PAYLOAD}, tmp)
        self.addCleanup(os.remove, tmp.name)
        out = StringIO()
        call_command("import_luxembourg_venues", osm_file=tmp.name, stdout=out)

        self.assertIn("3 OSM venue element(s); 2 placed in a commune", out.getvalue())
        rows = {
            row.external_id: (row.city, row.canton) for row in Location.objects.all()
        }
        self.assertEqual(
            rows,
            {
                "osm:node:1": ("Wiltz", "Wiltz"),
                "osm:node:2": ("Ettelbruck", "Diekirch"),
                "osm:node:3": ("", ""),
            },
        )

    def test_live_echo_scan_never_repoints_an_existing_venue_mapping(self):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        from crush_lu.models.echo_lu import EchoVenue

        EchoVenue.objects.create(
            key="scott's pub|1238", venue_id="echo-scotts-123", title="Scott's Pub"
        )
        duplicate = {
            "id": "echo-other-999",
            "title": "Scott's Pub",
            "location": {"address": {"postcode": "1238", "town": "Luxembourg"}},
        }
        fake_client = type("FakeClient", (), {"api_key": "test-key"})()
        out = StringIO()
        with (
            patch("crush_lu.services.echo_lu.EchoLuClient", return_value=fake_client),
            patch(
                "crush_lu.services.echo_lu.iter_venue_records",
                return_value=iter([duplicate]),
            ),
        ):
            call_command(
                "import_luxembourg_venues",
                skip_osm=True,
                with_echo_lu=True,
                stdout=out,
            )

        mapping = EchoVenue.objects.get(key="scott's pub|1238")
        self.assertEqual(mapping.venue_id, "echo-scotts-123")
        self.assertFalse(
            Location.objects.filter(external_id="echo:echo-other-999").exists()
        )
        self.assertEqual(
            Location.objects.filter(external_id="echo:echo-scotts-123").count(), 1
        )
        self.assertIn("kept echo-scotts-123, ignored echo-other-999", out.getvalue())


class LuxembourgVenueImportTests(TestCase):
    def test_osm_and_echo_lu_import_creates_prospects_and_preserves_claims(self):
        import json
        import tempfile
        from django.core.management import call_command
        from crush_lu.models.echo_lu import EchoVenue

        coach = User.objects.create_user(
            username="franz",
            first_name="Franz",
            last_name="Coach",
            is_staff=True,
        )
        EchoVenue.objects.create(
            key="scott's pub|1238",
            venue_id="echo-scotts-123",
            title="Scott's Pub",
        )

        sample_osm = {
            "elements": [
                {
                    "type": "node",
                    "id": 1001,
                    "lat": 49.610123,
                    "lon": 6.133456,
                    "tags": {
                        "name": "Scott's Pub",
                        "amenity": "pub",
                        "addr:street": "Bisserwee",
                        "addr:housenumber": "4",
                        "addr:postcode": "L-1238",
                        "addr:city": "Luxembourg",
                        "outdoor_seating": "yes",
                        "website": "www.scottspub.lu",
                        "phone": "+352 22 75 65",
                    },
                },
                {
                    "type": "way",
                    "id": 2002,
                    "center": {"lat": 49.502345, "lon": 5.948123},
                    "tags": {
                        "name": "Brasserie Rockhal",
                        "amenity": "restaurant",
                        "addr:postcode": "4361",
                        "cuisine": "burger;french",
                        "capacity": "120",
                    },
                },
                {
                    "type": "node",
                    "id": 3003,
                    "tags": {"amenity": "bar"},  # unnamed -> ignored
                },
            ]
        }

        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(sample_osm, tmp)
            tmp_path = tmp.name

        call_command(
            "import_luxembourg_venues",
            osm_file=tmp_path,
            with_echo_lu=True,
        )

        scotts = Location.objects.get(external_id="osm:node:1001")
        self.assertEqual(scotts.partnership_stage, Location.PartnershipStage.PROSPECT)
        self.assertEqual(scotts.address_postcode, "1238")
        self.assertEqual(scotts.canton, "Luxembourg")
        self.assertTrue(scotts.has_outdoor_space)
        self.assertEqual(scotts.website, "https://www.scottspub.lu")
        self.assertEqual(scotts.max_capacity, 40)
        self.assertIn("capacite-estimee", scotts.tags)
        self.assertIsNotNone(scotts.echo_venue)
        self.assertEqual(scotts.primary_contact.phone, "+352 22 75 65")

        rockhal = Location.objects.get(external_id="osm:way:2002")
        self.assertEqual(rockhal.city, "Esch-sur-Alzette")
        self.assertEqual(rockhal.canton, "Esch-sur-Alzette")
        self.assertEqual(rockhal.max_capacity, 120)
        self.assertTrue(rockhal.has_kitchen)
        self.assertNotIn("capacite-estimee", rockhal.tags)

        # Claim Scott's Pub and advance stage to Negotiating; re-run import must not overwrite
        scotts.claimed_by = coach
        scotts.account_manager = "Franz Coach"
        scotts.partnership_stage = Location.PartnershipStage.NEGOTIATING
        scotts.save()

        call_command(
            "import_luxembourg_venues",
            osm_file=tmp_path,
            with_echo_lu=True,
        )
        scotts.refresh_from_db()
        self.assertEqual(scotts.claimed_by, coach)
        self.assertEqual(scotts.account_manager, "Franz Coach")
        self.assertEqual(
            scotts.partnership_stage, Location.PartnershipStage.NEGOTIATING
        )
