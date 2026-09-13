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
from crush_lu.management.commands.process_email_bounces import _get_mailbox_messages
from crush_lu.models import EmailBounceEvent, EmailSuppression
from crush_lu.services.email_bounces import (
    classify_bounce,
    is_delivery_report,
    process_graph_bounce,
)


def delivery_report_mime(*records):
    records = records or (("person@example.net", "5.1.10", "failed"),)
    recipient_blocks = "".join(
        (
            f"Final-Recipient: rfc822; {recipient}\r\n"
            f"Action: {action}\r\n"
            f"Status: {status}\r\n\r\n"
        )
        for recipient, status, action in records
    )
    return (
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type="delivery-status"; '
        'boundary="dsn-boundary"\r\n\r\n'
        "--dsn-boundary\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Human-readable delivery report.\r\n"
        "--dsn-boundary\r\n"
        "Content-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; tenant.example\r\n\r\n"
        f"{recipient_blocks}"
        "--dsn-boundary--\r\n"
    ).encode()


def delivery_report_message(**overrides):
    message = {
        "id": "graph-id",
        "internetMessageId": "<ndr-1@example.com>",
        "subject": "Undeliverable",
        "receivedDateTime": "2026-09-13T14:00:00Z",
        "from": {
            "emailAddress": {
                "address": "MicrosoftExchange000000@tenant.onmicrosoft.com"
            }
        },
        "internetMessageHeaders": [
            {
                "name": "Content-Type",
                "value": "multipart/report; report-type=delivery-status",
            },
            {
                "name": "X-MS-Exchange-Organization-AuthAs",
                "value": "Internal",
            },
            {
                "name": "X-MS-Exchange-Organization-MessageDirectionality",
                "value": "Originating",
            },
        ],
        "body": {"content": "person@example.net wasn't found. Status 5.1.10."},
        "_raw_mime": delivery_report_mime(),
    }
    message.update(overrides)
    return message


class HTMLToPlainTextTests(TestCase):
    def test_removes_css_and_keeps_structure_and_links(self):
        plain = html_to_plain_text(
            "<style>.button { color: red; }</style><h1>Hello</h1>"
            '<p>First<br>Second</p><a href="https://crush.lu/x">Open</a>'
        )

        self.assertNotIn("color: red", plain)
        self.assertIn("Hello", plain)
        self.assertIn("First\nSecond", plain)
        self.assertIn("Open\nhttps://crush.lu/x", plain)


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
        self.assertEqual(message.body, "legacy plain")
        self.assertEqual(message.reply_to, ["support@crush.lu"])
        self.assertEqual(message.alternatives[0].mimetype, "text/html")
        self.assertEqual(message.attachments[0].filename, "invite.ics")

    def test_html_conversion_is_used_only_when_plain_body_is_empty(self):
        send_domain_email(
            subject="Fallback",
            message="",
            html_message="<style>.x{color:red}</style><p>Hello<br>world</p>",
            recipient_list=["member@example.com"],
            domain="crush.lu",
        )

        self.assertEqual(mail.outbox[0].body, "Hello\nworld")
        self.assertNotIn("color:red", mail.outbox[0].body)

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

    def test_display_name_recipient_is_filtered_by_mailbox_address(self):
        EmailSuppression.objects.create(email="bounce@example.com")

        result = send_domain_email(
            subject="Skipped",
            message="Body",
            recipient_list=["Bounced Member <bounce@example.com>"],
            domain="crush.lu",
        )

        self.assertEqual(result, 0)
        self.assertEqual(mail.outbox, [])

    @patch.dict("os.environ", {"CRUSH_DEFAULT_FROM_EMAIL": "sender@example.org"})
    def test_custom_crush_sender_still_applies_suppressions(self):
        EmailSuppression.objects.create(email="bounce@example.com")

        result = send_domain_email(
            subject="Skipped",
            message="Body",
            recipient_list=["bounce@example.com"],
            domain="crush.lu",
        )

        self.assertEqual(result, 0)
        self.assertEqual(mail.outbox, [])


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

    def test_requestless_mail_uses_the_platform_default_site(self):
        user = get_user_model().objects.create_user(
            username="requestless@example.com", email="requestless@example.com"
        )

        MultiDomainAccountAdapter().send_mail(
            "account/email/email_confirmation_signup",
            user.email,
            {
                "user": user,
                "activate_url": "https://powerup.lu/confirm/test/",
            },
        )

        self.assertIn("Power Up", mail.outbox[0].subject)
        self.assertIn("Power Up", mail.outbox[0].body)


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


@override_settings(CRUSH_EMAIL_BOUNCE_TRUSTED_DOMAINS=["tenant.onmicrosoft.com"])
class BounceClassificationTests(TestCase):
    def test_unambiguous_permanent_failure_is_suppressed_once(self):
        message = delivery_report_message()

        first = process_graph_bounce(message, apply=True)
        process_graph_bounce(message, apply=True)

        self.assertEqual(first.classification, "hard")
        self.assertEqual(EmailBounceEvent.objects.count(), 1)
        self.assertTrue(
            EmailSuppression.objects.filter(email="person@example.net").exists()
        )

    def test_temporary_or_ambiguous_failure_never_suppresses(self):
        soft = classify_bounce(
            delivery_report_message(
                _raw_mime=delivery_report_mime(
                    ("person@example.net", "5.2.2", "failed")
                )
            )
        )
        ambiguous = classify_bounce(
            delivery_report_message(
                _raw_mime=delivery_report_mime(
                    ("one@example.net", "5.1.10", "failed"),
                    ("two@example.net", "5.1.10", "failed"),
                )
            )
        )

        self.assertEqual(soft.classification, "soft")
        self.assertEqual(ambiguous.classification, "unknown")

    def test_unverified_message_cannot_be_applied(self):
        message = delivery_report_message(
            **{
                "from": {"emailAddress": {"address": "person@example.org"}},
                "internetMessageHeaders": [],
            }
        )

        self.assertFalse(is_delivery_report(message))
        with self.assertRaisesMessage(ValueError, "verified delivery report"):
            process_graph_bounce(message, apply=True)
        self.assertEqual(EmailBounceEvent.objects.count(), 0)

    def test_spoofed_microsoft_local_part_from_untrusted_domain_is_rejected(self):
        message = delivery_report_message(
            **{
                "from": {
                    "emailAddress": {"address": "MicrosoftExchange123@attacker.example"}
                }
            }
        )

        self.assertFalse(is_delivery_report(message))
        with self.assertRaisesMessage(ValueError, "verified delivery report"):
            process_graph_bounce(message, apply=True)

    def test_trusted_sender_without_internal_exchange_auth_is_rejected(self):
        message = delivery_report_message(
            internetMessageHeaders=[
                {
                    "name": "Content-Type",
                    "value": "multipart/report; report-type=delivery-status",
                }
            ]
        )

        self.assertFalse(is_delivery_report(message))

    def test_trusted_container_without_structured_dsn_mime_is_rejected(self):
        message = delivery_report_message(
            _raw_mime=b"Content-Type: text/plain\r\n\r\nNot a DSN"
        )

        self.assertFalse(is_delivery_report(message))
        with self.assertRaisesMessage(ValueError, "verified delivery report"):
            process_graph_bounce(message, apply=True)

    @override_settings(
        CRUSH_EMAIL_BOUNCE_MAILBOXES=[],
        CRUSH_NEWSLETTER_FROM_EMAIL="news@example.org",
    )
    @patch("crush_lu.services.email_bounces.get_domain_email_config")
    def test_configured_sender_addresses_are_excluded_from_candidates(self, config):
        config.return_value = {
            "DEFAULT_FROM_EMAIL": "Campaign <campaign@example.org>",
            "REPLY_TO_EMAIL": "Help <help@example.org>",
        }
        result = classify_bounce(
            delivery_report_message(
                _raw_mime=delivery_report_mime(
                    ("campaign@example.org", "5.1.10", "failed"),
                    ("help@example.org", "5.1.10", "failed"),
                    ("news@example.org", "5.1.10", "failed"),
                    ("person@example.net", "5.1.10", "failed"),
                )
            )
        )

        self.assertEqual(result.classification, "hard")
        self.assertEqual(result.recipient, "person@example.net")

    def test_remote_diagnostic_cannot_substitute_an_unrelated_recipient(self):
        message = delivery_report_message(
            body={
                "content": "Remote server said 550 5.1.1 victim@example.com not found"
            },
            _raw_mime=delivery_report_mime(
                ("attacker!@evil.example", "5.1.1", "failed")
            ),
        )

        result = process_graph_bounce(message, apply=True)

        self.assertEqual(result.recipient, "attacker!@evil.example")
        self.assertFalse(
            EmailSuppression.objects.filter(email="victim@example.com").exists()
        )
        self.assertTrue(
            EmailSuppression.objects.filter(email="attacker!@evil.example").exists()
        )

    def test_policy_rejection_is_not_a_hard_bounce(self):
        message = delivery_report_message(
            body={"content": "Recipient address rejected; 5.7.1"},
            _raw_mime=delivery_report_mime(("person@example.net", "5.7.1", "failed")),
        )

        result = process_graph_bounce(message, apply=True)

        self.assertEqual(result.classification, "unknown")
        self.assertFalse(EmailSuppression.objects.exists())

    def test_existing_hard_event_repairs_its_missing_suppression(self):
        message = delivery_report_message()
        EmailBounceEvent.objects.create(
            source_message_id=message["internetMessageId"],
            recipient="person@example.net",
            classification="hard",
        )

        process_graph_bounce(message, apply=True)

        self.assertTrue(
            EmailSuppression.objects.filter(email="person@example.net").exists()
        )

    @patch(
        "crush_lu.services.email_bounces.EmailSuppression.objects.update_or_create",
        side_effect=RuntimeError("suppression write failed"),
    )
    def test_event_rolls_back_when_suppression_write_fails(self, update_suppression):
        with self.assertRaisesMessage(RuntimeError, "suppression write failed"):
            process_graph_bounce(delivery_report_message(), apply=True)

        self.assertEqual(EmailBounceEvent.objects.count(), 0)

    def test_persisted_diagnostic_does_not_retain_original_message_content(self):
        secret = "private reset link https://crush.lu/reset/secret-token"
        message = delivery_report_message(
            subject="Undeliverable: Private event invitation",
            body={
                "content": ("person@example.net wasn't found. Status 5.1.10. " + secret)
            },
        )

        process_graph_bounce(message, apply=True)

        event = EmailBounceEvent.objects.get()
        self.assertNotIn(secret, event.diagnostic)
        self.assertIn("5.1.10", event.diagnostic)
        self.assertEqual(event.subject, "Delivery report")

    @patch(
        "crush_lu.management.commands.process_email_bounces._get_message_mime",
        return_value=delivery_report_mime(),
    )
    @patch("crush_lu.management.commands.process_email_bounces.requests.get")
    def test_graph_message_fetch_follows_pagination_with_a_cap(self, get, get_mime):
        first = Mock(status_code=200, text="")
        first.json.return_value = {
            "value": [
                delivery_report_message(id="one", internetMessageId="<one@example.com>")
            ],
            "@odata.nextLink": "https://graph.microsoft.com/next-page",
        }
        second = Mock(status_code=200, text="")
        second.json.return_value = {
            "value": [
                delivery_report_message(
                    id="two", internetMessageId="<two@example.com>"
                ),
                delivery_report_message(
                    id="three", internetMessageId="<three@example.com>"
                ),
            ]
        }
        get.side_effect = [first, second]

        messages, ignored = _get_mailbox_messages(
            mailbox="noreply@crush.lu",
            folder="inbox",
            token="token",
            since="2026-09-01T00:00:00Z",
            limit=2,
            excluded_message_ids={"<one@example.com>"},
        )

        self.assertEqual([message["id"] for message in messages], ["two", "three"])
        self.assertEqual(ignored, 0)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get_mime.call_count, 2)
        self.assertIsNone(get.call_args_list[1].kwargs["params"])

    @override_settings(
        CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED=True,
        CRUSH_EMAIL_BOUNCE_FOLDER="inbox",
        CRUSH_EMAIL_BOUNCE_MAILBOXES=["noreply@crush.lu", "love@crush.lu"],
        CRUSH_EMAIL_BOUNCE_FOLDERS={
            "noreply@crush.lu": "noreply-folder-id",
            "love@crush.lu": "love-folder-id",
        },
    )
    @patch(
        "crush_lu.management.commands.process_email_bounces._get_message_mime",
        return_value=delivery_report_mime(),
    )
    @patch("crush_lu.management.commands.process_email_bounces.requests.get")
    @patch("crush_lu.management.commands.process_email_bounces.get_domain_email_config")
    @patch("crush_lu.management.commands.process_email_bounces.GraphEmailBackend")
    def test_command_scans_all_mailboxes_and_ignores_non_ndrs(
        self, backend, get_config, get, get_mime
    ):
        get_config.return_value = {
            "GRAPH_TENANT_ID": "tenant",
            "GRAPH_CLIENT_ID": "client",
            "GRAPH_CLIENT_SECRET": "secret",
            "DEFAULT_FROM_EMAIL": "noreply@crush.lu",
        }
        backend.return_value.get_access_token.return_value = "token"
        ordinary = delivery_report_message(
            id="ordinary",
            internetMessageId="<ordinary@example.com>",
            receivedDateTime="2026-09-13T13:00:00Z",
            **{
                "from": {"emailAddress": {"address": "person@example.org"}},
                "internetMessageHeaders": [],
            },
        )

        def response_for(url, **kwargs):
            response = Mock(status_code=200, text="")
            response.json.return_value = {
                "value": [
                    ordinary if "love%40crush.lu" in url else delivery_report_message()
                ]
            }
            return response

        get.side_effect = response_for
        output = StringIO()

        call_command("process_email_bounces", apply=True, limit=100, stdout=output)

        requested_urls = [call.args[0] for call in get.call_args_list]
        self.assertTrue(any("noreply%40crush.lu" in url for url in requested_urls))
        self.assertTrue(any("love%40crush.lu" in url for url in requested_urls))
        self.assertTrue(
            any("/mailFolders/noreply-folder-id/" in url for url in requested_urls)
        )
        self.assertTrue(
            any("/mailFolders/love-folder-id/" in url for url in requested_urls)
        )
        self.assertIn("hard=1", output.getvalue())
        self.assertIn("ignored=1", output.getvalue())
        self.assertEqual(EmailBounceEvent.objects.count(), 1)
        self.assertTrue(
            EmailSuppression.objects.filter(email="person@example.net").exists()
        )
        get_mime.assert_called_once()

    @override_settings(
        CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED=True,
        CRUSH_EMAIL_BOUNCE_FOLDER="shared-custom-folder-id",
        CRUSH_EMAIL_BOUNCE_MAILBOXES=["noreply@crush.lu", "love@crush.lu"],
        CRUSH_EMAIL_BOUNCE_FOLDERS={},
    )
    @patch("crush_lu.management.commands.process_email_bounces.get_domain_email_config")
    def test_shared_custom_folder_id_is_rejected_for_multiple_mailboxes(
        self, get_config
    ):
        get_config.return_value = {
            "GRAPH_TENANT_ID": "tenant",
            "GRAPH_CLIENT_ID": "client",
            "GRAPH_CLIENT_SECRET": "secret",
            "DEFAULT_FROM_EMAIL": "noreply@crush.lu",
        }

        with self.assertRaisesMessage(CommandError, "mailbox-specific"):
            call_command("process_email_bounces", apply=True, stdout=StringIO())

    @override_settings(CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED=False)
    def test_apply_command_is_feature_gated_before_graph_access(self):
        with self.assertRaisesMessage(
            CommandError, "CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED"
        ):
            call_command("process_email_bounces", apply=True, stdout=StringIO())

    @override_settings(
        CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED=True,
        CRUSH_EMAIL_BOUNCE_TRUSTED_DOMAINS=[],
    )
    def test_command_requires_an_exact_trusted_tenant_domain(self):
        with self.assertRaisesMessage(
            CommandError, "CRUSH_EMAIL_BOUNCE_TRUSTED_DOMAINS"
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

    def test_event_without_address_retains_canton_location(self):
        event = SimpleNamespace(
            title="Mixer",
            date_time=timezone.now(),
            location="Venue",
            full_address="",
            canton="Capellen",
            description="Details",
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

        self.assertIn("Venue", html)
        self.assertIn("Capellen", html)


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
