"""Mobile coach warnings, translations and no-conflict states in a real browser."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from crush_lu.models import EventRegistration, UserBlock
from crush_lu.tests.test_coach_event_conflicts import pool  # noqa: F401
from crush_lu.tests.test_connect_chat_block_confirm_playwright import _log_in

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 390, "height": 844},
        "is_mobile": True,
        "has_touch": True,
    }


def test_mobile_coach_conflicts(page, live_server, pool, settings):  # noqa: F811
    event, coach, (alice, benoit, _) = pool
    event.registration_mode = "curated"
    event.event_type = "speed_dating"
    event.save(update_fields=["registration_mode", "event_type"])
    origin = f"http://crush.localhost:{urlsplit(live_server.url).port}"
    settings.CSRF_TRUSTED_ORIGINS = [*settings.CSRF_TRUSTED_ORIGINS, origin]
    _log_in(page, origin, coach.user)
    page.set_viewport_size({"width": 390, "height": 844})
    screenshots = Path("screenshots/event-conflicts")
    screenshots.mkdir(parents=True, exist_ok=True)
    before = list(EventRegistration.objects.filter(event=event).values())

    def visit(path):
        response = page.goto(f"{origin}{path}")
        assert response.status == 200
        assert page.url == f"{origin}{path}"
        expect(page.locator("body")).to_contain_text(event.title)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )

    for language, badge, heading in (
        ("en", "1 potential conflict", "Potential participant conflicts"),
        ("de", "1 möglicher Konflikt", "Mögliche Konflikte zwischen Teilnehmenden"),
        ("fr", "1 conflit potentiel", "Conflits potentiels entre participants"),
    ):
        listing = f"/{language}/coach/events/"
        detail = f"{listing}{event.pk}/"
        visit(listing)
        expect(page.locator("[data-event-conflict-warning]")).to_have_count(0)
        page.screenshot(
            path=str(screenshots / f"{language}-list-none.png"), full_page=True
        )
        visit(detail)
        expect(page.locator("[data-event-conflicts]")).to_have_count(0)
        page.screenshot(
            path=str(screenshots / f"{language}-detail-none.png"), full_page=True
        )

        block = UserBlock.objects.create(
            blocker=benoit, blocked=alice, reason="harassment"
        )
        for dark in (False, True):
            page.evaluate(
                "dark => localStorage.setItem('theme', dark ? 'dark' : 'light')",
                dark,
            )
            visit(listing)
            expect(page.locator("html")).to_have_class("dark" if dark else "")
            expect(page.locator("[data-event-conflict-warning]")).to_contain_text(badge)
            page.screenshot(
                path=str(
                    screenshots / f"{language}-list-{'dark' if dark else 'light'}.png"
                ),
                full_page=True,
            )
            page.locator("a").filter(
                has=page.locator("[data-event-conflict-warning]")
            ).click()
            expect(page).to_have_url(f"{origin}{detail}")
            warning = page.locator("[data-event-conflicts]")
            expect(warning).to_be_visible()
            expect(warning.get_by_role("heading")).to_have_text(f"⚠ {heading}")
            expect(warning).to_contain_text("Alice")
            expect(warning).to_contain_text("Benoit")
            expect(warning).not_to_contain_text("harassment")
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            expect(page.locator("html")).to_have_class("dark" if dark else "")
            page.screenshot(
                path=str(
                    screenshots / f"{language}-detail-{'dark' if dark else 'light'}.png"
                ),
                full_page=True,
            )
        block.delete()
        visit(detail)
        expect(page.locator("[data-event-conflicts]")).to_have_count(0)
    assert list(EventRegistration.objects.filter(event=event).values()) == before
