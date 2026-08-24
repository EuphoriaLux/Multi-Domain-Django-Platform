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

    def test_handoff_is_never_an_untargeted_action_view(self):
        """openExternal() sets no package, so it can resolve back to us."""
        start = self.activity.index("private void startNativeAuth()")
        end = self.activity.index("private String resolveBrowserPackage()")
        body = self.activity[start:end]

        self.assertNotIn("openExternal(", body)
        # Both launch paths pin a concrete browser package.
        self.assertIn("customTab.intent.setPackage(browserPackage)", body)
        self.assertIn("intent.setPackage(fallbackBrowser)", body)

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
