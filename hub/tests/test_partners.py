from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from crush_lu.models.crush_connect_cycle import (
    ConnectCoffeeDate,
    ConnectTemporaryChat,
    ConnectWeekSession,
    ConnectWeeklyRequest,
)
from crush_lu.models.events import MeetupEvent
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
        partner = make_partner(city="Luxembourg")
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
