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

    def test_release_metadata_targets_testflight_build_4(self):
        project = (Path(__file__).parents[1] / "project.yml").read_text(encoding="utf-8")

        self.assertIn('MARKETING_VERSION: "1.0.2"', project)
        self.assertIn('CURRENT_PROJECT_VERSION: "4"', project)


if __name__ == "__main__":
    unittest.main()
