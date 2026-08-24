"""Source guards for how the Android shell launches the login handoff.

The handoff URL is `https://<appHost>/api/mobile/android/auth/handoff/`, and the
manifest claims that host with `autoVerify="true"`. So an untargeted
`ACTION_VIEW` is safe only while App Links stay unverified: the day
`assetlinks.json` gains its `android_app` target, that intent can resolve back
into MainActivity, which loads the handoff in the WebView, gets bounced to a
login page, matches `shouldStartNativeAuth` again, and loops.

These guards exist so the App Links fingerprint can be set on the production
slot without arming that loop — the two changes are only safe together, and
nothing else in the repo records that dependency.

Plain unittest with no Android dependencies, so it runs on the Linux CI box
alongside the Gradle build.
"""

from pathlib import Path
import unittest

ANDROID = Path(__file__).parents[1]
MAIN_ACTIVITY = (
    ANDROID / "app" / "src" / "main" / "java" / "lu" / "crush" / "app" / "MainActivity.java"
)
MANIFEST = ANDROID / "app" / "src" / "main" / "AndroidManifest.xml"
BUILD_GRADLE = ANDROID / "app" / "build.gradle.kts"


class AuthHandoffLaunchTests(unittest.TestCase):
    def setUp(self):
        self.activity = MAIN_ACTIVITY.read_text(encoding="utf-8")

    def test_handoff_opens_in_a_custom_tab(self):
        self.assertIn("CustomTabsIntent", self.activity)
        self.assertIn("CustomTabsClient.getPackageName", self.activity)

    def _start_native_auth_body(self):
        start = self.activity.index("private boolean startNativeAuth()")
        end = self.activity.index("private String resolveBrowserPackage()")
        return self.activity[start:end]

    def test_handoff_is_never_an_untargeted_action_view(self):
        """openExternal() sets no package, so it can resolve back to us."""
        body = self._start_native_auth_body()

        self.assertNotIn("openExternal(", body)
        # Both launch paths pin a concrete browser package.
        self.assertIn("customTab.intent.setPackage(browserPackage)", body)
        self.assertIn("intent.setPackage(fallbackBrowser)", body)

    def test_no_browser_means_no_launch_at_all(self):
        """The dangerous case is a null package, not a missing setPackage call.

        Launching an untargeted ACTION_VIEW when no browser was found would let
        a verified App Link route the handoff straight back into this activity —
        the exact loop this method exists to prevent, reappearing only on
        browser-less or managed devices where nobody would look for it.
        """
        body = self._start_native_auth_body()

        # The null check must short-circuit BEFORE the Intent is constructed.
        null_guard = body.index("fallbackBrowser == null")
        intent_built = body.index("new Intent(Intent.ACTION_VIEW, handoff)")
        self.assertLess(
            null_guard,
            intent_built,
            "the no-browser case must return before an intent is built",
        )
        self.assertIn("return false;", body)

    def test_no_browser_falls_back_to_login_inside_the_webview(self):
        """Dropping the navigation would strand the user; the WebView login works."""
        self.assertIn("if (!startNativeAuth()) {", self.activity)

    def test_browser_probe_excludes_our_own_package(self):
        self.assertIn("!candidatePackage.equals(getPackageName())", self.activity)

    def test_package_visibility_is_declared(self):
        """targetSdk 35 returns an empty query result without <queries>."""
        manifest = MANIFEST.read_text(encoding="utf-8")

        self.assertIn("<queries>", manifest)
        self.assertIn("android.support.customtabs.action.CustomTabsService", manifest)
        self.assertIn('<data android:scheme="https" />', manifest)

    def test_androidx_browser_is_a_dependency(self):
        self.assertIn("androidx.browser:browser", BUILD_GRADLE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
