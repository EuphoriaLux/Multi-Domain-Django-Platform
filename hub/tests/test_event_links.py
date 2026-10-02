from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory
from django.http import Http404
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from crush_lu.admin.events import MeetupEventAdmin
from crush_lu.admin.site import crush_admin_site
from crush_lu.models import EventRegistration, MeetupEvent
from hub.partner_services import build_event_prefill
from hub.views_event_links import PartnerEventsView
from hub.tests.test_partners import ThrottleIsolatedTestCase, make_offer, make_partner


class EventLinkTests(ThrottleIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.partner = make_partner()
        self.other = make_partner(name="Other venue")
        self.offer = make_offer(self.partner)
        self.staff = get_user_model().objects.create_user(
            "event_link_staff", is_staff=True
        )
        self.staff.user_permissions.add(
            Permission.objects.get(codename="change_meetupevent")
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)
        self.event = self.make_event()
        self.link_url = f"/hub/events/{self.event.pk}/partner-link"
        self.list_url = f"/hub/locations/{self.partner.pk}/events"

    def make_event(self, **overrides):
        values = dict(
            title_en="Historical title",
            description_en="Historical description",
            event_type="mixer",
            location="Café Konrad",
            address="Original address",
            date_time=timezone.now() + timedelta(days=20),
            registration_deadline=timezone.now() + timedelta(days=19),
            registration_fee="27.50",
            max_participants=24,
        )
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def test_link_changes_only_attribution(self):
        before = MeetupEvent.objects.filter(pk=self.event.pk).values().get()
        with patch("crush_lu.models.events.MeetupEvent.save") as save:
            response = self.client.patch(
                self.link_url,
                {
                    "partnerId": self.partner.pk,
                    "offerId": self.offer.pk,
                },
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        save.assert_not_called()
        after = MeetupEvent.objects.filter(pk=self.event.pk).values().get()
        changed = {key for key in before if before[key] != after[key]}
        self.assertEqual(changed, {"partner_id", "offer_id"})
        self.assertEqual(response.data["offerId"], str(self.offer.pk))
        audit = LogEntry.objects.get(object_id=str(self.event.pk))
        self.assertEqual(audit.user_id, self.staff.pk)
        self.assertIn("Event content preserved", audit.change_message)

    def test_wrong_offer_and_unknown_content_are_rejected(self):
        for payload in (
            {"partnerId": self.other.pk, "offerId": self.offer.pk},
            {"partnerId": self.partner.pk, "registration_fee": "0.00"},
            {"offerId": self.offer.pk},
        ):
            self.assertEqual(
                self.client.patch(self.link_url, payload, format="json").status_code,
                400,
            )
        self.event.refresh_from_db()
        self.assertIsNone(self.event.partner_id)

    def test_existing_other_partner_is_not_overwritten(self):
        MeetupEvent.objects.filter(pk=self.event.pk).update(partner=self.other)
        response = self.client.patch(
            self.link_url, {"partnerId": self.partner.pk}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.event.refresh_from_db()
        self.assertEqual(self.event.partner_id, self.other.pk)

    def test_staff_requires_event_change_permission(self):
        self.staff.user_permissions.clear()
        self.assertEqual(
            self.client.patch(
                self.link_url, {"partnerId": self.partner.pk}, format="json"
            ).status_code,
            403,
        )

    def test_non_staff_and_anonymous_cannot_read_or_write(self):
        member = get_user_model().objects.create_user("event_link_member")
        for user in (member, None):
            self.client.force_authenticate(user)
            for url in (self.list_url, self.link_url):
                self.assertIn(self.client.get(url).status_code, (401, 403))
            self.assertIn(
                self.client.patch(
                    self.link_url, {"partnerId": self.partner.pk}, format="json"
                ).status_code,
                (401, 403),
            )

    def test_preview_unlinked_and_empty_partner(self):
        response = self.client.get(self.link_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["partnerId"])
        self.assertEqual(self.client.get(self.list_url).data, {"items": []})
        self.assertEqual(
            self.client.get("/hub/locations/999999/events").status_code, 404
        )

    def test_list_is_scoped_and_includes_drafts_cancelled_and_past(self):
        MeetupEvent.objects.filter(pk=self.event.pk).update(partner=self.partner)
        past = self.make_event(
            partner=self.partner,
            offer=self.offer,
            date_time=timezone.now() - timedelta(days=2),
        )
        cancelled = self.make_event(
            partner=self.partner, is_published=True, is_cancelled=True
        )
        self.make_event(partner=self.other)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["id"] for row in response.data["items"]},
            {str(self.event.pk), str(past.pk), str(cancelled.pk)},
        )
        self.assertEqual(response.data["items"][-1]["offerName"], self.offer.name)
        self.assertFalse(
            next(
                row for row in response.data["items"] if row["id"] == str(self.event.pk)
            )["isPublished"]
        )

    def test_counts_match_registration_statuses_without_n_plus_one(self):
        MeetupEvent.objects.filter(pk=self.event.pk).update(
            partner=self.partner, offer=self.offer
        )
        for index, status in enumerate(
            ("confirmed", "pending", "attended", "applied", "waitlist", "cancelled")
        ):
            user = get_user_model().objects.create_user(f"registered_{index}")
            EventRegistration.objects.create(event=self.event, user=user, status=status)
        for _ in range(3):
            self.make_event(partner=self.partner, offer=self.offer)
        request = APIRequestFactory().get(self.list_url)
        force_authenticate(request, self.staff)
        with self.assertNumQueries(2):
            response = PartnerEventsView.as_view()(request, pk=self.partner.pk)
        row = next(
            row for row in response.data["items"] if row["id"] == str(self.event.pk)
        )
        self.assertEqual(
            (
                row["seatHolders"],
                row["applications"],
                row["waitlisted"],
                row["attended"],
            ),
            (3, 1, 1, 1),
        )

    def test_model_validates_partner_offer_and_database_requires_partner(self):
        self.event.offer = self.offer
        with self.assertRaises(ValidationError):
            self.event.validate_partner_offer()
        with self.assertRaises(IntegrityError), transaction.atomic():
            MeetupEvent.objects.filter(pk=self.event.pk).update(offer=self.offer)
        self.event.partner = self.other
        with self.assertRaises(ValidationError):
            self.event.validate_partner_offer()

    def test_linked_partner_and_offer_are_protected_everywhere(self):
        MeetupEvent.objects.filter(pk=self.event.pk).update(
            partner=self.partner, offer=self.offer
        )
        for obj in (self.partner, self.offer):
            with self.assertRaises(ProtectedError):
                obj.delete()
        self.assertEqual(
            self.client.delete(f"/hub/locations/{self.partner.pk}").status_code, 409
        )
        self.assertEqual(
            self.client.delete(
                f"/hub/locations/{self.partner.pk}/offers/{self.offer.pk}"
            ).status_code,
            409,
        )

    def test_offer_updates_preserve_event_snapshot(self):
        MeetupEvent.objects.filter(pk=self.event.pk).update(
            partner=self.partner, offer=self.offer
        )
        before = MeetupEvent.objects.filter(pk=self.event.pk).values().get()
        self.offer.title_en = "New offer title"
        self.offer.registration_fee = "10.00"
        self.offer.save()
        self.partner.address = "New partner address"
        self.partner.save()
        self.assertEqual(
            MeetupEvent.objects.filter(pk=self.event.pk).values().get(), before
        )

    def test_backfill_preview_never_applies_candidates(self):
        output = StringIO()
        call_command("backfill_event_partners", stdout=output)
        self.assertIn(str(self.partner.pk), output.getvalue())
        self.event.refresh_from_db()
        self.assertIsNone(self.event.partner_id)
        with self.assertRaises(CommandError):
            call_command("backfill_event_partners", apply=True)

    def test_backfill_explicit_mapping_preview_apply_and_idempotence(self):
        mapping = f"{self.event.pk}:{self.partner.pk}"
        call_command("backfill_event_partners", link=[mapping], stdout=StringIO())
        self.event.refresh_from_db()
        self.assertIsNone(self.event.partner_id)
        before = MeetupEvent.objects.filter(pk=self.event.pk).values().get()
        for _ in range(2):
            call_command(
                "backfill_event_partners", link=[mapping], apply=True, stdout=StringIO()
            )
        after = MeetupEvent.objects.filter(pk=self.event.pk).values().get()
        self.assertEqual(
            {key for key in before if before[key] != after[key]}, {"partner_id"}
        )

    def test_backfill_invalid_batch_writes_nothing(self):
        for mappings in (
            [f"{self.event.pk}:{self.partner.pk}", "999999:999999"],
            [f"{self.event.pk}:{self.partner.pk}", f"{self.event.pk}:{self.other.pk}"],
            ["wrong"],
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "backfill_event_partners",
                    link=mappings,
                    apply=True,
                    stdout=StringIO(),
                )
        self.event.refresh_from_db()
        self.assertIsNone(self.event.partner_id)

    def test_admin_prefill_uses_server_offer_and_keeps_attribution(self):
        request = RequestFactory().get(
            "/crush-admin/crush_lu/meetupevent/add/",
            {
                "offer_id": self.offer.pk,
                "registration_fee": "999",
                "location": "Wrong",
            },
        )
        initial = MeetupEventAdmin(
            MeetupEvent, crush_admin_site
        ).get_changeform_initial_data(request)
        self.assertEqual(
            initial, {**initial, **build_event_prefill(self.offer)["fields"]}
        )
        self.assertEqual(initial["partner"], self.partner.pk)
        self.assertEqual(initial["offer"], self.offer.pk)
        self.assertEqual(initial["location"], self.partner.name)
        self.assertEqual(initial["registration_fee"], self.offer.registration_fee)

    def test_admin_rejects_missing_inactive_and_paused_presets(self):
        admin = MeetupEventAdmin(MeetupEvent, crush_admin_site)
        for value in ("invalid", "999999"):
            with self.assertRaises(Http404):
                admin.get_changeform_initial_data(
                    RequestFactory().get("/", {"offer_id": value})
                )
        request = RequestFactory().get("/", {"offer_id": self.offer.pk})
        self.offer.is_active = False
        self.offer.save()
        with self.assertRaises(Http404):
            admin.get_changeform_initial_data(request)
        self.offer.is_active = True
        self.offer.save()
        for stage in ("Paused", "Archived"):
            self.partner.partnership_stage = stage
            self.partner.save()
            with self.assertRaises(Http404):
                admin.get_changeform_initial_data(request)

    def test_invalid_payload_shape_is_a_validation_error(self):
        response = self.client.patch(
            self.link_url, [{"partnerId": self.partner.pk}], format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_backfill_never_reassigns_an_existing_partner(self):
        MeetupEvent.objects.filter(pk=self.event.pk).update(partner=self.other)
        with self.assertRaises(CommandError):
            call_command(
                "backfill_event_partners",
                link=[f"{self.event.pk}:{self.partner.pk}"],
                apply=True,
            )
        self.event.refresh_from_db()
        self.assertEqual(self.event.partner_id, self.other.pk)

    def test_api_pins_event_title_to_english(self):
        MeetupEvent.objects.filter(pk=self.event.pk).update(
            partner=self.partner, title_fr="Titre français"
        )
        response = self.client.get(self.list_url, HTTP_ACCEPT_LANGUAGE="fr")
        self.assertEqual(response.data["items"][0]["title"], "Historical title")
