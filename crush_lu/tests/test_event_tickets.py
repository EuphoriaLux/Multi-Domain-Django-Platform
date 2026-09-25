"""
Tests for Event QR Check-In and Ticket system.

Tests cover:
- Token signing/verification (Signer round-trip)
- Check-in API: success, invalid token, already attended, wrong day
- Web ticket page: requires login, only owner can view, 404 for unregistered
- Event ticket JWT generation (mocked wallet)
"""

import re
from datetime import date, timedelta
from unittest import mock

import pytest
import qrcode
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.signing import Signer
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext

from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent
from crush_lu.models.profiles import UserDataConsent
from crush_lu.qr_utils import generate_qr_code_svg
from crush_lu.views_ticket import _generate_checkin_token

# All crush_lu HTTP tests must use the crush-specific URL config and have db access
pytestmark = [pytest.mark.urls("azureproject.urls_crush"), pytest.mark.django_db]


@pytest.fixture
def event_user(db):
    """Create a user with an approved profile and consent."""
    user = User.objects.create_user(
        username="ticketuser",
        email="ticket@example.com",
        password="testpass123",
        first_name="Alice",
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="Luxembourg City",
        is_approved=True,
        is_active=True,
    )
    UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
    return user


@pytest.fixture
def other_user(db):
    """Create another user with consent."""
    user = User.objects.create_user(
        username="otheruser",
        email="other@example.com",
        password="testpass123",
    )
    UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
    return user


@pytest.fixture
def upcoming_event(db):
    """Create an upcoming published event."""
    return MeetupEvent.objects.create(
        title="Test Speed Dating",
        description="A fun event",
        event_type="speed_dating",
        location="Test Venue",
        address="123 Test St",
        canton="Luxembourg",
        date_time=timezone.now() + timedelta(hours=2),
        duration_minutes=120,
        max_participants=20,
        registration_deadline=timezone.now() + timedelta(hours=1),
        is_published=True,
    )


@pytest.fixture
def past_event(db):
    """Create a past event (outside check-in window)."""
    return MeetupEvent.objects.create(
        title="Past Event",
        description="Already happened",
        event_type="mixer",
        location="Past Venue",
        address="456 Past St",
        canton="Luxembourg",
        date_time=timezone.now() - timedelta(days=3),
        duration_minutes=120,
        max_participants=20,
        registration_deadline=timezone.now() - timedelta(days=4),
        is_published=True,
    )


@pytest.fixture
def confirmed_registration(event_user, upcoming_event):
    """Create a confirmed registration."""
    return EventRegistration.objects.create(
        event=upcoming_event,
        user=event_user,
        status="confirmed",
    )


class TestCheckinTokenGeneration:
    """Test the token signing/verification round-trip."""

    def test_generate_token_creates_valid_signed_value(self, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        assert token
        assert ":" in Signer().unsign(token)

    def test_generate_token_is_idempotent(self, confirmed_registration):
        token1 = _generate_checkin_token(confirmed_registration)
        token2 = _generate_checkin_token(confirmed_registration)
        assert token1 == token2

    def test_token_saved_to_registration(self, confirmed_registration):
        assert confirmed_registration.checkin_token == ""
        _generate_checkin_token(confirmed_registration)
        confirmed_registration.refresh_from_db()
        assert confirmed_registration.checkin_token != ""

    def test_token_contains_registration_and_event_ids(self, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        unsigned = Signer().unsign(token)
        reg_id, event_id = unsigned.split(":")
        assert int(reg_id) == confirmed_registration.id
        assert int(event_id) == confirmed_registration.event_id


class TestCheckinAPI:
    """Test the check-in API endpoint."""

    def test_successful_checkin(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        url = f"/api/events/checkin/{confirmed_registration.id}/{token}/"
        response = client.post(url)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["already_checked_in"] is False
        assert "attendee_name" in data

        confirmed_registration.refresh_from_db()
        assert confirmed_registration.status == "attended"
        assert confirmed_registration.checked_in_at is not None

    def test_already_attended(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        confirmed_registration.status = "attended"
        confirmed_registration.checked_in_at = timezone.now()
        confirmed_registration.save()

        url = f"/api/events/checkin/{confirmed_registration.id}/{token}/"
        response = client.post(url)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["already_checked_in"] is True

    def test_invalid_token(self, client, confirmed_registration):
        url = f"/api/events/checkin/{confirmed_registration.id}/invalid-token/"
        response = client.post(url)
        assert response.status_code == 400
        assert response.json()["success"] is False

    def test_wrong_registration_id(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        url = f"/api/events/checkin/99999/{token}/"
        response = client.post(url)
        assert response.status_code == 400

    def test_cancelled_registration(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        confirmed_registration.status = "cancelled"
        confirmed_registration.save()

        url = f"/api/events/checkin/{confirmed_registration.id}/{token}/"
        response = client.post(url)
        assert response.status_code == 400
        assert "Cancelled" in response.json()["error"]

    def test_outside_checkin_window(self, client, event_user, past_event):
        reg = EventRegistration.objects.create(
            event=past_event,
            user=event_user,
            status="confirmed",
        )
        token = _generate_checkin_token(reg)
        url = f"/api/events/checkin/{reg.id}/{token}/"
        response = client.post(url)
        assert response.status_code == 400
        error_msg = response.json()["error"].lower()
        assert "window" in error_msg or "hours" in error_msg

    def test_get_method_not_allowed(self, client, confirmed_registration):
        token = _generate_checkin_token(confirmed_registration)
        url = f"/api/events/checkin/{confirmed_registration.id}/{token}/"
        response = client.get(url)
        assert response.status_code == 405


class TestWebTicketPage:
    """Test the web ticket page view."""

    def test_ticket_page_requires_login(self, client, upcoming_event):
        url = reverse("crush_lu:event_ticket", args=[upcoming_event.id])
        response = client.get(url)
        assert response.status_code == 302
        assert "login" in response.url.lower()

    def test_ticket_page_shows_for_confirmed_user(
        self, client, event_user, confirmed_registration
    ):
        client.login(username="ticketuser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[confirmed_registration.event_id])
        response = client.get(url)
        assert response.status_code == 200

    def test_ticket_page_404_for_unregistered_user(
        self, client, other_user, upcoming_event
    ):
        client.login(username="otheruser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[upcoming_event.id])
        response = client.get(url)
        assert response.status_code == 404

    def test_ticket_page_404_for_cancelled_registration(
        self, client, event_user, upcoming_event
    ):
        EventRegistration.objects.create(
            event=upcoming_event,
            user=event_user,
            status="cancelled",
        )
        client.login(username="ticketuser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[upcoming_event.id])
        response = client.get(url)
        assert response.status_code == 404

    def test_ticket_page_other_user_cannot_view(
        self, client, other_user, confirmed_registration
    ):
        client.login(username="otheruser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[confirmed_registration.event_id])
        response = client.get(url)
        assert response.status_code == 404

    def test_ticket_page_shows_checked_in_status(
        self, client, event_user, confirmed_registration
    ):
        confirmed_registration.status = "attended"
        confirmed_registration.checked_in_at = timezone.now()
        confirmed_registration.save()

        client.login(username="ticketuser", password="testpass123")
        url = reverse("crush_lu:event_ticket", args=[confirmed_registration.event_id])
        response = client.get(url)
        assert response.status_code == 200
        assert response.context["already_checked_in"] is True
        assert "checked in!" in response.content.decode()


class TestTicketHonesty:
    """UX review 4-01 / 4-02: no invented score, a QR that always renders.

    The ticket showed "Room Chemistry ✨ 88-98%" computed as
    ``88 + registration.id % 11``, linked to a page describing a weighted
    algorithm that never existed. Its QR was drawn in the browser by a
    jsDelivr script, so a CDN failure -- or the offline copy the service
    worker serves at the venue door -- left a blank white box.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        # Every test viewer ends up as the same user id and shares the
        # @ratelimit counters (AGENTS.md).
        cache.clear()

    def _get_ticket(self, client, registration, lang="en"):
        client.login(username="ticketuser", password="testpass123")
        return client.get(
            f"/{lang}/events/{registration.event_id}/ticket/", HTTP_HOST="crush.lu"
        )

    def test_ticket_has_no_fabricated_chemistry_score(
        self, client, event_user, confirmed_registration
    ):
        response = self._get_ticket(client, confirmed_registration)
        assert response.status_code == 200
        assert "compatibility_score" not in response.context
        html = response.content.decode()
        for fabricated in (
            "Room Chemistry",
            "Chemistry Potential",
            "How is it calculated?",
            "Read Full Algorithm Details",
            "showScoreModal",
            "/compatibility/",
        ):
            assert fabricated not in html, fabricated

    def test_ticket_qr_is_inline_svg_without_cdn(
        self, client, event_user, confirmed_registration
    ):
        response = self._get_ticket(client, confirmed_registration)
        html = response.content.decode()
        container = html.split('id="qr-code-container"', 1)[1].split("</div>", 1)[0]
        assert 'role="img"' in container
        assert 'aria-label="QR code for event check-in"' in container
        assert "<svg" in container and 'id="qr-path"' in container
        assert "<?xml" not in html
        assert "cdn.jsdelivr.net" not in html
        assert "qrcode-generator" not in html
        assert "qr-canvas" not in html

    def test_ticket_qr_encodes_the_signed_checkin_url(
        self, client, event_user, confirmed_registration
    ):
        from crush_lu import views_ticket

        with mock.patch.object(
            views_ticket,
            "generate_qr_code_svg",
            wraps=views_ticket.generate_qr_code_svg,
        ) as spy:
            response = self._get_ticket(client, confirmed_registration)

        confirmed_registration.refresh_from_db()
        expected = (
            f"http://crush.lu/api/events/checkin/{confirmed_registration.id}/"
            f"{confirmed_registration.checkin_token}/"
        )
        spy.assert_called_once_with(expected)
        # The token is the one the coach check-in API accepts.
        assert (
            client.post(
                f"/api/events/checkin/{confirmed_registration.id}/"
                f"{confirmed_registration.checkin_token}/",
                HTTP_HOST="crush.lu",
            ).status_code
            == 200
        )
        # And the markup on the page is exactly that URL's QR, not a stale or
        # placeholder one.
        assert generate_qr_code_svg(expected) in response.content.decode()

    def test_ticket_shows_human_readable_ticket_number(
        self, client, event_user, confirmed_registration
    ):
        response = self._get_ticket(client, confirmed_registration)
        assert response.context["ticket_number"] == f"#{confirmed_registration.id}"
        html = response.content.decode()
        assert "Ticket number" in html
        assert f"#{confirmed_registration.id}</span>" in html

    @pytest.mark.parametrize(
        "lang, label, aria",
        [
            ("de", "Ticketnummer", "QR-Code für den Event-Check-in"),
            ("fr", "Numéro de billet", "Code QR pour l'enregistrement"),
        ],
    )
    def test_ticket_labels_are_translated(
        self, client, event_user, confirmed_registration, lang, label, aria
    ):
        html = self._get_ticket(client, confirmed_registration, lang).content.decode()
        assert label in html
        assert aria in html
        with translation.override(lang):
            assert gettext("Ticket number") == label

    @pytest.mark.parametrize("lang", ["en", "de", "fr"])
    def test_compatibility_explainer_permanently_redirects(self, client, db, lang):
        response = client.get(f"/{lang}/compatibility/", HTTP_HOST="crush.lu")
        assert response.status_code == 301
        assert response["Location"] == f"/{lang}/how-it-works/"
        assert client.get(response["Location"], HTTP_HOST="crush.lu").status_code == 200

    def test_compatibility_explainer_template_is_gone(self):
        with pytest.raises(TemplateDoesNotExist):
            get_template("crush_lu/compatibility_explainer.html")


class TestQrCodeSvgHelper:
    """generate_qr_code_svg: the module grid IS the encoded data."""

    URL = "https://crush.lu/api/events/checkin/42/42:7:abcDEF123/"

    @staticmethod
    def _dark_modules(svg):
        # SvgPathFillImage draws one "M{x},{y}H.." subpath per dark module,
        # in module units with the quiet zone included.
        return set(re.findall(r"M(\d+),(\d+)H", svg))

    @staticmethod
    def _expected_modules(data):
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(data)
        qr.make(fit=True)
        return {
            (str(x), str(y))
            for y, row in enumerate(qr.get_matrix())
            for x, dark in enumerate(row)
            if dark
        }

    def test_svg_modules_match_the_encoded_url(self):
        svg = generate_qr_code_svg(self.URL)
        assert self._dark_modules(svg) == self._expected_modules(self.URL)
        assert self._dark_modules(svg) != self._expected_modules(self.URL + "x")

    def test_svg_is_inline_fluid_and_on_white(self):
        svg = generate_qr_code_svg(self.URL)
        assert svg.startswith("<svg ")
        assert "<?xml" not in svg
        assert 'width="100%"' in svg and 'height="100%"' in svg
        assert 'viewBox="0 0 ' in svg
        # White quiet zone in the SVG itself, whatever the page theme.
        assert '<rect fill="white"' in svg
        assert 'fill="#000000"' in svg


class TestEventTicketJWTView:
    """Test the Google Wallet event ticket JWT endpoint."""

    def test_jwt_endpoint_requires_login(self, client, confirmed_registration):
        url = f"/wallet/google/event-ticket/{confirmed_registration.id}/jwt/"
        response = client.get(url)
        assert response.status_code == 302

    def test_jwt_endpoint_404_for_wrong_user(
        self, client, other_user, confirmed_registration
    ):
        client.login(username="otheruser", password="testpass123")
        url = f"/wallet/google/event-ticket/{confirmed_registration.id}/jwt/"
        response = client.get(url)
        assert response.status_code == 404

    def test_jwt_endpoint_returns_503_when_not_configured(
        self, client, event_user, confirmed_registration
    ):
        client.login(username="ticketuser", password="testpass123")
        url = f"/wallet/google/event-ticket/{confirmed_registration.id}/jwt/"
        response = client.get(url)
        # Without wallet configured, should return 503
        assert response.status_code == 503
