"""Which URLs the native shells hand to the external auth browser.

Both shells cancel a WebView navigation and open a browser session when the
URL looks like a login page. The predicate used to be
``path.endsWith("/login/") || path.contains("/accounts/")``, which matched far
more than login: password reset, email management, connected accounts and the
confirm-email landing all live under ``/accounts/``. Those are ordinary
signed-in pages, and pushing them into a browser puts them in front of a
*different* session from the one in the WebView, so the flows either dead-end
or apply somewhere the member cannot see.

The rule is now an allowlist of the actual entry points: the login pages, plus
`/accounts/<provider>/login/` matched structurally so a newly configured
provider is covered without touching either shell. Narrowing it to the login
pages alone was tried first and was wrong — it stranded social signup, because
the OAuth state is stashed in the WebView session while the provider redirect
leaves for the system browser, so the callback lands where it cannot be
matched and the member ends up signed in to a browser and anonymous in the app.

This test models the rule against the real URL surface (taken from the crush
urlconf) so both a widening and that narrowing have to break a named
expectation rather than slipping through.

The model below is deliberately a re-implementation rather than a parse of the
Swift and Java: it states what the rule should be, and the source guards at the
bottom check both shells still implement that rule.
"""

from pathlib import Path
import unittest

LANGUAGE_PREFIXES = ("/en", "/de", "/fr")
AUTH_ENTRY_PATHS = {"/login/", "/accounts/login/"}


def normalize_auth_path(raw_path):
    path = raw_path or ""
    for language in LANGUAGE_PREFIXES:
        if path == language:
            return "/"
        if path.startswith(language + "/"):
            path = path[len(language):]
            break
    return path if path.endswith("/") else path + "/"


def is_provider_login_start(path):
    """/accounts/<provider>/login/ — the "Continue with X" buttons.

    Excludes /accounts/<provider>/login/callback/, one segment longer, which
    only ever arrives in the browser that started the flow.
    """
    segments = [s for s in path.split("/") if s]
    return len(segments) == 3 and segments[0] == "accounts" and segments[2] == "login"


def should_start_native_auth(path):
    normalized = normalize_auth_path(path)
    return normalized in AUTH_ENTRY_PATHS or is_provider_login_start(normalized)


# Every login entry point a WebView can be bounced to. crush_login_required
# sends users to crush_lu:login (/<lang>/login/); allauth's own @login_required
# sends them to /accounts/login/.
MUST_INTERCEPT = [
    "/login/",
    "/en/login/",
    "/de/login/",
    "/fr/login/",
    "/accounts/login/",
    # The "Continue with X" buttons on the in-app signup page. Left in the
    # WebView, the OAuth state is stashed in the WebView session while the
    # provider redirect leaves for the system browser, so the callback lands
    # where it cannot be matched: signed in in a browser, anonymous in the app.
    "/accounts/luxid/login/",
    "/accounts/google/login/",
    "/accounts/facebook/login/",
    "/accounts/microsoft/login/",
    "/accounts/apple/login/",
]

# Pages that must stay in the WebView, where the member's session is.
MUST_NOT_INTERCEPT = [
    # The regression this test exists for: account management under /accounts/.
    "/accounts/logout/",
    "/accounts/email/",
    "/accounts/password/change/",
    "/accounts/password/reset/",
    "/accounts/password/reset/key/abc-def/",
    "/accounts/confirm-email/SOMEKEY/",
    "/accounts/3rdparty/",
    "/accounts/social/connections/",
    "/accounts/signup/",
    "/accounts/inactive/",
    "/accounts/reauthenticate/",
    # Callbacks belong to the browser that started the flow — one segment
    # longer than the provider start, and never a reason to open a second
    # auth session on top of the first.
    "/accounts/google/login/callback/",
    "/accounts/apple/login/callback/",
    "/accounts/facebook/login/token/",
    # Ordinary app pages, including the logout the UI actually links to.
    "/en/logout/",
    "/en/signup/",
    "/en/dashboard/",
    "/en/events/",
    "/en/crush-connect/catalogue/",
    "/api/mobile/ios/auth/complete/CODE/",
]


class AuthInterceptScopeTests(unittest.TestCase):
    def test_login_entry_points_are_intercepted(self):
        for path in MUST_INTERCEPT:
            with self.subTest(path=path):
                self.assertTrue(
                    should_start_native_auth(path),
                    f"{path} is a login entry point and must open the auth browser",
                )

    def test_account_pages_stay_in_the_webview(self):
        for path in MUST_NOT_INTERCEPT:
            with self.subTest(path=path):
                self.assertFalse(
                    should_start_native_auth(path),
                    f"{path} must not be pushed into a browser with a different session",
                )

    def test_trailing_slash_is_normalized_not_assumed(self):
        """URL.path is documented to strip a trailing slash on Apple platforms."""
        self.assertTrue(should_start_native_auth("/en/login"))
        self.assertTrue(should_start_native_auth("/accounts/login"))

    def test_both_shells_implement_the_allowlist(self):
        swift = (
            Path(__file__).parents[1] / "CrushLU" / "CrushWebView.swift"
        ).read_text(encoding="utf-8")
        java = (
            Path(__file__).parents[3]
            / "android"
            / "CrushLU"
            / "app"
            / "src"
            / "main"
            / "java"
            / "lu"
            / "crush"
            / "app"
            / "MainActivity.java"
        ).read_text(encoding="utf-8")

        for source, name in ((swift, "CrushWebView.swift"), (java, "MainActivity.java")):
            with self.subTest(source=name):
                # The prefix match is what over-captured; it must not come back.
                self.assertNotIn('contains("/accounts/")', source)
                self.assertIn('"/accounts/login/"', source)
                self.assertIn('"/login/"', source)
                # Provider starts are matched structurally, not enumerated, so a
                # newly added provider is covered without touching the shells.
                self.assertIn("isProviderLoginStart", source)


if __name__ == "__main__":
    unittest.main()
