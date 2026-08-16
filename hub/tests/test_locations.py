from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from hub.models import Location, LocationContact

User = get_user_model()


class LocationModelTests(TestCase):
    def test_location_validates_capacity_and_list_fields(self):
        location = Location(
            name="Invalid venue",
            address="1 Test Street",
            city="Luxembourg City",
            max_capacity=40,
            seated_capacity=50,
            compatible_event_types=["Unsupported event"],
            tags=[""],
        )

        with self.assertRaises(ValidationError) as error:
            location.full_clean()

        self.assertIn("seated_capacity", error.exception.message_dict)
        self.assertIn("compatible_event_types", error.exception.message_dict)
        self.assertIn("tags", error.exception.message_dict)


class LocationAPITests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="location_coach",
            email="location-coach@crush.lu",
            password="password123",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)

    def test_staff_can_list_locations_with_frontend_contract(self):
        location = Location.objects.create(
            name="Maison du Vin",
            address="12 Rue de la Boucherie, 1247 Luxembourg",
            city="Luxembourg City",
            max_capacity=60,
            seated_capacity=32,
            has_private_room=True,
            has_sound_system=True,
            compatible_event_types=[
                Location.EventType.WINE_TASTING,
                Location.EventType.SPEED_DATING,
            ],
            partnership_stage=Location.PartnershipStage.ACTIVE,
            account_manager="Lina Weber",
            commercial_terms="20% commission on bar sales.",
            partner_since=date(2025, 2, 1),
            last_contact_date=date(2026, 5, 2),
            next_action="Confirm the next line-up",
            next_action_date=date(2026, 5, 15),
            notes="Strong fit for premium tastings.",
            tags=["Old Town", "Premium"],
        )
        LocationContact.objects.create(
            location=location,
            name="Camille Hoffmann",
            role="Manager",
            email="camille@maisonduvin.example",
            phone="+352 621 100 014",
        )

        response = self.client.get("/hub/locations")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "items": [
                    {
                        "id": str(location.pk),
                        "name": "Maison du Vin",
                        "address": "12 Rue de la Boucherie, 1247 Luxembourg",
                        "city": "Luxembourg City",
                        "country": "Luxembourg",
                        "maxCapacity": 60,
                        "seatedCapacity": 32,
                        "hasOutdoorSpace": False,
                        "hasKitchen": False,
                        "hasPrivateRoom": True,
                        "hasSoundSystem": True,
                        "compatibleEventTypes": [
                            "Wine tasting",
                            "Speed dating",
                        ],
                        "partnershipStage": "Active",
                        "primaryContact": {
                            "name": "Camille Hoffmann",
                            "role": "Manager",
                            "email": "camille@maisonduvin.example",
                            "phone": "+352 621 100 014",
                        },
                        "accountManager": "Lina Weber",
                        "commercialTerms": "20% commission on bar sales.",
                        "partnerSince": "2025-02-01",
                        "lastContactDate": "2026-05-02",
                        "nextAction": "Confirm the next line-up",
                        "nextActionDate": "2026-05-15",
                        "notes": "Strong fit for premium tastings.",
                        "tags": ["Old Town", "Premium"],
                    }
                ]
            },
        )

    def test_optional_null_fields_are_safe_for_frontend(self):
        Location.objects.create(
            name="New prospect",
            address="2 Test Street",
            city="Esch-sur-Alzette",
            max_capacity=50,
        )

        response = self.client.get("/hub/locations/")

        self.assertEqual(response.status_code, 200)
        item = response.data["items"][0]
        self.assertNotIn("seatedCapacity", item)
        self.assertNotIn("partnerSince", item)
        self.assertNotIn("nextActionDate", item)
        self.assertEqual(item["lastContactDate"], "")
        self.assertEqual(
            item["primaryContact"],
            {"name": "", "role": "", "email": "", "phone": ""},
        )

    def test_locations_endpoint_is_staff_only(self):
        member = User.objects.create_user(username="location_member")
        self.client.force_authenticate(user=member)

        response = self.client.get("/hub/locations")

        self.assertEqual(response.status_code, 403)

    def test_locations_endpoint_is_read_only(self):
        response = self.client.post("/hub/locations", {}, format="json")

        self.assertEqual(response.status_code, 405)
