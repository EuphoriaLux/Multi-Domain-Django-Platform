"""
UX review Wave 3, WP2 (auth page): crush_lu/auth.html (the live tabbed
/login/ /signup/ page) and its JS/CSS.

2-05  Social login buttons no longer overwrite their own textContent (the
      provider logo survives a click), and a bfcache `pageshow` restore
      resets every busy/disabled state instead of leaving the page frozen.
2-07  The tab bar carries real ARIA tab semantics, and the inactive tab has
      a dark-mode style distinct from the active one.
2-08  The login fields carry autocomplete tokens; the field to focus on
      load is marked with data-autofocus-candidate (JS decides whether to
      actually focus it, gated on a fine pointer) instead of a literal
      autofocus attribute.
2-09  Field-level errors carry aria-invalid/aria-describedby and a
      dark-mode-visible color; non-field error boxes are role=alert. The
      OTP modal's digit inputs are labelled and its error box is
      role=alert with a dark variant.
2-11  The "(optional)" last-name label no longer wraps to a second line.
2-13  Both tab panels and the signup reassurance strip carry x-cloak.
2-16  The live page — not the dead crush_lu/signup.html — fires the signup
      funnel's page-view event, gated to the signup tab.
"""

import os
import re
from unittest import mock

from django.core.cache import cache
from django.template import Context, Template
from django.test import Client, RequestFactory, TestCase

ANALYTICS_ENV = {
    "GA4_CRUSH_LU": "G-TEST12345",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=test-key",
}


def _input_tag(html, field_id):
    match = re.search(rf'<input\b[^>]*\bid="{field_id}"[^>]*>', html)
    assert match, f"#{field_id} is missing from the page"
    return match.group(0)


class AuthTabsAriaTests(TestCase):
    """2-07: real tab semantics, plus a dark-mode-visible inactive tab."""

    def setUp(self):
        cache.clear()

    def test_tabs_carry_tablist_and_tab_roles(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        self.assertIn('role="tablist"', html)
        login_tab = re.search(r'<button\b[^>]*id="auth-tab-login"[^>]*>', html)
        signup_tab = re.search(r'<button\b[^>]*id="auth-tab-signup"[^>]*>', html)
        self.assertTrue(login_tab and 'role="tab"' in login_tab.group(0))
        self.assertTrue(signup_tab and 'role="tab"' in signup_tab.group(0))
        self.assertIn('aria-controls="login-panel"', login_tab.group(0))
        self.assertIn('aria-controls="signup-panel"', signup_tab.group(0))
        # Bare-name bindings only: Alpine's CSP-friendly build can't
        # evaluate an inline ternary (isLoginTab ? 'true' : 'false') and
        # silently drops the attribute — see loginAriaSelected/
        # signupAriaSelected getters in alpine-components.js.
        self.assertIn('x-bind:aria-selected="loginAriaSelected"', login_tab.group(0))
        self.assertIn('x-bind:aria-selected="signupAriaSelected"', signup_tab.group(0))
        self.assertNotIn("isLoginTab ?", html)
        self.assertNotIn("isSignupTab ?", html)

    def test_panels_are_labelled_tabpanels(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        self.assertIn(
            'id="login-panel" role="tabpanel" aria-labelledby="auth-tab-login"', html
        )
        self.assertIn(
            'id="signup-panel" role="tabpanel" aria-labelledby="auth-tab-signup"',
            html,
        )

    def test_inactive_tab_has_a_dark_mode_style(self):
        """
        Before this fix, loginTabClass/signupTabClass returned the same
        light-only 'bg-white/50 text-gray-900' for the inactive tab in both
        themes: a light-grey pill next to the gradient pill in dark mode,
        indistinguishable from a second active-looking button.
        """
        with open(
            "crush_lu/static/crush_lu/js/alpine-components.js", encoding="utf-8"
        ) as f:
            src = f.read()
        start = src.index('Alpine.data("tabNav"')
        end = src.index("setLogin: function", start)
        tab_nav_src = src[start:end]
        self.assertIn("dark:text-gray-300", tab_nav_src)
        self.assertIn("dark:bg-transparent", tab_nav_src)


class AuthPanelsCloakTests(TestCase):
    """2-13: no flash-of-both-forms before Alpine initializes."""

    def setUp(self):
        cache.clear()

    def test_login_and_signup_panels_carry_x_cloak(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        self.assertIn('id="login-panel" role="tabpanel"', html)
        login_panel_open = html[
            html.index('id="login-panel"') : html.index('id="login-panel"') + 400
        ]
        signup_panel_open = html[
            html.index('id="signup-panel"') : html.index('id="signup-panel"') + 400
        ]
        self.assertIn("x-cloak", login_panel_open)
        self.assertIn("x-cloak", signup_panel_open)

    def test_signup_reassurance_strip_carries_x_cloak(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/signup/").content.decode()
        strip = html[
            html.index('x-show="isSignupTab"')
            - 20 : html.index('x-show="isSignupTab"')
            + 200
        ]
        self.assertIn("x-cloak", strip)


class LoginFieldAutocompleteTests(TestCase):
    """2-08: autocomplete tokens, and no forced autofocus on a touch device."""

    def setUp(self):
        cache.clear()

    def test_login_email_has_autocomplete_and_inputmode(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        tag = _input_tag(html, "id_login")
        self.assertIn('autocomplete="username email"', tag)
        self.assertIn('inputmode="email"', tag)
        # The literal autofocus attribute is gone — see AuthJSBehaviorTests
        # for the pointer-fine-gated JS replacement.
        self.assertNotRegex(tag, r"\sautofocus\b")

    def test_login_password_has_current_password_autocomplete(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        tag = _input_tag(html, "id_password")
        self.assertIn('autocomplete="current-password"', tag)
        self.assertNotRegex(tag, r"\sautofocus\b")

    def test_autofocus_candidate_marks_the_empty_email_field_on_first_visit(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        self.assertIn("data-autofocus-candidate", _input_tag(html, "id_login"))
        self.assertNotIn("data-autofocus-candidate", _input_tag(html, "id_password"))


class AuthJSBehaviorTests(TestCase):
    """2-05 and 2-08: bfcache reset and pointer-fine-gated autofocus, in the
    rendered <script> block (there is no live-server JS runtime in this
    suite, so these are source-level regressions on the exact bug)."""

    def setUp(self):
        cache.clear()

    def _script(self):
        return Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()

    def test_social_buttons_never_overwrite_textcontent(self):
        html = self._script()
        script = html[html.index("Unified Auth Page Handler") :]
        self.assertNotIn("button.textContent", script)

    def test_pageshow_persisted_resets_are_registered(self):
        html = self._script()
        script = html[html.index("Unified Auth Page Handler") :]
        self.assertIn("resetters", script)
        self.assertIn("pageshow", script)
        self.assertIn("e.persisted", script)

    def test_autofocus_is_gated_on_a_fine_pointer(self):
        html = self._script()
        script = html[html.index("Unified Auth Page Handler") :]
        self.assertIn("data-autofocus-candidate", script)
        self.assertIn("pointer: fine", script)


class SignupFieldAriaTests(TestCase):
    """2-09: field errors are announced and readable in dark mode."""

    def setUp(self):
        cache.clear()

    def test_duplicate_email_error_is_described_and_dark_mode_visible(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        User.objects.create_user(
            username="dupe@example.com",
            email="dupe@example.com",
            password="Str0ng-pass-2026!",
        )
        response = Client(HTTP_HOST="crush.lu").post(
            "/en/signup/",
            {
                "first_name": "Dup",
                "email": "dupe@example.com",
                "password1": "Str0ng-pass-2026!",
                "password2": "Str0ng-pass-2026!",
                "crushlu_consent": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        email_tag = _input_tag(html, "id_email")
        self.assertIn('aria-invalid="true"', email_tag)
        self.assertIn('aria-describedby="id_email_error"', email_tag)
        self.assertIn('id="id_email_error"', html)
        self.assertIn('role="alert"', html)
        self.assertIn("dark:text-red-400", html)

    def test_valid_field_reports_aria_invalid_false(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/signup/").content.decode()
        self.assertIn('aria-invalid="false"', _input_tag(html, "id_email"))
        self.assertIn('aria-invalid="false"', _input_tag(html, "id_password1"))


class OtpModalAriaTests(TestCase):
    """2-09: OTP digits are labelled, grouped, and the error box is alertable."""

    def _render(self):
        factory = RequestFactory()
        request = factory.get("/")
        template = Template('{% include "crush_lu/includes/phone_otp_modal.html" %}')
        return template.render(Context({"request": request}))

    def test_digit_inputs_have_labels_and_are_grouped(self):
        html = self._render()
        self.assertIn('role="group"', html)
        self.assertIn('aria-label="Digit 1 of 6"', html)
        self.assertIn('aria-label="Digit 6 of 6"', html)

    def test_error_box_is_alertable_and_dark_mode_visible(self):
        html = self._render()
        error_div = html[html.index('x-show="hasError"') - 10 :]
        error_div = error_div[: error_div.index(">") + 1]
        self.assertIn('role="alert"', error_div)
        self.assertIn("dark:bg-red-900/30", error_div)
        self.assertIn("dark:text-red-300", error_div)


class SignupLastNameLayoutTests(TestCase):
    """2-11: the '(optional)' label no longer wraps and misaligns the row."""

    def setUp(self):
        cache.clear()

    def test_optional_label_does_not_wrap_and_row_aligns_to_the_bottom(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/signup/").content.decode()
        label = html[html.index("Last Name") - 200 : html.index("Last Name") + 200]
        self.assertIn("whitespace-nowrap", label)
        grid_open = html[
            html.index('class="grid grid-cols-2 gap-4 mb-4') : html.index(
                'class="grid grid-cols-2 gap-4 mb-4'
            )
            + 80
        ]
        self.assertIn("items-end", grid_open)


class SignupFunnelAnalyticsTests(TestCase):
    """2-16: the live auth page — not the dead signup.html — instruments the
    funnel, and only on the signup tab."""

    def setUp(self):
        cache.clear()

    def test_signup_page_view_fires_only_on_the_signup_tab(self):
        """
        appinsights_event/ga4_event (json.dumps-quoted, double quotes) render
        the page-view event; the always-present tab-switch tracker in
        extra_js uses single-quoted JS literals for the *click* event, so the
        two are distinguishable even though both name the same event.

        Deliberately "signup_page_viewed", not GA4's reserved "sign_up"
        conversion event: signup.html fired "sign_up" on every page view,
        counting abandoned visits as completed registrations (the bug 2-16
        is about). The real conversion firing is deferred — see
        findings_deferred — to the successful complete_signup() hook.
        """
        with mock.patch.dict(os.environ, ANALYTICS_ENV):
            signup_html = (
                Client(HTTP_HOST="crush.lu").get("/en/signup/").content.decode()
            )
            login_html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        self.assertIn('"signup_page_viewed"', signup_html)
        self.assertNotIn('"sign_up"', signup_html)
        self.assertNotIn('"signup_page_viewed"', login_html)

    def test_dead_signup_template_is_gone(self):
        import os as _os

        self.assertFalse(_os.path.exists("crush_lu/templates/crush_lu/signup.html"))

    def test_tab_switch_to_signup_is_tracked_in_js(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        script = html[html.index("Unified Auth Page Handler") :]
        self.assertIn("auth-tab-signup", script)
        self.assertIn("signup_page_viewed", script)
        self.assertNotIn("'sign_up'", script)
