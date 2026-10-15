"""
The profile wizard must not submit a photo whose auto-save upload failed.

Production, 2026-09-28 to 2026-10-08: on iPhones the final profile submit
reached the server as multipart/form-data with content_length=0 (CSRF 403).
A photo the auto-save upload could not store stays in its file input, and the
native multipart submit then carried it. The service worker no longer claims
file uploads (test_sw_multipart_uploads.py); this is the second layer: the
wizard sends the member back to Photos instead of submitting the leftover file.

Run with: pytest crush_lu/tests/test_profile_unsaved_photo_playwright.py -m playwright
"""

import io

import pytest
from django.contrib.auth import get_user_model
from playwright.sync_api import Page

User = get_user_model()

WIZARD = '[x-data="profileWizard"]'


def _jpeg_bytes():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (400, 400), "purple").save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _sites(transactional_db):
    from django.contrib.sites.models import SITE_CACHE, Site

    SITE_CACHE.clear()
    Site.objects.update_or_create(
        id=1, defaults={"domain": "localhost", "name": "localhost"}
    )
    Site.objects.get_or_create(domain="127.0.0.1", defaults={"name": "Live Server"})
    yield
    SITE_CACHE.clear()


@pytest.fixture
def wizard_page(page: Page, live_server_url, transactional_db):
    from allauth.account.models import EmailAddress

    user = User.objects.create_user(
        username="photo-guard@example.com",
        email="photo-guard@example.com",
        password="testpass123",
        first_name="Photo",
    )
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    # ConsentMiddleware sends members without Crush.lu consent to /consent/confirm/.
    from crush_lu.models.profiles import UserDataConsent

    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    # The wizard opens only at journey step 4 (crush_lu/onboarding.py): past
    # welcome, phone verification and the coach intro.
    from django.utils import timezone

    from crush_lu.models import CrushProfile

    CrushProfile.objects.create(
        user=user,
        welcome_seen_at=timezone.now(),
        phone_number="+352621123456",
        phone_verified=True,
        coach_intro_seen_at=timezone.now(),
    )

    page.goto(f"{live_server_url}/accounts/login/")
    page.wait_for_selector('input[name="login"]', timeout=10000)
    decline = page.locator('button:has-text("Decline All")')
    if decline.count() > 0:
        decline.click()
    page.fill('input[name="login"]', user.email)
    page.fill('input[name="password"]', "testpass123")
    page.click('button:has-text("Login")')
    page.wait_for_load_state("networkidle")

    page.goto(f"{live_server_url}/en/create-profile/")
    page.wait_for_function(
        "(sel) => { const el = document.querySelector(sel);"
        " return !!(el && el._x_dataStack && el._x_dataStack.length); }",
        arg=WIZARD,
        timeout=10000,
    )
    return page


def _wizard(page, expression):
    """Evaluate ``expression`` with ``w`` bound to the wizard's Alpine data."""
    return page.evaluate(
        "([sel, expr]) => { const w = document.querySelector(sel)._x_dataStack[0];"
        " return (new Function('w', 'return ' + expr))(w); }",
        [WIZARD, expression],
    )


def _submit_from_review(page):
    """Jump to Review (the last step) and submit the form as its button does."""
    _wizard(page, "(w.currentStep = w.totalSteps, w.totalSteps)")
    page.evaluate("() => document.getElementById('profileForm').requestSubmit()")


@pytest.mark.playwright
def test_failed_photo_upload_is_not_sent_with_the_final_submit(wizard_page: Page):
    page = wizard_page
    profile_posts = []
    page.on(
        "request",
        lambda r: (
            profile_posts.append(r.url)
            if r.method == "POST" and "/create-profile/" in r.url
            else None
        ),
    )
    # The auto-save upload fails, so the file stays in the input.
    page.route(
        "**/api/profile/draft/upload-photo/",
        lambda route: route.fulfill(
            status=429,
            content_type="application/json",
            body='{"success": false, "error": "Daily upload limit reached"}',
        ),
    )
    with page.expect_response("**/api/profile/draft/upload-photo/"):
        page.set_input_files(
            "#photo1",
            files=[
                {"name": "me.jpg", "mimeType": "image/jpeg", "buffer": _jpeg_bytes()}
            ],
        )
    assert page.evaluate("() => document.getElementById('photo1').files.length") == 1

    _submit_from_review(page)
    page.wait_for_function(
        "(sel) => document.querySelector(sel)._x_dataStack[0].currentStep === 3",
        arg=WIZARD,
        timeout=5000,
    )
    page.wait_for_timeout(500)

    assert profile_posts == [], f"the profile form was submitted: {profile_posts}"
    assert _wizard(page, "w.saveError").startswith("A photo was not saved")


@pytest.mark.playwright
def test_review_submits_normally_without_a_leftover_photo(wizard_page: Page):
    page = wizard_page
    with page.expect_request(
        lambda r: r.method == "POST" and "/create-profile/" in r.url, timeout=10000
    ):
        _submit_from_review(page)
