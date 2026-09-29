"""UX Wave 4 · WP14 design sweep: event-flow buttons follow STYLE.md (4-11).

One ``btn-crush-primary`` per rendered state, canonical variants on the key
buttons. Literal /en/ paths, not reverse(): reverse() resolves against the
default urlconf, not the crush.lu one the HTTP_HOST override selects.
"""

from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.profiles import CrushProfile, UserDataConsent

User = get_user_model()

GRADIENT_MARKERS = ("bg-gradient-to-r", "from-emerald", "hover:bg-crush-pink")


class _Buttons(HTMLParser):
    """Collect the attrs of every <a>/<button>, optionally inside one id."""

    def __init__(self, container_id=None):
        super().__init__()
        self.container_id = container_id
        self._depth = 0 if container_id else 1
        self._container_tag = None
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.container_id:
            if not self._depth:
                if a.get("id") == self.container_id:
                    self._depth = 1
                    self._container_tag = tag
                return
            if tag == self._container_tag:
                self._depth += 1
        if tag in ("a", "button"):
            self.buttons.append(a)

    def handle_endtag(self, tag):
        if self.container_id and self._depth and tag == self._container_tag:
            self._depth -= 1


def buttons(html, container_id=None):
    parser = _Buttons(container_id)
    parser.feed(html)
    return parser.buttons


def with_class(items, cls):
    return [b for b in items if cls in (b.get("class") or "").split()]


def href_ends(items, suffix):
    return [b for b in items if (b.get("href") or "").rstrip("/").endswith(suffix)]


class EventFlowButtonBase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "crush.lu", "name": "Crush.lu"}
        )

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="wp14@crush.lu", email="wp14@crush.lu", password="password123"
        )
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            verification_status="verified",
            completion_status="step4",
            is_approved=True,
        )
        self.event = MeetupEvent.objects.create(
            title="WP14 Design Sweep Event",
            description="WP14",
            event_type="speed_dating",
            location="Luxembourg City",
            address="10 Grand Rue",
            date_time=timezone.now() + timezone.timedelta(days=5),
            registration_deadline=timezone.now() + timezone.timedelta(days=4),
            registration_fee=Decimal("15.00"),
            is_published=True,
        )
        self.client.force_login(self.user)

    def register(self, status="confirmed", paid=False):
        return EventRegistration.objects.create(
            user=self.user,
            event=self.event,
            status=status,
            payment_confirmed=paid,
        )

    def get(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def detail(self):
        return self.get(f"/en/events/{self.event.id}/")

    def assertNoGradientButtons(self, items):
        for b in items:
            if b.get("id") == "pwa-install-button":
                continue  # base-template chrome, not part of the event flow
            classes = b.get("class") or ""
            for marker in GRADIENT_MARKERS:
                self.assertNotIn(marker, classes, f"hand-rolled gradient: {b}")


class EventDetailButtonTests(EventFlowButtonBase):
    def test_not_registered_has_single_register_primary(self):
        html = self.detail()
        panel = buttons(html, "event-cta-panel")
        primaries = with_class(panel, "btn-crush-primary")
        self.assertEqual(len(primaries), 1, primaries)
        self.assertTrue(primaries[0]["href"].rstrip("/").endswith("/register"))
        self.assertNoGradientButtons(buttons(html, "event-cta-panel"))

    def test_share_button_is_outline(self):
        share = [b for b in buttons(self.detail()) if b.get("id") == "shareEventBtn"]
        self.assertEqual(len(share), 1)
        self.assertIn("btn-crush-outline", share[0]["class"].split())
        self.assertNotIn("btn-crush-primary", share[0]["class"].split())

    def test_payment_due_has_single_pay_primary(self):
        self.register(status="confirmed", paid=False)
        html = self.detail()
        primaries = with_class(buttons(html, "event-cta-panel"), "btn-crush-primary")
        self.assertEqual(len(primaries), 1, primaries)
        self.assertEqual(primaries[0].get("data-payment-method"), "card")
        self.assertNoGradientButtons(buttons(html, "event-cta-panel"))

    def test_registered_ticket_solid_and_cancel_danger_without_primary(self):
        self.register(status="confirmed", paid=True)
        html = self.detail()
        panel = buttons(html, "event-cta-panel")
        self.assertEqual(with_class(panel, "btn-crush-primary"), [])
        ticket = href_ends(panel, "/ticket")
        self.assertEqual(len(ticket), 1, ticket)
        self.assertIn("btn-crush-solid", ticket[0]["class"].split())
        cancel = href_ends(panel, "/cancel")
        self.assertEqual(len(cancel), 1, cancel)
        self.assertIn("btn-danger", cancel[0]["class"].split())
        toggles = [
            b
            for b in buttons(html)
            if b.get("@click") == "toggle" and "nav-link" not in (b.get("class") or "")
        ]
        self.assertTrue(with_class(toggles, "btn-crush-outline"), toggles)
        self.assertNoGradientButtons(toggles)
        self.assertNoGradientButtons(buttons(html, "event-cta-panel"))

    def test_attended_links_are_solid_not_gradient(self):
        self.event.date_time = timezone.now() - timezone.timedelta(
            minutes=self.event.duration_minutes + 60
        )
        self.event.save()
        self.register(status="attended", paid=True)
        panel = buttons(self.detail(), "event-cta-panel")
        self.assertEqual(with_class(panel, "btn-crush-primary"), [])
        self.assertNoGradientButtons(panel)
        attendees = href_ends(panel, "/attendees")
        self.assertEqual(len(attendees), 1, attendees)
        self.assertIn("btn-crush-solid", attendees[0]["class"].split())


class EventRegisterFormButtonTests(EventFlowButtonBase):
    def test_form_has_one_primary_and_outline_cancel(self):
        items = buttons(self.get(f"/en/events/{self.event.id}/register/"))
        self.assertEqual(len(with_class(items, "btn-crush-primary")), 1)
        cancel = href_ends(items, f"/events/{self.event.id}")
        self.assertTrue(with_class(cancel, "btn-crush-outline"), cancel)
        for b in items:
            self.assertNotIn("bg-gray-200", (b.get("class") or "").split())


class EventTicketButtonTests(EventFlowButtonBase):
    def test_ticket_page_has_no_primary_and_outline_back_link(self):
        self.register(status="confirmed", paid=True)
        items = buttons(self.get(f"/en/events/{self.event.id}/ticket/"))
        self.assertEqual(with_class(items, "btn-crush-primary"), [])
        back = href_ends(items, f"/events/{self.event.id}")
        self.assertTrue(with_class(back, "btn-crush-outline"), back)


class RegistrationSuccessButtonTests(EventFlowButtonBase):
    def render(self, registration, credit):
        return render_to_string(
            "crush_lu/_event_registration_success.html",
            {
                "event": self.event,
                "registration": registration,
                "waitlist_position": None,
                "has_sufficient_crush_credit": credit,
            },
        )

    def test_payment_due_state_single_card_primary_and_solid_credit(self):
        html = self.render(self.register(status="pending"), credit=True)
        items = buttons(html)
        primaries = with_class(items, "btn-crush-primary")
        self.assertEqual(len(primaries), 1, primaries)
        self.assertEqual(primaries[0].get("data-payment-method"), "card")
        credit = [b for b in items if b.get("data-payment-method") == "credit"]
        self.assertEqual(len(credit), 1)
        self.assertIn("btn-crush-solid", credit[0]["class"].split())
        self.assertNoGradientButtons(items)
        self.assertNotIn("btn-crush-secondary", html)

    def test_confirmed_state_has_no_primary_and_outline_calendar(self):
        self.event.registration_fee = Decimal("0.00")
        self.event.save()
        html = self.render(self.register(status="confirmed", paid=True), credit=False)
        items = buttons(html)
        self.assertEqual(with_class(items, "btn-crush-primary"), [])
        self.assertNoGradientButtons(items)
        toggles = [b for b in items if b.get("@click") == "toggle"]
        self.assertTrue(toggles)
        for b in toggles:
            self.assertIn("btn-crush-outline", b["class"].split())
