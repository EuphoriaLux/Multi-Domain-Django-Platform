"""UX Wave 4 · WP2b "invitation-pages" (finding 7-14).

The four invitation pages (landing, accept form, pending approval, expired)
drop their inline white-card <style> and Bootstrap alerts for STYLE.md tokens
with dark variants, and the event details use a valid <dl> structure.

Paths are literal: ``reverse("crush_lu:...")`` builds ``/crush/...`` paths
that 404 under ``HTTP_HOST=crush.lu``.
"""

from datetime import timedelta
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import EventInvitation, MeetupEvent, UserDataConsent

User = get_user_model()
HOST = "crush.lu"


def _user(email, first="Lena", last="Schmit", **extra):
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        first_name=first,
        last_name=last,
        **extra,
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


class _DlChecker(HTMLParser):
    """Collects content-model violations inside <dl>: a <dl> may only hold
    <dt>/<dd> (optionally grouped in <div>s that themselves hold only
    <dt>/<dd>), which is what axe-core's definition-list/dlitem rules check."""

    VOID = {"br", "img", "input", "hr", "meta", "link", "source", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.dl_count = 0
        self.errors = []

    def handle_starttag(self, tag, attrs):
        parent = self.stack[-1] if self.stack else None
        grandparent = self.stack[-2] if len(self.stack) > 1 else None
        if tag == "dl":
            self.dl_count += 1
        if parent == "dl" and tag not in ("dt", "dd", "div"):
            self.errors.append(f"<{tag}> directly in <dl>")
        if parent == "div" and grandparent == "dl" and tag not in ("dt", "dd"):
            self.errors.append(f"<{tag}> in a <dl> group <div>")
        if tag in ("dt", "dd") and not (
            parent == "dl" or (parent == "div" and grandparent == "dl")
        ):
            self.errors.append(f"<{tag}> outside a <dl> group (in <{parent}>)")
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self.stack:
            if self.stack.pop() == tag:
                break


def _dl_errors(html):
    checker = _DlChecker()
    checker.feed(html)
    return checker.dl_count, checker.errors


class InvitationPagesTokenTests(TestCase):
    """7-14: no inline white cards or Bootstrap alerts; dark variants present."""

    LEGACY = (
        "<style>",
        "background: white",
        "alert alert-",
        "accept-btn",
        "text-muted ",
        'class="lead',
        "#6c757d",
    )

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        self.event = MeetupEvent.objects.create(
            title="Private VIP Event",
            description="Exclusive private event",
            event_type="mixer",
            location="Luxembourg City",
            address="123 Test Street",
            date_time=timezone.now() + timedelta(days=7),
            duration_minutes=120,
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            is_private_invitation=True,
            invitation_code="vip2024",
            invitation_expires_at=timezone.now() + timedelta(days=30),
        )
        self.invitation = EventInvitation.objects.create(
            event=self.event,
            guest_email="guest@example.com",
            guest_first_name="John",
            guest_last_name="Doe",
            invited_by=_user("coach@example.com", first="Cora", last="Coach"),
            status="pending",
            approval_status="pending_approval",
        )
        self.base = f"/en/invite/{self.invitation.invitation_code}/"

    def _assert_tokens(self, response, template):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, template)
        html = response.content.decode()
        main = html[html.index("<main") : html.index("</main>")]
        for legacy in self.LEGACY:
            self.assertNotIn(legacy, main, f"{template}: {legacy}")
        self.assertIn("bg-[var(--color-surface-card)] dark:bg-gray-800", main)

    def test_landing(self):
        response = self.client.get(self.base)
        self._assert_tokens(response, "crush_lu/invitation_landing.html")
        self.assertContains(response, "btn-crush-primary btn-lg")

    def test_definition_lists_are_valid(self):
        for path in (self.base, f"{self.base}accept/"):
            html = self.client.get(path).content.decode()
            count, errors = _dl_errors(html[html.index("<main") :])
            self.assertGreater(count, 0, path)
            self.assertEqual(errors, [], path)

    def test_accept_form(self):
        response = self.client.get(f"{self.base}accept/")
        self._assert_tokens(response, "crush_lu/invitation_accept_form.html")
        self.assertContains(response, 'name="date_of_birth"')

    def test_pending_approval(self):
        response = self.client.post(
            f"{self.base}accept/",
            {"date_of_birth": "1995-05-15", "agree_to_terms": "on"},
        )
        self._assert_tokens(response, "crush_lu/invitation_pending_approval.html")

    def test_expired(self):
        self.event.invitation_expires_at = timezone.now() - timedelta(days=1)
        self.event.save()
        response = self.client.get(self.base)
        self._assert_tokens(response, "crush_lu/invitation_expired.html")
