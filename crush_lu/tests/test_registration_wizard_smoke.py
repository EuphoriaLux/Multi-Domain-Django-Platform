"""
CI smoke tier: critical-path Playwright checks.

This is the fast, PR-gating tier described in
`docs/testing/playwright-coverage-matrix.md` (§5, rows 1 and 3). Run it with:

    pytest -m "playwright and smoke"

Selection criteria for anything added here: deterministic (own data, no
shared/order-dependent state), fast, and tied to a real user-visible outcome
rather than incidental styling. See the coverage matrix for the full
regression backlog — this file intentionally stays small.

Row 3 (member signup / profile-build wizard) previously had coverage in
`test_profile_registration_e2e.py`, but every test there hangs on
`page.wait_for_selector('#phone-verification-container', ...)` — that
selector hasn't existed on `/create-profile/` since phone verification moved
to `/onboarding/phone/` (see `create_profile.html`'s own comment: "Phone
verification now happens at step 2 of the onboarding journey"). That file is
left for the regression-tier task (`t_2eb7f76b`) to repair or retire; this
smoke test re-implements just the "does the wizard load and initialize"
outcome with selectors that match the current template, so signup — the
true top-of-funnel critical path — has *some* CI-fast coverage today.
"""
import pytest
from playwright.sync_api import Page, expect
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


@pytest.mark.playwright
@pytest.mark.smoke
class TestRegistrationWizardSmoke:
    """Smoke coverage for the profile-build step of the onboarding journey."""

    @pytest.fixture
    def wizard_ready_user(self, transactional_db):
        """A user positioned at onboarding step 4 (build profile).

        The 7-step onboarding journey (crush_lu/onboarding.py) gates
        /create-profile/ behind welcome/phone-verify/coach-intro (steps
        1-3): views.create_profile redirects any user whose
        onboarding.get_current_step() < 4 to /onboarding/. Stamping the
        CrushProfile fields get_current_step reads lets this test isolate
        the step-4 outcome without walking steps 1-3 end to end (step 2
        requires Firebase phone verification, unavailable here) — the same
        shortcut the `capture_screenshots` management command's
        `_ensure_onboarding_user` helper already uses for this scenario.
        Also grants Crush.lu consent so `CrushConsentMiddleware` doesn't
        redirect to /consent/confirm/ first.
        """
        from allauth.account.models import EmailAddress
        from crush_lu.models.profiles import CrushProfile, UserDataConsent

        user = User.objects.create_user(
            username="wizardsmoke@example.com",
            email="wizardsmoke@example.com",
            password="testpass123",
            first_name="Wizard",
            last_name="Smoke",
        )
        EmailAddress.objects.create(
            user=user, email=user.email, verified=True, primary=True
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        now = timezone.now()
        CrushProfile.objects.update_or_create(
            user=user,
            defaults={
                "welcome_seen_at": now,
                "phone_verified": True,
                "phone_verified_at": now,
                "coach_intro_seen_at": now,
                "verification_status": "incomplete",
                "is_active": True,
            },
        )
        return user

    @pytest.fixture
    def logged_in_page(self, page: Page, live_server_url, wizard_ready_user):
        """Log in the wizard-ready user via the real login form."""
        page.goto(f"{live_server_url}/accounts/login/")
        page.wait_for_selector('input[name="login"]', timeout=10000)

        cookie_decline = page.locator('button:has-text("Decline All")')
        if cookie_decline.count() > 0:
            cookie_decline.click()
            page.wait_for_timeout(300)

        page.fill('input[name="login"]', wizard_ready_user.email)
        page.fill('input[name="password"]', "testpass123")
        page.click('button:has-text("Login")')
        page.wait_for_load_state("networkidle")
        return page

    def test_create_profile_step1_loads_and_alpine_initializes(
        self, logged_in_page: Page, live_server_url
    ):
        """
        The member signup / profile-build wizard is the top-of-funnel
        critical path: this asserts step 1 (Basic Information) renders for
        a user who has completed the earlier journey steps, the wizard's
        Alpine.js component initializes, and there are no JS console
        errors — the clearest, cheapest failure signal for template/JS
        drift on this page.
        """
        page = logged_in_page
        console_errors = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )

        page.goto(f"{live_server_url}/en/create-profile/")
        page.wait_for_selector("#profileForm", timeout=10000)

        # Step 1 content is visible.
        expect(page.locator('h3:has-text("Basic Information")')).to_be_visible()

        # Alpine's profileWizard component initialized on the root element.
        has_alpine = page.evaluate(
            """() => {
                const el = document.querySelector('[x-data="profileWizard"]');
                return !!(el && el._x_dataStack && el._x_dataStack.length > 0);
            }"""
        )
        assert has_alpine, "Alpine.js profileWizard component did not initialize"

        # The page always loads phone-verification.js (kept for the
        # phone_verified=False fallback branch of create_profile.html), and
        # that module logs a console.error the moment it can't find
        # FIREBASE_API_KEY/FIREBASE_PROJECT_ID. Those aren't set in this test
        # environment (nor in CI — see the coverage matrix's env-var
        # inventory) and firing is by design
        # (crush_lu/static/crush_lu/js/phone-verification.js), so filter it
        # out as an expected environment condition rather than a page defect.
        # Any *other* console error still fails the test.
        unexpected_errors = [
            e for e in console_errors if "Firebase configuration missing" not in e
        ]
        assert unexpected_errors == [], f"JavaScript console errors on load: {unexpected_errors}"
