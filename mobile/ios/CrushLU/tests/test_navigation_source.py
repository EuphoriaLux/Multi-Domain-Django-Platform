"""Source guards for how the iOS shell decides to (re)load a URL.

Two failures pull in opposite directions and both have shipped:

* Reloading too much re-submits a spent one-time auth code. The web view used
  to be rebuilt through ``.id(reloadToken)``, which handed every rebuild a
  fresh Coordinator with an empty guard, so the completion URL was requested
  twice and the second attempt returned "Invalid or expired authentication
  code".
* Reloading too little strands the user. Keeping the Coordinator alive but
  de-duplicating on URL equality means a repeat navigation to a URL already
  visited is silently swallowed — and the APNS payload's deep links are a
  fixed set of constant paths with no query string, so a second "New message"
  push carries a byte-identical URL and would do nothing at all.

The resolution is to de-duplicate on the identity of the *request* rather than
the URL: SwiftUI re-running updateUIView reuses the request, so nothing
reloads; asking AppState to navigate again mints a new one, so it does.
"""

from pathlib import Path
import unittest

IOS_SOURCES = Path(__file__).parents[1] / "CrushLU"


def _source(name):
    return (IOS_SOURCES / name).read_text(encoding="utf-8")


class IOSNavigationSourceTests(unittest.TestCase):
    def test_auth_completion_does_not_recreate_and_reload_webview(self):
        """One-time auth URLs must be loaded by the existing WKWebView once."""
        content_view = _source("ContentView.swift")
        web_view = _source("CrushWebView.swift")

        self.assertNotIn("reloadToken", content_view)
        self.assertNotIn(".id(", content_view)
        self.assertIn(
            "func updateUIView(_ webView: WKWebView, context: Context) {\n"
            "        context.coordinator.load(appState.navigation)\n"
            "    }",
            web_view,
        )

    def test_repeat_navigation_is_not_swallowed_by_a_url_guard(self):
        """The Coordinator outlives the view, so a URL-keyed guard is permanent.

        Tapping the same push notification twice must navigate twice. Guarding
        on URL equality would make the second tap a no-op for the rest of the
        process's life.
        """
        content_view = _source("ContentView.swift")
        web_view = _source("CrushWebView.swift")

        self.assertIn("struct NavigationRequest", content_view)
        self.assertIn("let id = UUID()", content_view)
        # Every navigation entry point mints a new request rather than
        # assigning a bare URL: init, go(to:) and load(_:).
        self.assertEqual(content_view.count("NavigationRequest("), 3)

        self.assertIn("private var lastHandledRequestID: UUID?", web_view)
        self.assertIn(
            "guard lastHandledRequestID != navigation.id, let webView else { return }",
            web_view,
        )
        self.assertNotIn("lastLoadedURL", web_view)

    def test_guard_is_never_resynced_from_the_web_views_own_url(self):
        """`lastLoadedURL = webView.url` in didFinish would reopen the replay.

        The post-login redirect lands on a different URL, which would clear the
        guard and let the next updateUIView re-request the already-consumed
        completion URL. Pinned because it is the obvious-looking fix for the
        swallowed-navigation half.
        """
        web_view = _source("CrushWebView.swift")

        self.assertNotIn("= webView.url", web_view)

    def test_release_metadata_targets_next_testflight_build(self):
        project = (Path(__file__).parents[1] / "project.yml").read_text(encoding="utf-8")

        self.assertIn('MARKETING_VERSION: "1.0.2"', project)
        self.assertIn('CURRENT_PROJECT_VERSION: "8"', project)

    def test_release_uses_production_and_debug_uses_staging(self):
        content_view = _source("ContentView.swift")

        self.assertIn("#if DEBUG", content_view)
        self.assertIn('return URL(string: "https://test.crush.lu")!', content_view)
        self.assertIn('return URL(string: "https://crush.lu")!', content_view)

    def test_location_and_motion_bridges_are_limited_to_trusted_main_frames(self):
        web_view = _source("CrushWebView.swift")

        self.assertIn("forMainFrameOnly: true", web_view)
        self.assertIn("message.frameInfo.isMainFrame", web_view)
        self.assertIn("isTrustedNativeOrigin(message.frameInfo.securityOrigin)", web_view)
        self.assertIn("guard frame.isMainFrame, isTrustedNativeOrigin(origin)", web_view)

    def test_location_bridge_validates_samples_and_stops_with_navigation(self):
        web_view = _source("CrushWebView.swift")

        self.assertIn("guard isUsable(location) else { return }", web_view)
        self.assertIn("location.horizontalAccuracy > 0", web_view)
        self.assertIn("newHeading.headingAccuracy >= 0", web_view)
        self.assertIn("locationBridge?.stopAll()", web_view)
        self.assertIn("UIApplication.willResignActiveNotification", web_view)
        self.assertIn("UIApplication.didBecomeActiveNotification", web_view)
        self.assertIn("requestTemporaryFullAccuracyAuthorization", web_view)

    def test_location_errors_expose_geolocation_permission_constants(self):
        web_view = _source("CrushWebView.swift")

        self.assertIn("PERMISSION_DENIED: 1", web_view)
        self.assertIn("POSITION_UNAVAILABLE: 2", web_view)
        self.assertIn("TIMEOUT: 3", web_view)

    def test_accuracy_permission_callback_rechecks_active_consumers(self):
        web_view = _source("CrushWebView.swift")
        callback = web_view.split("requestTemporaryFullAccuracyAuthorization(", 1)[1]

        self.assertIn(
            "guard !self.activeWatchIDs.isEmpty || !self.pendingCurrentPositionIDs.isEmpty else { return }",
            callback,
        )

    def test_location_and_motion_usage_descriptions_are_present(self):
        info = _source("Info.plist")

        self.assertIn("NSLocationWhenInUseUsageDescription", info)
        self.assertIn("NSLocationTemporaryUsageDescriptionDictionary", info)
        self.assertIn("CacheNavigation", info)
        self.assertIn("NSMotionUsageDescription", info)


if __name__ == "__main__":
    unittest.main()
