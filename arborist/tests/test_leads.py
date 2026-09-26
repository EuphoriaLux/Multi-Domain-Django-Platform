import tempfile
import uuid
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from PIL import Image

from arborist.models import ArboristLead, LeadPhoto
from arborist.services.leads import notify_lead
from arborist.storage import AzurePrivateStorage, LocalPrivateStorage


def picture():
    output = BytesIO()
    photo = Image.new("RGB", (30, 30), "green")
    exif = Image.Exif()
    exif[270] = "private metadata"
    photo.save(output, format="JPEG", exif=exif)
    return SimpleUploadedFile(
        "garden.jpg", output.getvalue(), content_type="image/jpeg"
    )


class LeadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="arborist.lu")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        field = LeadPhoto._meta.get_field("image")
        self.storage_patch = patch.object(
            field, "storage", LocalPrivateStorage(location=self.directory.name)
        )
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)

    def data(self, language="en"):
        response = self.client.get(f"/{language}/kontakt/")
        return {
            "submission_id": str(response.context["form"].initial["submission_id"]),
            "name": "Tree Owner",
            "email": "owner@example.com",
            "postal_code": "6211",
            "message": "Tree near the terrace",
        }

    def create_lead(self, **overrides):
        data = self.data()
        data.update(overrides)
        response = self.client.post("/en/kontakt/", data)
        self.assertEqual(response.status_code, 302)
        return (
            ArboristLead.objects.get(submission_id=data["submission_id"]),
            response["Location"],
        )

    def analytics_version(self, *cookie_dates):
        """Register the analytics cookie group the way setup_cookie_groups
        does, one cookie per date, and return the group's version (its
        newest cookie's date), which the banner writes into its flag."""
        from cookie_consent.cache import delete_cache, get_cookie_group
        from cookie_consent.models import Cookie, CookieGroup

        group, _ = CookieGroup.objects.get_or_create(
            varname="analytics", defaults={"name": "Analytics"}
        )
        for created in cookie_dates:
            cookie = Cookie.objects.create(
                cookiegroup=group, name=f"_ga_{created:%Y%m%d}", domain=""
            )
            # auto_now_add ignores a value passed to create().
            Cookie.objects.filter(pk=cookie.pk).update(created=created)
        delete_cache()
        return get_cookie_group("analytics").get_version()

    def upload(self, url, **overrides):
        page = self.client.get(url)
        data = {
            "upload_id": str(page.context["photo_form"].initial["upload_id"]),
            "tree_label": "Oak",
            "category": "root",
            "image": picture(),
        }
        data.update(overrides)
        return self.client.post(url, data)

    def test_phone_only_lead_persists_and_email_is_after_commit(self):
        with patch("arborist.services.leads.send_domain_email", return_value=1) as send:
            with self.captureOnCommitCallbacks(execute=True):
                lead, _ = self.create_lead(email="", phone="+352621123456")
                send.assert_not_called()
            lead.refresh_from_db()
            self.assertEqual(send.call_count, 1)
            self.assertEqual(lead.customer_delivery, "skipped")
            self.assertEqual(lead.staff_delivery, "sent")

    def test_delivery_failure_does_not_lose_enquiry_and_retry_only_unsent(self):
        with patch(
            "arborist.services.leads.send_domain_email",
            side_effect=[RuntimeError("offline"), 1],
        ):
            with self.captureOnCommitCallbacks(execute=True):
                lead, url = self.create_lead()
        lead.refresh_from_db()
        self.assertEqual(lead.staff_delivery, "failed")
        self.assertEqual(lead.customer_delivery, "sent")
        with patch("arborist.services.leads.send_domain_email", return_value=1) as send:
            notify_lead(lead.pk)
            self.assertEqual(send.call_count, 1)
        self.assertContains(self.client.get(url), "Your enquiry is saved")

    def test_repeated_submission_does_not_duplicate_or_resend(self):
        data = self.data()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            first = self.client.post("/en/kontakt/", data)
            second = self.client.post("/en/kontakt/", data)
        self.assertEqual(first["Location"], second["Location"])
        self.assertEqual(ArboristLead.objects.count(), 1)
        self.assertEqual(len(callbacks), 1)

    def test_foreign_token_cannot_create_or_read_a_lead(self):
        data = self.data()
        other = Client(HTTP_HOST="arborist.lu")
        response = other.post("/en/kontakt/", data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ArboristLead.objects.exists())
        lead, url = self.create_lead()
        self.assertEqual(other.get(url).status_code, 404)
        self.assertEqual(
            other.post(url, {"action": "notes", "photo_notes": "changed"}).status_code,
            404,
        )
        self.assertEqual(other.get(f"/en/termin/?lead={lead.pk}").status_code, 404)

    def test_contact_required_and_honeypot(self):
        data = self.data()
        for extra in [
            {"email": "", "phone": ""},
            {"website": "spam"},
            {"postal_code": "12345"},
        ]:
            response = self.client.post("/en/kontakt/", {**data, **extra})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(ArboristLead.objects.exists())
            self.assertContains(response, 'value="Tree Owner"')

    def test_language_routes_and_no_required_photos(self):
        for language in ("en", "de", "fr"):
            data = self.data(language)
            response = self.client.post(f"/{language}/kontakt/", data)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith(f"/{language}/"))
            lead = ArboristLead.objects.get(submission_id=data["submission_id"])
            self.assertEqual(lead.language, language)
        self.assertEqual(LeadPhoto.objects.count(), 0)

    def test_photo_validation_metadata_and_private_access(self):
        lead, url = self.create_lead()
        self.assertEqual(self.upload(url).status_code, 302)
        photo = lead.photos.get()
        with photo.image.open("rb") as file:
            self.assertFalse(Image.open(file).getexif())
        with self.assertRaises(ValueError):
            _ = photo.image.url
        photo_url = f"/en/enquiry-photo/{photo.pk}/"
        response = self.client.get(photo_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        b"".join(response.streaming_content)
        other = Client(HTTP_HOST="arborist.lu")
        self.assertEqual(other.get(photo_url).status_code, 404)
        user = get_user_model().objects.create_user(
            username="lead-staff",
            email="staff@example.com",
            password="test",
            is_staff=True,
        )
        other.force_login(user)
        self.assertEqual(other.get(photo_url).status_code, 404)
        user.user_permissions.add(Permission.objects.get(codename="view_arboristlead"))
        response = other.get(photo_url)
        self.assertEqual(response.status_code, 200)
        b"".join(response.streaming_content)

    def test_fake_and_oversized_images_rejected(self):
        lead, url = self.create_lead()
        for file in [
            SimpleUploadedFile("fake.jpg", b"not an image", content_type="image/jpeg"),
            SimpleUploadedFile("large.jpg", b"x" * (8 * 1024 * 1024 + 1)),
        ]:
            self.assertEqual(self.upload(url, image=file).status_code, 200)
        self.assertFalse(lead.photos.exists())

    def test_upload_failure_preserves_lead(self):
        lead, url = self.create_lead()
        with patch.object(
            LeadPhoto._meta.get_field("image").storage,
            "save",
            side_effect=OSError("offline"),
        ):
            response = self.upload(url)
        self.assertContains(
            response, "Your enquiry is saved, but the photo could not be uploaded"
        )
        self.assertFalse(lead.photos.exists())

    def test_upload_replay_and_notes(self):
        lead, url = self.create_lead()
        token = self.client.get(url).context["photo_form"].initial["upload_id"]
        self.upload(url, upload_id=token)
        self.upload(url, upload_id=token)
        self.assertEqual(lead.photos.count(), 1)
        self.client.post(url, {"action": "notes", "photo_notes": "Unsafe to approach"})
        lead.refresh_from_db()
        self.assertEqual(lead.photo_notes, "Unsafe to approach")

    def test_consent_required_for_attribution_and_revocation(self):
        version = self.analytics_version(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.client.get("/en/?utm_source=google&utm_campaign=trees")
        lead, _ = self.create_lead()
        self.assertEqual(lead.first_attribution, {})
        # The cookies core/templates/includes/cookie_banner.html writes on
        # "accept all": JSON in cookie_consent, plus the server-side flag
        # carrying the group version the acceptance was given under.
        self.client.cookies["cookie_consent"] = (
            '{"essential":true,"analytics":true,'
            '"timestamp":"2026-03-01T12:00:00.000Z"}'
        )
        self.client.cookies["cookie_consent_analytics"] = f"accept:{version}"
        self.client.get(
            "/en/?utm_source=google&utm_campaign=trees&email=private@example.com"
        )
        self.client.get("/en/baumpflege/?utm_source=partner")
        lead, _ = self.create_lead()
        self.assertEqual(
            lead.first_attribution,
            {
                "utm_source": "google",
                "utm_campaign": "trees",
                "landing_path": "/en/",
            },
        )
        self.assertEqual(lead.last_attribution["utm_source"], "partner")
        self.client.cookies["cookie_consent_analytics"] = "decline"
        lead, _ = self.create_lead()
        self.assertEqual(lead.first_attribution, {})

    def test_versioned_banner_flag_alone_keeps_attribution(self):
        """What the banner writes today: ``accept:<group version>``. A check
        for a bare "accept" read every new acceptance as no consent."""
        version = self.analytics_version(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.client.cookies["cookie_consent_analytics"] = f"accept:{version}"
        self.client.get("/en/?utm_source=partner")
        lead, _ = self.create_lead()
        self.assertEqual(lead.first_attribution["utm_source"], "partner")

    def test_stale_acceptance_keeps_no_attribution(self):
        """A cookie added to the analytics group since the acceptance makes it
        undecided, as it is for the trackers: the banner asks again, and
        nothing is kept until the visitor answers."""
        old = self.analytics_version(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.client.cookies["cookie_consent_analytics"] = f"accept:{old}"
        self.client.get("/en/?utm_source=partner")
        self.assertEqual(
            self.create_lead()[0].first_attribution["utm_source"], "partner"
        )

        self.analytics_version(datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.client.get("/en/?utm_source=later")
        lead, _ = self.create_lead()
        self.assertEqual(lead.first_attribution, {})
        self.assertEqual(lead.last_attribution, {})

    def test_library_format_consent_still_honoured(self):
        # /cookies/ (django-cookie-consent's own pages) writes its own cookie,
        # "group=version|...". A visitor may hold only this.
        version = self.analytics_version(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.client.cookies["cookie_consent"] = f"analytics={version}"
        self.client.get("/en/?utm_source=newsletter")
        lead, _ = self.create_lead()
        self.assertEqual(lead.first_attribution["utm_source"], "newsletter")

    def test_invalid_upload_token_rejected(self):
        lead, url = self.create_lead()
        self.assertEqual(self.upload(url, upload_id=uuid.uuid4()).status_code, 200)
        self.assertFalse(lead.photos.exists())

    def test_booking_prefills_owned_enquiry(self):
        lead, _ = self.create_lead(service="faellung")
        response = self.client.get(f"/en/termin/?lead={lead.pk}")
        initial = response.context["form"].initial
        self.assertEqual(initial["name"], lead.name)
        self.assertEqual(initial["service_type"], "faellung")
        # An explicit ?service= still wins over the enquiry's choice.
        response = self.client.get(f"/en/termin/?lead={lead.pk}&service=beratung")
        self.assertEqual(response.context["form"].initial["service_type"], "beratung")

    def test_enquiry_pages_noindex(self):
        _, url = self.create_lead()
        response = self.client.get(url)
        self.assertContains(response, 'content="noindex, nofollow"')
        self.assertIn("no-store", response["Cache-Control"])

    def test_photo_cap_and_file_deletion_after_commit(self):
        lead, url = self.create_lead()
        self.upload(url)
        photo = lead.photos.get()
        name, storage = photo.image.name, photo.image.storage
        self.assertTrue(storage.exists(name))
        with self.captureOnCommitCallbacks(execute=True):
            photo.delete()
            self.assertTrue(storage.exists(name))
        self.assertFalse(storage.exists(name))
        LeadPhoto.objects.bulk_create(
            [
                LeadPhoto(
                    lead=lead,
                    upload_id=uuid.uuid4(),
                    category="root",
                    image="unused.jpg",
                )
                for _ in range(12)
            ]
        )
        self.assertContains(self.upload(url), "You can add up to 12 photos")
        self.assertEqual(lead.photos.count(), 12)

    def test_failed_database_write_removes_uploaded_file(self):
        lead, url = self.create_lead()
        with patch.object(
            LeadPhoto, "save", side_effect=RuntimeError("database failure")
        ):
            self.assertEqual(self.upload(url).status_code, 200)
        self.assertFalse(lead.photos.exists())
        from pathlib import Path

        self.assertEqual(list(Path(self.directory.name).rglob("*.jpg")), [])

    def test_azure_storage_rejects_public_container(self):
        storage = AzurePrivateStorage(
            account_name="test", account_key="test", azure_container="arborist-private"
        )
        storage._client = Mock()
        storage._client.get_container_properties.return_value = {
            "public_access": "blob"
        }
        with self.assertRaises(ValueError):
            storage._open("private.jpg")
        with self.assertRaises(ValueError):
            storage._save("private.jpg", picture())

    def test_booking_links_to_lead_without_changing_price(self):
        from arborist.tests.test_booking import BOOKING_POST
        from arborist.models import ArboristBooking

        lead, _ = self.create_lead()
        response = self.client.post(f"/en/termin/?lead={lead.pk}", BOOKING_POST)
        self.assertEqual(response.status_code, 302)
        booking = ArboristBooking.objects.get(lead=lead)
        self.assertEqual(booking.prepayment_amount, 50)

    def test_admin_can_review_photos_and_overdue_leads(self):
        from django.utils import timezone
        from datetime import timedelta

        lead, url = self.create_lead()
        self.upload(url)
        lead.follow_up_at = timezone.now() - timedelta(days=1)
        lead.save()
        user = get_user_model().objects.create_superuser(
            username="arborist-admin", email="admin@example.com", password="test"
        )
        self.client.force_login(user)
        response = self.client.get(
            f"/arborist-admin/arborist/arboristlead/{lead.pk}/change/"
        )
        self.assertContains(response, "View private photo")
        response = self.client.get("/arborist-admin/arborist/arboristlead/?overdue=yes")
        self.assertContains(response, lead.name)
