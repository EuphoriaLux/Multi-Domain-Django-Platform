from pathlib import Path
import unittest


class IOSNavigationSourceTests(unittest.TestCase):
    def test_auth_completion_does_not_recreate_and_reload_webview(self):
        """One-time auth URLs must be loaded by the existing WKWebView once."""
        content_view = (
            Path(__file__).parents[1] / "CrushLU" / "ContentView.swift"
        ).read_text(encoding="utf-8")
        web_view = (
            Path(__file__).parents[1] / "CrushLU" / "CrushWebView.swift"
        ).read_text(encoding="utf-8")

        self.assertNotIn("reloadToken", content_view)
        self.assertNotIn(".id(", content_view)
        self.assertIn(
            "func updateUIView(_ webView: WKWebView, context: Context) {\n"
            "        context.coordinator.load(appState.currentURL)\n"
            "    }",
            web_view,
        )
        self.assertIn("private var lastLoadedURL: URL?", web_view)
        self.assertIn("guard lastLoadedURL != url, let webView else { return }", web_view)

    def test_release_metadata_targets_testflight_build_4(self):
        project = (
            Path(__file__).parents[1] / "project.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('MARKETING_VERSION: "1.0.2"', project)
        self.assertIn('CURRENT_PROJECT_VERSION: "4"', project)


if __name__ == "__main__":
    unittest.main()
