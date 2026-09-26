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
import re
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
        project = (Path(__file__).parents[1] / "project.yml").read_text(
            encoding="utf-8"
        )

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
        self.assertIn(
            "isTrustedNativeOrigin(message.frameInfo.securityOrigin)", web_view
        )
        self.assertIn(
            "guard frame.isMainFrame, isTrustedNativeOrigin(origin)", web_view
        )

    def test_location_bridge_validates_samples_and_stops_with_navigation(self):
        web_view = _source("CrushWebView.swift")

        self.assertIn("guard isUsable(location) else { return }", web_view)
        self.assertIn("location.horizontalAccuracy > 0", web_view)
        self.assertIn("newHeading.headingAccuracy >= 0", web_view)
        self.assertIn("locationBridge?.stopAll()", web_view)
        self.assertIn("UIApplication.willResignActiveNotification", web_view)
        self.assertIn("UIApplication.didBecomeActiveNotification", web_view)
        self.assertIn("requestTemporaryFullAccuracyAuthorization", web_view)

    def test_location_bridge_honors_the_pages_maximum_age(self):
        """A fix older than the page's maximumAge must never reach it.

        Crush Cache asks for maximumAge 5000 and unlocks a station from the
        positions it receives. Core Location's first delivery after a start can
        be a cached fix, so accepting anything up to the 30 s sanity cap would
        let a player who has walked away unlock from the stale position.
        """
        web_view = _source("CrushWebView.swift")
        bridge = web_view.split("final class NativeLocationBridge", 1)[1]

        # Both request kinds record their own maximumAge from the options the
        # JS shim forwards, and clearWatch/stopAll/failure drop it again.
        self.assertIn('options["maximumAge"]', bridge)
        self.assertEqual(
            bridge.count("maximumAgeByID[id] = Self.maximumAge(from: body)"), 2
        )
        self.assertIn("maximumAgeByID.removeValue(forKey: id)", bridge)
        forget_all = bridge.split("private func forgetAllRequests() {", 1)[1]
        self.assertIn("maximumAgeByID.removeAll()", forget_all.split("\n    }\n", 1)[0])
        for caller in ("func stopAll() {", "private func failActiveRequests("):
            body = bridge.split(caller, 1)[1].split("\n    }\n", 1)[0]
            self.assertIn("forgetAllRequests()", body, caller)
        self.assertIn(
            "return min(max(milliseconds / 1000, minimumMaximumAge), maximumMaximumAge)",
            bridge,
        )
        self.assertIn("private static let maximumMaximumAge: TimeInterval = 30", bridge)

        # Every dispatch path filters by the requester's own freshness window.
        updates = bridge.split("didUpdateLocations locations: [CLLocation]) {", 1)[1]
        updates = updates.split("\n    }\n", 1)[0]
        self.assertIn(
            "pendingCurrentPositionIDs.filter { isFresh(location, for: $0) }", updates
        )
        self.assertIn(
            "for id in activeWatchIDs where isFresh(location, for: id) {", updates
        )
        self.assertNotIn("pendingCurrentPositionIDs.removeAll()", updates)
        self.assertIn("isFresh(last, for: id)", bridge)

    def test_location_bridge_honors_the_pages_timeout(self):
        """A request that sees no fix within its timeout gets error code 3.

        Crush Cache asks for timeout 15000. Without it a one-shot request with
        no fix kept Core Location running forever, and a watch reported
        nothing until the page's own 25 s watchdog.
        """
        web_view = _source("CrushWebView.swift")
        bridge = web_view.split("final class NativeLocationBridge", 1)[1]

        self.assertIn('options["timeout"]', bridge)
        self.assertEqual(bridge.count("timeoutByID[id] = Self.timeout(from: body)"), 2)
        # Infinity (the spec default) and a missing option mean no timer.
        self.assertIn("milliseconds.isFinite else {\n            return nil", bridge)

        fired = bridge.split("private func timeoutFired(for id: Int) {", 1)[1]
        fired = fired.split("\n    }\n", 1)[0]
        self.assertIn(
            'dispatchError(code: 3, message: "Location request timed out", to: id)',
            fired,
        )
        # Timeouts measure acquisition time only. A timer runs only while the
        # sensors do: stopping them (background, permission prompt, idle)
        # cancels every timer, and starting them arms the waiting requests.
        # Checking the state only when a timer fires would still count the
        # paused time if the app came back before the deadline.
        arm = bridge.split("private func armTimeout(for id: Int) {", 1)[1]
        self.assertIn(
            "guard isUpdatingLocation, let timeout = timeoutByID[id] else { return }",
            arm.split("\n    }\n", 1)[0],
        )
        start = bridge.split("private func startLocationServices() {", 1)[1]
        start = start.split("\n    }\n", 1)[0]
        self.assertIn("where timeoutWorkItems[id] == nil {", start)
        self.assertIn("armTimeout(for: id)", start)
        stop = bridge.split("private func stopLocationServices() {", 1)[1]
        stop = stop.split("\n    }\n", 1)[0]
        self.assertIn("timeoutWorkItems.values.forEach { $0.cancel() }", stop)
        for caller in ("func pauseForInactiveApp() {",):
            body = bridge.split(caller, 1)[1].split("\n    }\n", 1)[0]
            self.assertIn("stopLocationServices()", body)
        self.assertNotIn(".notDetermined", fired)
        # A one-shot request ends; a watch keeps looking.
        self.assertIn("pendingCurrentPositionIDs.remove(id)", fired)
        self.assertIn("stopLocationServices()", fired)

        # Each fix delivered to a watch starts its next wait; an answered or
        # cleared request cancels its timer.
        updates = bridge.split("didUpdateLocations locations: [CLLocation]) {", 1)[1]
        updates = updates.split("\n    }\n", 1)[0]
        self.assertIn("armTimeout(for: id)", updates)
        self.assertIn("forgetRequest(id)", updates)
        forget = bridge.split("private func forgetRequest(_ id: Int) {", 1)[1]
        self.assertIn(
            "timeoutWorkItems.removeValue(forKey: id)?.cancel()",
            forget.split("\n    }\n", 1)[0],
        )
        self.assertIn("timeoutWorkItems.values.forEach { $0.cancel() }", bridge)

    def test_sensors_stop_when_a_new_document_commits_not_before(self):
        """Tear down native watches only once the old page is really gone.

        A provisional load that fails (DNS, TLS, network) leaves the Cache
        page and its JS watch alive; stopping at provisional start froze its
        tracking until the page's own watchdog recreated the watch.
        """
        web_view = _source("CrushWebView.swift")
        commit = web_view.split(
            "func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {",
            1,
        )[1]
        self.assertIn("locationBridge?.stopAll()", commit.split("\n        }\n", 1)[0])
        self.assertNotIn("didStartProvisionalNavigation", web_view)

    def test_deferred_start_rechecks_consumers_before_prompting(self):
        """No permission prompt or sensor start once every request is gone.

        ensureAuthorizationAndStart defers to the main queue; a clearWatch or
        a committed navigation can empty both request sets before it runs.
        """
        web_view = _source("CrushWebView.swift")
        body = web_view.split("private func ensureAuthorizationAndStart() {", 1)[1]
        body = body.split("\n    }\n", 1)[0]
        recheck = (
            "guard !self.activeWatchIDs.isEmpty || "
            "!self.pendingCurrentPositionIDs.isEmpty else { return }"
        )
        self.assertIn(recheck, body)
        self.assertLess(
            body.index(recheck), body.index("requestWhenInUseAuthorization()")
        )

    def test_location_failure_ends_one_shot_requests(self):
        """A getCurrentPosition gets one error, and native forgets it too.

        The injected wrapper deletes a one-shot callback on its error; a
        request left in pendingCurrentPositionIDs kept the sensors running
        for a page that could no longer hear it.
        """
        web_view = _source("CrushWebView.swift")
        failed = web_view.split("didFailWithError error: Error) {", 1)[1]
        failed = failed.split("\n    }\n", 1)[0]
        self.assertIn("for id in pendingCurrentPositionIDs {", failed)
        self.assertIn("forgetRequest(id)", failed)
        self.assertIn("pendingCurrentPositionIDs.removeAll()", failed)
        self.assertIn(
            "if activeWatchIDs.isEmpty {\n                stopLocationServices()",
            failed,
        )

    def test_heading_sensor_runs_only_for_a_compass_page(self):
        """Heading updates start only once the page installs its compass hook.

        cache-play.js sets window.__crushHeadingUpdate in attachCompass(), and
        only in compass mode. Map mode and one-shot requests must not keep
        the heading sensor and a per-sample evaluateJavaScript running.
        """
        web_view = _source("CrushWebView.swift")
        shim = web_view.split("private let locationBridgeScript = ", 1)[1]
        shim = shim.split('"""\n', 2)[1]
        bridge = web_view.split("final class NativeLocationBridge", 1)[1]

        self.assertIn("Object.defineProperty(window, '__crushHeadingUpdate'", shim)
        self.assertIn('action: "headingConsumer"', shim)
        self.assertIn("enabled: headingHook !== null", shim)

        self.assertIn('if action == "headingConsumer" {', bridge)
        # The only place that starts the heading sensor is gated on a consumer.
        self.assertEqual(bridge.count("startUpdatingHeading()"), 1)
        self.assertIn(
            "if hasHeadingConsumer && isAppActive && isUpdatingLocation {\n"
            "            locationManager.startUpdatingHeading()",
            bridge,
        )
        start = bridge.split("private func startLocationServices() {", 1)[1]
        self.assertIn("updateHeadingUpdates()", start.split("\n    }\n", 1)[0])
        # A new document starts without a compass until it installs one.
        stop_all = bridge.split("func stopAll() {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("hasHeadingConsumer = false", stop_all)

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

    def test_accuracy_callback_reads_manager_state_not_completion_error(self):
        """The full-accuracy prompt reports only an optional error, never the level.

        The completion of requestTemporaryFullAccuracyAuthorization(
        withPurposeKey:completion:) takes an ``Error?``. Comparing that argument
        with ``.fullAccuracy`` does not type-check, so the granted level has to
        be read back from the manager, which iOS updates before it calls the
        completion.
        """
        web_view = _source("CrushWebView.swift")
        callback = web_view.split("requestTemporaryFullAccuracyAuthorization(", 1)[1]
        callback = callback.split("startLocationServices()", 1)[0]

        self.assertIn(") { [weak self] _ in", callback)
        self.assertIn(
            "guard self.locationManager.accuracyAuthorization == .fullAccuracy else {",
            callback,
        )
        self.assertIsNone(re.search(r"\bauthorization\s*==", callback))

    def test_precise_location_decline_keeps_the_watch_retryable(self):
        """Declining precise location must not strand the hunt.

        cache-play.js marks code 1 as denied and never recreates a denied
        watch, so native keeps the watch registered (only one-shot requests
        end) and does not re-prompt on every restart. Turning Precise Location
        on reaches startWithAppropriateAccuracy again through
        locationManagerDidChangeAuthorization or resumeForActiveApp, and the
        first fix clears the page's denied state.
        """
        web_view = _source("CrushWebView.swift")
        start = web_view.split("private func startWithAppropriateAccuracy() {", 1)[1]
        start = start.split("\n    }\n", 1)[0]
        callback = start.split("requestTemporaryFullAccuracyAuthorization(", 1)[1]

        self.assertIn("guard !declinedFullAccuracy else { return }", start)
        self.assertLess(
            start.index("guard !declinedFullAccuracy else { return }"),
            start.index("requestTemporaryFullAccuracyAuthorization("),
        )
        self.assertIn("self.declinedFullAccuracy = true", callback)
        self.assertIn("self.reportPreciseLocationRequired()", callback)
        self.assertNotIn("failActiveRequests", callback)

        report = web_view.split("private func reportPreciseLocationRequired() {", 1)[1]
        report = report.split("\n    }\n", 1)[0]
        self.assertIn("code: 1,", report)
        self.assertIn("pendingCurrentPositionIDs.removeAll()", report)
        self.assertNotIn("activeWatchIDs.removeAll()", report)
        self.assertNotIn("forgetAllRequests()", report)

        stop_all = web_view.split("func stopAll() {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("declinedFullAccuracy = false", stop_all)

    def test_trusted_origin_accepts_webkits_default_port_zero(self):
        """WKSecurityOrigin reports port 0 for an origin on its default port.

        WebKit drops a scheme's default port from an origin, so the app's own
        page at https://crush.lu arrives with port 0, not 443. Requiring 443
        alone would reject every geolocation message and every motion
        permission request from crush.lu itself. The parentheses are
        load-bearing: ``&&`` binds tighter than ``||``, so without them any
        HTTPS origin on its default port would pass whatever its host.
        """
        web_view = _source("CrushWebView.swift")
        signature = (
            "private func isTrustedNativeOrigin(_ origin: WKSecurityOrigin) -> Bool {"
        )
        body = web_view.split(signature, 1)[1].split("}", 1)[0]

        self.assertIn('origin.`protocol`.lowercased() == "https"', body)
        self.assertIn("&& (origin.port == 0 || origin.port == 443)", body)
        self.assertIn("&& isInternalHost(origin.host)", body)
        self.assertEqual(body.count("origin.port"), 2)

    def test_swift_sources_have_no_literal_escape_artifacts(self):
        """A line holding a literal backslash-n is a top-level Swift expression.

        ContentView.swift once lost its trailing blank line to the two
        characters backslash and n, with no final newline. The Release build
        stopped there with "expressions are not allowed at the top level",
        before type-checking the rest of the target, while every string guard
        in this module still passed.
        """
        sources = sorted(IOS_SOURCES.glob("*.swift"))
        self.assertTrue(sources)
        for path in sources:
            with self.subTest(source=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.endswith("\n"), "missing trailing newline")
                lines = [line.strip() for line in text.splitlines()]
                self.assertNotIn("\\n", lines)

    def test_location_and_motion_usage_descriptions_are_present(self):
        info = _source("Info.plist")

        self.assertIn("NSLocationWhenInUseUsageDescription", info)
        self.assertIn("NSLocationTemporaryUsageDescriptionDictionary", info)
        self.assertIn("CacheNavigation", info)
        self.assertIn("NSMotionUsageDescription", info)


if __name__ == "__main__":
    unittest.main()
