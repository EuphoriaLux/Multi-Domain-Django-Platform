import base64
from email import message_from_bytes, policy
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from django.utils import translation
from django.utils.translation import gettext

from azureproject.email_utils import html_to_plain_text, send_domain_email
from azureproject.graph_email_backend import GraphEmailBackend
from azureproject.adapters import MultiDomainAccountAdapter
from crush_lu.models import EmailBounceEvent, EmailSuppression
from crush_lu.services.email_bounces import classify_bounce, process_graph_bounce


class HTMLToPlainTextTests(TestCase):
    def test_removes_css_and_keeps_structure_and_links(self):
        plain = html_to_plain_text(
            "<style>.button { color: red; }</style><h1>Hello</h1>"
            '<p>First<br>Second</p><a href="https://crush.lu/x">Open</a>'
        )

        self.assertNotIn("color: red", plain)
        self.assertIn("Hello", plain)
        self.assertIn("First\nSecond", plain)
        self.assertIn("Open (https://crush.lu/x)", plain)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MultipartAndSuppressionTests(TestCase):
    def test_html_send_has_plain_alternative_reply_to_and_attachment(self):
        result = send_domain_email(
            subject="Hello",
            message="legacy plain",
            html_message="<style>.x{color:red}</style><p>Hello<br>world</p>",
            recipient_list=["member@example.com"],
            domain="crush.lu",
            attachments=[("invite.ics", "BEGIN:VCALENDAR", "text/calendar")],
        )

        self.assertEqual(result, 1)
        message = mail.outbox[0]
        self.assertEqual(message.body, "Hello\nworld")
        self.assertEqual(message.reply_to, ["support@crush.lu"])
        self.assertEqual(message.alternatives[0].mimetype, "text/html")
        self.assertEqual(message.attachments[0].filename, "invite.ics")

    def test_active_suppression_skips_send_case_insensitively(self):
        EmailSuppression.objects.create(email="BOUNCE@example.com")

        result = send_domain_email(
            subject="Skipped",
            message="Body",
            recipient_list=["bounce@example.com"],
            domain="crush.lu",
        )

        self.assertEqual(result, 0)
        self.assertEqual(mail.outbox, [])

    def test_inactive_suppression_allows_send(self):
        EmailSuppression.objects.create(email="member@example.com", is_active=False)

        result = send_domain_email(
            subject="Allowed",
            message="Body",
            recipient_list=["member@example.com"],
            domain="crush.lu",
        )

        self.assertEqual(result, 1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AllauthBrandContextTests(TestCase):
    def test_confirmation_is_branded_in_every_supported_language(self):
        Site.objects.update_or_create(
            id=1, defaults={"domain": "crush.lu", "name": "Crush.lu"}
        )
        user = get_user_model().objects.create_user(
            username="brand@example.com", email="brand@example.com"
        )
        request = RequestFactory().get("/", HTTP_HOST="crush.lu")
        adapter = MultiDomainAccountAdapter()
        expected_subjects = {
            "en": "Welcome to Crush.lu",
            "fr": "Bienvenue sur Crush.lu",
            "de": "Willkommen bei Crush.lu",
        }

        for language, expected in expected_subjects.items():
            with self.subTest(language=language), translation.override(language):
                request.LANGUAGE_CODE = language
                adapter.send_mail(
                    "account/email/email_confirmation_signup",
                    user.email,
                    {
                        "request": request,
                        "user": user,
                        "activate_url": "https://crush.lu/confirm/test/",
                    },
                )
                message = mail.outbox[-1]
                self.assertIn(expected, message.subject)
                self.assertIn("Crush.lu", message.body)
                self.assertIn("Crush.lu", message.alternatives[0].content)


class GraphMimeBackendTests(TestCase):
    @patch("requests.post")
    def test_sends_complete_mime_payload(self, post):
        post.return_value = Mock(status_code=202, text="")
        message = EmailMultiAlternatives(
            subject="Multipart",
            body="Plain body",
            from_email="Crush <noreply@crush.lu>",
            to=["to@example.com"],
            cc=["cc@example.com"],
            bcc=["bcc@example.com"],
            reply_to=["support@crush.lu"],
        )
        message.attach_alternative("<p>HTML body</p>", "text/html")
        message.attach("invite.ics", "BEGIN:VCALENDAR", "text/calendar")
        backend = GraphEmailBackend(
            tenant_id="tenant",
            client_id="client",
            client_secret="secret",
            from_email="noreply@crush.lu",
        )

        backend._send_message(message, "token")

        request = post.call_args
        self.assertEqual(request.kwargs["headers"]["Content-Type"], "text/plain")
        self.assertNotIn("json", request.kwargs)
        mime = message_from_bytes(
            base64.b64decode(request.kwargs["data"]), policy=policy.default
        )
        self.assertEqual(mime["Reply-To"], "support@crush.lu")
        self.assertEqual(mime["Bcc"], "bcc@example.com")
        self.assertEqual(
            mime.get_body(preferencelist=("plain",)).get_content().rstrip(),
            "Plain body",
        )
        self.assertEqual(
            mime.get_body(preferencelist=("html",)).get_content().rstrip(),
            "<p>HTML body</p>",
        )
        self.assertEqual(
            [part.get_filename() for part in mime.iter_attachments()], ["invite.ics"]
        )


class BounceClassificationTests(TestCase):
    def test_unambiguous_permanent_failure_is_suppressed_once(self):
        message = {
            "id": "graph-id",
            "internetMessageId": "<ndr-1@example.com>",
            "subject": "Undeliverable",
            "receivedDateTime": "2026-09-13T14:00:00Z",
            "body": {"content": "person@example.net wasn't found. Status 5.1.10."},
        }

        first = process_graph_bounce(message, apply=True)
        process_graph_bounce(message, apply=True)

        self.assertEqual(first.classification, "hard")
        self.assertEqual(EmailBounceEvent.objects.count(), 1)
        self.assertTrue(
            EmailSuppression.objects.filter(email="person@example.net").exists()
        )

    def test_temporary_or_ambiguous_failure_never_suppresses(self):
        soft = classify_bounce(
            "Delivery delayed", "person@example.net mailbox full; status 5.2.2"
        )
        ambiguous = classify_bounce(
            "Undeliverable",
            "one@example.net and two@example.net were not found; status 5.1.10",
        )

        self.assertEqual(soft.classification, "soft")
        self.assertEqual(ambiguous.classification, "unknown")

    @override_settings(CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED=False)
    def test_apply_command_is_feature_gated_before_graph_access(self):
        with self.assertRaisesMessage(
            CommandError, "CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED"
        ):
            call_command("process_email_bounces", apply=True, stdout=StringIO())


class EventEmailMarkupTests(TestCase):
    def test_event_description_and_address_render_cleanly(self):
        event = SimpleNamespace(
            title="Mixer",
            date_time=timezone.now(),
            location="Venue",
            full_address="1 Main Street, L-1234 Luxembourg",
            canton="Luxembourg",
            description="First paragraph\n\nSecond paragraph",
            registration_fee=0,
            registration_deadline=timezone.now(),
        )
        registration = SimpleNamespace(
            user=SimpleNamespace(first_name="Alex"),
            dietary_restrictions="",
            bringing_guest=False,
        )

        html = render_to_string(
            "crush_lu/emails/event_registration_confirmation.html",
            {
                "event": event,
                "registration": registration,
                "event_url": "https://crush.lu/en/events/1/",
                "cancel_url": "https://crush.lu/en/events/1/cancel/",
            },
        )

        self.assertEqual(html.count(event.full_address), 1)
        self.assertNotIn("Luxembourg, Luxembourg", html)
        self.assertNotIn("<strong><p>", html)
        self.assertIn("<p>First paragraph</p>", html)


class EmailTranslationTests(TestCase):
    def test_reviewed_french_and_german_copy(self):
        with translation.override("fr"):
            self.assertEqual(
                gettext("To choose a new password, click the button below:"),
                "Pour choisir un nouveau mot de passe, cliquez sur le bouton ci-dessous :",
            )
            self.assertEqual(gettext("Location:"), "Lieu :")

        with translation.override("de"):
            self.assertEqual(
                gettext("What you can do now:"), "Was du jetzt tun kannst:"
            )
