import CoreLocation
import PassKit
import SafariServices
import SwiftUI
import WebKit

/// Message-handler name used to ferry the downloaded .pkpass bytes (base64)
/// from the WKWebView's JS fetch back into Swift. Declared here so the
/// Coordinator registration and the injected JS stay in sync.
private let pkpassMessageName = "pkpassDownload"

/// Message-handler name used to bridge W3C navigator.geolocation calls from
/// inside the WKWebView to the native CLLocationManager.
private let locationMessageName = "crushLocation"

private let locationBridgeScript = """
(function () {
    if (!window.webkit || !window.webkit.messageHandlers || !window.webkit.messageHandlers.crushLocation) {
        return;
    }
    var watchCallbacks = {};
    var nextWatchId = 1;

    window.__crushLocationSuccess = function (id, pos) {
        var cb = watchCallbacks[id];
        if (cb && typeof cb.success === 'function') {
            try { cb.success(pos); } catch (e) { console.error('crushLocation success error', e); }
        }
    };

    window.__crushLocationError = function (id, err) {
        var cb = watchCallbacks[id];
        if (cb && typeof cb.error === 'function') {
            try { cb.error(err); } catch (e) { console.error('crushLocation error callback error', e); }
        }
    };

    // The page installs window.__crushHeadingUpdate only when it shows a
    // compass (cache-play.js attachCompass). Tell the app when that happens,
    // so the heading sensor runs only for a page that consumes it.
    var headingHook = null;
    try {
        Object.defineProperty(window, '__crushHeadingUpdate', {
            configurable: true,
            get: function () { return headingHook; },
            set: function (fn) {
                headingHook = typeof fn === 'function' ? fn : null;
                window.webkit.messageHandlers.crushLocation.postMessage({
                    action: "headingConsumer",
                    enabled: headingHook !== null
                });
            }
        });
    } catch (e) {}

    var customGeolocation = {
        getCurrentPosition: function (success, error, options) {
            var id = nextWatchId++;
            watchCallbacks[id] = {
                success: function (pos) {
                    delete watchCallbacks[id];
                    if (typeof success === 'function') success(pos);
                },
                error: function (err) {
                    delete watchCallbacks[id];
                    if (typeof error === 'function') error(err);
                }
            };
            window.webkit.messageHandlers.crushLocation.postMessage({
                action: "getCurrentPosition",
                id: id,
                options: options || {}
            });
        },
        watchPosition: function (success, error, options) {
            var id = nextWatchId++;
            watchCallbacks[id] = { success: success, error: error };
            window.webkit.messageHandlers.crushLocation.postMessage({
                action: "watchPosition",
                id: id,
                options: options || {}
            });
            return id;
        },
        clearWatch: function (id) {
            delete watchCallbacks[id];
            window.webkit.messageHandlers.crushLocation.postMessage({
                action: "clearWatch",
                id: id
            });
        }
    };

    try {
        Object.defineProperty(navigator, 'geolocation', {
            value: customGeolocation,
            configurable: true,
            writable: true
        });
    } catch (e) {
        try {
            navigator.geolocation.getCurrentPosition = customGeolocation.getCurrentPosition;
            navigator.geolocation.watchPosition = customGeolocation.watchPosition;
            navigator.geolocation.clearWatch = customGeolocation.clearWatch;
        } catch (e2) {}
    }
})();
"""

struct CrushWebView: UIViewRepresentable {
    @ObservedObject var appState: AppState

    func makeCoordinator() -> Coordinator {
        Coordinator(appState: appState)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        // Bridge for ferrying the .pkpass bytes (base64) from an in-page JS
        // fetch back to Swift. Fetching inside the web view is what lets the
        // download carry the authenticated session cookie — a plain
        // URLSession.shared request would arrive unauthenticated.
        configuration.userContentController.add(context.coordinator, name: pkpassMessageName)
        configuration.userContentController.add(context.coordinator, name: locationMessageName)

        let locationScript = WKUserScript(
            source: locationBridgeScript,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        )
        configuration.userContentController.addUserScript(locationScript)

        configuration.allowsInlineMediaPlayback = true
        // The scanner's <video> is fed by a MediaStream and started from inside a
        // promise chain, so the tap's user-gesture no longer counts by the time
        // play() runs. Mirrors setMediaPlaybackRequiresUserGesture(false) on Android.
        configuration.mediaTypesRequiringUserActionForPlayback = []

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.customUserAgent = "Mozilla/5.0 AppleWebKit/605.1.15 CrushLUApp/1.0.2"

        context.coordinator.webView = webView
        context.coordinator.locationBridge = NativeLocationBridge(webView: webView)
        context.coordinator.load(appState.navigation)
        context.coordinator.registerForNativeEvents()
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.load(appState.navigation)
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, PKAddPassesViewControllerDelegate {
        weak var webView: WKWebView?
        var locationBridge: NativeLocationBridge?
        private let appState: AppState
        private var lastHandledRequestID: UUID?
        private var nativeAuthSession: NativeAuthSession?
        private var observers: [NSObjectProtocol] = []

        init(appState: AppState) {
            self.appState = appState
        }

        deinit {
            observers.forEach(NotificationCenter.default.removeObserver)
        }

        func registerForNativeEvents() {
            let tokenObserver = NotificationCenter.default.addObserver(
                forName: .didUpdateAPNSToken,
                object: nil,
                queue: .main
            ) { [weak self] notification in
                guard
                    let self,
                    let token = notification.object as? String,
                    let webView = self.webView
                else { return }
                NativeBridge.registerDeviceToken(token, in: webView)
            }
            observers.append(tokenObserver)

            let pauseLocationObserver = NotificationCenter.default.addObserver(
                forName: UIApplication.willResignActiveNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                self?.locationBridge?.pauseForInactiveApp()
            }
            observers.append(pauseLocationObserver)

            let resumeLocationObserver = NotificationCenter.default.addObserver(
                forName: UIApplication.didBecomeActiveNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                self?.locationBridge?.resumeForActiveApp()
            }
            observers.append(resumeLocationObserver)
        }

        func load(_ navigation: NavigationRequest) {
            // Keyed on the request id, never the URL. The Coordinator now lives
            // for the whole process (the view is no longer rebuilt via .id), so
            // a URL-keyed guard would permanently swallow every repeat
            // navigation to a URL already visited — including the fixed push
            // deep links, which repeat by design.
            guard lastHandledRequestID != navigation.id, let webView else { return }
            lastHandledRequestID = navigation.id
            var request = URLRequest(url: navigation.url)
            request.setValue("ios-app", forHTTPHeaderField: "X-Crush-Client")
            webView.load(request)
        }

        func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
            // The new document has now replaced the page that registered the
            // watches, so its callbacks are gone: stop the native sensors. Not
            // earlier: a provisional load that fails (DNS, TLS, network) leaves
            // the old page and its live watch in place.
            locationBridge?.stopAll()
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            if let token = UserDefaults.standard.string(forKey: AppDelegate.apnsDeviceTokenKey) {
                NativeBridge.registerDeviceToken(token, in: webView)
            }
        }

        // MARK: - Apple Wallet (.pkpass) & Geolocation

        /// Callback from injected JS fetches or handlers.
        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            if message.name == pkpassMessageName {
                // Reported back either way, so let the next tap through immediately
                // rather than waiting out the deadline.
                passDownloadStartedAt = nil
                guard let body = message.body as? [String: Any] else { return }
                let error = body["error"] as? String
                let base64 = body["data"] as? String

                if let base64, !base64.isEmpty,
                   let data = Data(base64Encoded: base64) {
                    presentAddPassesVC(with: data)
                } else {
                    showAddPassFailure(message: error)
                }
            } else if message.name == locationMessageName {
                guard message.frameInfo.isMainFrame,
                      isTrustedNativeOrigin(message.frameInfo.securityOrigin) else { return }
                if let body = message.body as? [String: Any] {
                    locationBridge?.handleMessage(body)
                }
            }
        }

        /// Kick off an authenticated `.pkpass` download by evaluating a fetch
        /// inside the web view. The fetch shares the page's cookie jar, so the
        /// wallet endpoint's `@login_required` lets the request through.
        func presentAddPassController(for url: URL) {
            guard let webView else { return }
            // Coalesce repeated taps. Each call starts an independent
            // authenticated download, and a profile whose Apple identifiers do
            // not exist yet would have them generated by both requests; the
            // backend claims them atomically so the loser adopts the winner's
            // values, but there is still nothing to gain from two downloads and
            // two native sheets.
            //
            // Deliberately a DEADLINE, not a boolean. A flag cleared only by
            // the JS bridge callback sticks forever if the page navigates away
            // or the web view reloads mid-fetch, and a stuck guard silently
            // no-ops every later tap — which is precisely the "Add to Apple
            // Wallet does nothing" symptom this whole change exists to fix.
            // A deadline cannot get stuck: the worst case is one ignored tap.
            if let startedAt = passDownloadStartedAt,
               Date().timeIntervalSince(startedAt) < Self.passDownloadCoalesceWindow {
                return
            }
            passDownloadStartedAt = Date()
            pendingPassURL = url
            // Escape backslashes and single quotes so the URL is safe to drop
            // into a JS single-quoted string literal. These site URLs are plain
            // HTTP(S) with no such characters, but the guard is cheap.
            let escaped = url.absoluteString
                .replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "'", with: "\\'")

            let script = """
            (function () {
              var url = '\(escaped)';
              fetch(url, { credentials: 'same-origin' })
                .then(function (resp) {
                  if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
                  return resp.blob();
                })
                .then(function (blob) {
                  var reader = new FileReader();
                  reader.onload = function () {
                    // result is "data:application/vnd.apple.pkpass;base64,AAAA..."
                    var b64 = String(reader.result).split(',')[1] || '';
                    window.webkit.messageHandlers.\(pkpassMessageName).postMessage({ data: b64 });
                  };
                  reader.onerror = function () {
                    window.webkit.messageHandlers.\(pkpassMessageName).postMessage({ error: 'read failed' });
                  };
                  reader.readAsDataURL(blob);
                })
                .catch(function (err) {
                  window.webkit.messageHandlers.\(pkpassMessageName).postMessage({ error: String(err && err.message || err) });
                });
            })();
            """
            // The JS fetch posts back asynchronously via the message handler.
            webView.evaluateJavaScript(script, completionHandler: nil)
        }

        /// Parse the downloaded bytes and present the native Add-Pass sheet.
        /// Falls back to Safari if the pass can't be parsed (e.g. the server
        /// returned an error page instead of a real `.pkpass`).
        private func presentAddPassesVC(with data: Data) {
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                guard PKAddPassesViewController.canAddPasses() else {
                    self.showAddPassFailure(message: "Wallet isn't available on this device.")
                    return
                }
                do {
                    let pass = try PKPass(data: data)
                    guard let presentingVC = self.topViewController() else { return }
                    // init(pass:) is failable — it returns nil if the pass
                    // data is structured but not presentable.
                    guard let controller = PKAddPassesViewController(pass: pass) else {
                        self.showAddPassFailure(message: "Couldn't read the pass.")
                        return
                    }
                    controller.delegate = self
                    presentingVC.present(controller, animated: true)
                } catch {
                    self.showAddPassFailure(message: "Couldn't read the pass: \(error.localizedDescription)")
                }
            }
        }

        // MARK: - PKAddPassesViewControllerDelegate

        // The protocol's optional completion callback. Tapping Add or Cancel
        // invokes this; the prior addPassViewController(_:didFinishWith:) name
        // was invented and never called, so the sheet never dismissed.
        func addPassesViewControllerDidFinish(_ controller: PKAddPassesViewController) {
            controller.dismiss(animated: true)
        }

        // MARK: - Add-pass UI helpers

        private func showAddPassFailure(message: String? = nil, fallbackURL: URL? = nil) {
            DispatchQueue.main.async { [weak self] in
                guard let self, let presentingVC = self.topViewController() else { return }
                let alert = UIAlertController(
                    title: "Couldn't Add Pass",
                    message: message ?? "Apple Wallet could not be reached. Try again from Safari.",
                    preferredStyle: .alert
                )
                if let url = fallbackURL ?? self.pendingPassURL {
                    alert.addAction(UIAlertAction(title: "Open in Safari", style: .default) { _ in
                        UIApplication.shared.open(url)
                    })
                }
                alert.addAction(UIAlertAction(title: "OK", style: .cancel))
                presentingVC.present(alert, animated: true)
            }
        }

        /// The `.pkpass` URL we asked the JS bridge to fetch, retained so the
        /// failure fallback can still hand it to Safari if parsing fails.
        private var pendingPassURL: URL?

        /// When the in-flight `.pkpass` fetch started, so a double-tap cannot
        /// start a second download. Nil once the bridge reports back; expires
        /// on its own so it can never wedge the button (see
        /// presentAddPassController).
        private var passDownloadStartedAt: Date?

        /// How long a started download suppresses further taps.
        private static let passDownloadCoalesceWindow: TimeInterval = 15

        /// Walk the presented-VC chain to find the topmost controller to
        /// present from. SwiftUI's hosting controller sits at the root.
        private func topViewController() -> UIViewController? {
            guard let scene = UIApplication.shared.connectedScenes
                .compactMap({ $0 as? UIWindowScene })
                .first(where: { $0.activationState == .foregroundActive }),
                let window = scene.windows.first(where: { $0.isKeyWindow }) ?? scene.windows.first,
                let root = window.rootViewController else {
                return nil
            }
            var top = root
            while let presented = top.presentedViewController { top = presented }
            return top
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }

            if url.scheme == "crushlu" {
                handleCustomScheme(url)
                decisionHandler(.cancel)
                return
            }

            // Apple Wallet download routes must NOT be handed to WKWebView — it
            // can't render or add a pass and silently swallows the download (the
            // cause of "Add to Apple Wallet button does nothing" in the native
            // shell). The dashboard links to /wallet/apple/pass/ and event
            // tickets to /wallet/apple/event-ticket/<id>/pass/ — neither has a
            // .pkpass extension, so match the known wallet paths instead.
            if isAppleWalletPassURL(url) {
                presentAddPassController(for: url)
                decisionHandler(.cancel)
                return
            }

            if shouldStartNativeAuth(for: url) {
                startNativeAuth()
                decisionHandler(.cancel)
                return
            }

            if isInternal(url) {
                decisionHandler(.allow)
                return
            }

            UIApplication.shared.open(url)
            decisionHandler(.cancel)
        }

        /// The only pages that belong in ASWebAuthenticationSession: the login
        /// entry points themselves.
        ///
        /// `crush_login_required` bounces to `crush_lu:login` (`/<lang>/login/`)
        /// and allauth's own `@login_required` bounces to `/accounts/login/`, so
        /// both are here. Everything else under `/accounts/` — password reset,
        /// email management, connected accounts, the confirm-email landing — is
        /// an ordinary signed-in page and must stay in the WKWebView, which is
        /// where the member's session lives. Matching the whole prefix pushed
        /// those into a browser holding a different session.
        private static let authEntryPaths: Set<String> = ["/login/", "/accounts/login/"]

        private func shouldStartNativeAuth(for url: URL) -> Bool {
            guard isInternal(url) else { return false }
            let path = Self.normalizeAuthPath(url.path)
            return Self.authEntryPaths.contains(path) || Self.isProviderLoginStart(path)
        }

        /// `/accounts/<provider>/login/` — the "Continue with LuxID/Google/…"
        /// button on the in-app signup page.
        ///
        /// These must open the auth browser too. Left in the WKWebView, the
        /// OAuth state is stashed in the WebView's session while the provider
        /// redirect leaves for Safari, so the callback lands where it cannot be
        /// matched: signed in inside a browser, still anonymous in the app.
        ///
        /// Excludes `.../login/callback/`, which carries one segment more and
        /// only ever arrives in the browser that started the flow.
        private static func isProviderLoginStart(_ path: String) -> Bool {
            let segments = path.split(separator: "/", omittingEmptySubsequences: true)
            guard segments.count >= 3,
                  segments[0] == "accounts",
                  segments[segments.count - 1] == "login"
            else { return false }
            // /accounts/<provider>/login/ for a dedicated provider, and
            // /accounts/oidc/<provider_id>/login/ for one mounted on allauth's
            // generic OIDC provider, which carries an extra segment. Requiring
            // the last segment to be "login" keeps .../login/callback/ out.
            return segments.count == 3 || (segments.count == 4 && segments[1] == "oidc")
        }

        /// Drop the i18n language prefix and guarantee a trailing slash.
        ///
        /// The trailing slash is not cosmetic: `URL.path` is documented to strip
        /// one, so a suffix test against "/login/" is not something to rely on.
        /// Normalising both sides removes the question.
        private static func normalizeAuthPath(_ rawPath: String) -> String {
            var path = rawPath
            for language in ["/en", "/de", "/fr"] {
                if path == language {
                    return "/"
                }
                if path.hasPrefix(language + "/") {
                    path = String(path.dropFirst(language.count))
                    break
                }
            }
            return path.hasSuffix("/") ? path : path + "/"
        }

        private func startNativeAuth() {
            nativeAuthSession = NativeAuthSession(baseURL: appState.baseURL) { [weak self] completeURL in
                guard let self, let completeURL else { return }
                self.appState.load(completeURL)
            }
            nativeAuthSession?.start()
        }

        private func handleCustomScheme(_ url: URL) {
            guard
                let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
                let completeURLString = components.queryItems?.first(where: { $0.name == "complete_url" })?.value,
                let completeURL = URL(string: completeURLString)
            else { return }
            appState.load(completeURL)
        }

        /// WebKit asks for a new web view whenever a navigation targets one —
        /// `target="_blank"` anchors and `window.open()`. We have no tab UI, and
        /// leaving this unimplemented means WebKit's default applies: the
        /// navigation is silently dropped and the tap does nothing. Load it in
        /// the existing view instead (there are 8 internal `target="_blank"`
        /// links across the crush.lu templates, including the dashboard's
        /// "Add to Apple Wallet" anchor).
        ///
        /// The `.pkpass` links never actually reach here: `decidePolicyFor` runs
        /// first for new-window actions too (with `targetFrame == nil`), and
        /// cancels them into the native Add-Pass flow. This method is the
        /// backstop that makes that independent of tab-opening behaviour.
        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            guard let url = navigationAction.request.url else { return nil }
            if isInternal(url) {
                webView.load(navigationAction.request)
            } else {
                UIApplication.shared.open(url)
            }
            // Returning nil tells WebKit we handled it and no new view is needed.
            return nil
        }

        func webView(
            _ webView: WKWebView,
            requestMediaCapturePermissionFor origin: WKSecurityOrigin,
            initiatedByFrame frame: WKFrameInfo,
            type: WKMediaCaptureType,
            decisionHandler: @escaping (WKPermissionDecision) -> Void
        ) {
            // Only the check-in scanner needs capture, and only our own pages may
            // ask. Granting here skips WebKit's redundant in-page prompt; iOS still
            // shows the system alert on first use (NSCameraUsageDescription).
            guard type == .camera, isInternalHost(origin.host) else {
                decisionHandler(.deny)
                return
            }
            decisionHandler(.grant)
        }

        @available(iOS 15.0, *)
        func webView(
            _ webView: WKWebView,
            requestDeviceOrientationAndMotionPermissionFor origin: WKSecurityOrigin,
            initiatedByFrame frame: WKFrameInfo,
            decisionHandler: @escaping (WKPermissionDecision) -> Void
        ) {
            guard frame.isMainFrame, isTrustedNativeOrigin(origin) else {
                decisionHandler(.deny)
                return
            }
            decisionHandler(.grant)
        }

        /// WKSecurityOrigin reports port 0 when the origin is on its scheme's
        /// default port, so https://crush.lu arrives as 0, not 443. 443 is
        /// accepted as well, so a WebKit that reports the default port
        /// explicitly still passes. Keep the parentheses: `&&` binds tighter
        /// than `||`, and without them any HTTPS origin on port 0 would pass.
        private func isTrustedNativeOrigin(_ origin: WKSecurityOrigin) -> Bool {
            origin.`protocol`.lowercased() == "https"
                && (origin.port == 0 || origin.port == 443)
                && isInternalHost(origin.host)
        }

        private func isInternal(_ url: URL) -> Bool {
            isInternalHost(url.host)
        }

        /// True for the two Crush.lu routes that serve a `.pkpass` download.
        /// Host-scoped so a third-party URL using the same path can't trigger
        /// the native Add-Pass flow. Mirrors the backend routes in urls_crush.py:
        ///   - wallet/apple/pass/                          (member pass)
        ///   - wallet/apple/event-ticket/<id>/pass/        (event ticket)
        private func isAppleWalletPassURL(_ url: URL) -> Bool {
            guard isInternal(url) else { return false }
            let path = url.path.hasSuffix("/") ? url.path : url.path + "/"
            if path == "/wallet/apple/pass/" { return true }
            if path.hasPrefix("/wallet/apple/event-ticket/"), path.hasSuffix("/pass/") {
                return true
            }
            return false
        }

        private func isInternalHost(_ host: String?) -> Bool {
            guard let host = host?.lowercased() else { return false }
            return host == "crush.lu" || host == "www.crush.lu" || host == "test.crush.lu"
        }
    }
}

// MARK: - Native Location Bridge (CoreLocation -> WKWebView navigator.geolocation)

final class NativeLocationBridge: NSObject, CLLocationManagerDelegate {
    private let locationManager = CLLocationManager()
    private weak var webView: WKWebView?
    private var activeWatchIDs = Set<Int>()
    private var pendingCurrentPositionIDs = Set<Int>()
    /// Each request's `maximumAge` from the page's geolocation options, in seconds.
    private var maximumAgeByID: [Int: TimeInterval] = [:]
    /// Used when a page passes no `maximumAge`; it is what Crush Cache asks for.
    private static let defaultMaximumAge: TimeInterval = 5
    /// Floor for an explicit small or zero `maximumAge`: a live fix is already a
    /// fraction of a second old by the time it reaches the delegate.
    private static let minimumMaximumAge: TimeInterval = 1
    /// Never hand the page a fix older than this, whatever it asks for.
    private static let maximumMaximumAge: TimeInterval = 30
    /// Each request's `timeout` from the page's options, in seconds. A request
    /// without one (the spec default is Infinity) never times out.
    private var timeoutByID: [Int: TimeInterval] = [:]
    private var timeoutWorkItems: [Int: DispatchWorkItem] = [:]
    /// True while the page has installed its compass hook.
    private var hasHeadingConsumer = false
    private var isUpdatingLocation = false
    /// The user declined the temporary precise-location prompt on this page.
    private var declinedFullAccuracy = false
    private var lastLocation: CLLocation?
    private var isRequestingFullAccuracy = false
    private var isAppActive = true

    init(webView: WKWebView) {
        self.webView = webView
        super.init()
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyBestForNavigation
        locationManager.distanceFilter = 1.0
        locationManager.activityType = .fitness
        if CLLocationManager.headingAvailable() {
            locationManager.headingFilter = 1.0
        }
    }

    func handleMessage(_ body: [String: Any]) {
        guard let action = body["action"] as? String else { return }
        if action == "headingConsumer" {
            hasHeadingConsumer = (body["enabled"] as? Bool) ?? false
            updateHeadingUpdates()
            return
        }
        guard let id = body["id"] as? Int else { return }

        switch action {
        case "getCurrentPosition":
            maximumAgeByID[id] = Self.maximumAge(from: body)
            timeoutByID[id] = Self.timeout(from: body)
            pendingCurrentPositionIDs.insert(id)
            armTimeout(for: id)
            ensureAuthorizationAndStart()
        case "watchPosition":
            maximumAgeByID[id] = Self.maximumAge(from: body)
            timeoutByID[id] = Self.timeout(from: body)
            activeWatchIDs.insert(id)
            armTimeout(for: id)
            ensureAuthorizationAndStart()
            if let last = lastLocation, isUsable(last), isFresh(last, for: id),
               Date().timeIntervalSince(last.timestamp) < 3.0 {
                dispatchLocation(last, to: id)
                armTimeout(for: id)
            }
        case "clearWatch":
            activeWatchIDs.remove(id)
            pendingCurrentPositionIDs.remove(id)
            forgetRequest(id)
            if activeWatchIDs.isEmpty && pendingCurrentPositionIDs.isEmpty {
                stopLocationServices()
            }
        default:
            break
        }
    }

    func stopAll() {
        forgetAllRequests()
        // The next document installs its own compass hook if it has one, and
        // may ask for precise location again.
        hasHeadingConsumer = false
        declinedFullAccuracy = false
        lastLocation = nil
        stopLocationServices()
    }

    func pauseForInactiveApp() {
        isAppActive = false
        stopLocationServices()
    }

    func resumeForActiveApp() {
        isAppActive = true
        guard !activeWatchIDs.isEmpty || !pendingCurrentPositionIDs.isEmpty else { return }
        ensureAuthorizationAndStart()
    }

    private func ensureAuthorizationAndStart() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            // A clearWatch or a new document can remove every request between
            // queueing this block and running it: then prompt for nothing.
            guard !self.activeWatchIDs.isEmpty || !self.pendingCurrentPositionIDs.isEmpty else { return }
            let status = self.locationManager.authorizationStatus
            switch status {
            case .notDetermined:
                self.locationManager.requestWhenInUseAuthorization()
            case .authorizedWhenInUse, .authorizedAlways:
                self.startWithAppropriateAccuracy()
            case .denied, .restricted:
                self.failActiveRequests(code: 1, message: "Location permission denied")
            @unknown default:
                self.locationManager.requestWhenInUseAuthorization()
            }
        }
    }

    // MARK: - CLLocationManagerDelegate

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            let status = manager.authorizationStatus
            switch status {
            case .authorizedWhenInUse, .authorizedAlways:
                if !self.activeWatchIDs.isEmpty || !self.pendingCurrentPositionIDs.isEmpty {
                    self.startWithAppropriateAccuracy()
                }
            case .denied, .restricted:
                self.failActiveRequests(code: 1, message: "Location permission denied")
            case .notDetermined:
                break
            @unknown default:
                break
            }
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        guard isUsable(location) else { return }
        lastLocation = location

        // Answer each request only with a fix inside its own maximumAge. Core
        // Location's first delivery after a start can be a cached fix, and
        // Crush Cache unlocks a station from the first position it receives,
        // so a pending getCurrentPosition waits for a fresh fix instead.
        let answeredIDs = pendingCurrentPositionIDs.filter { isFresh(location, for: $0) }
        pendingCurrentPositionIDs.subtract(answeredIDs)
        for id in answeredIDs {
            forgetRequest(id)
            dispatchLocation(location, to: id)
        }

        for id in activeWatchIDs where isFresh(location, for: id) {
            dispatchLocation(location, to: id)
            // A watch's timeout covers each wait for a fix: start the next one.
            armTimeout(for: id)
        }

        if activeWatchIDs.isEmpty && pendingCurrentPositionIDs.isEmpty {
            stopLocationServices()
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        let clErr = error as? CLError
        if clErr?.code == .denied {
            failActiveRequests(code: 1, message: "Location permission denied")
        } else {
            dispatchError(code: 2, message: "Location unavailable: \(error.localizedDescription)", to: nil)
            // The page drops a one-shot request's callbacks on its error, so
            // end it here too. Watches stay and keep trying.
            for id in pendingCurrentPositionIDs {
                forgetRequest(id)
            }
            pendingCurrentPositionIDs.removeAll()
            if activeWatchIDs.isEmpty {
                stopLocationServices()
            }
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        let headingAge = Date().timeIntervalSince(newHeading.timestamp)
        guard newHeading.headingAccuracy.isFinite,
              newHeading.headingAccuracy >= 0,
              headingAge >= -5,
              headingAge <= 30 else { return }
        let heading = newHeading.trueHeading >= 0 ? newHeading.trueHeading : newHeading.magneticHeading
        guard heading >= 0 else { return }
        let js = "if (typeof window.__crushHeadingUpdate === 'function') { window.__crushHeadingUpdate(\(heading)); }"
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript(js, completionHandler: nil)
        }
    }

    private func dispatchLocation(_ location: CLLocation, to id: Int) {
        let lat = location.coordinate.latitude
        let lng = location.coordinate.longitude
        let accuracy = location.horizontalAccuracy
        let altitude = location.altitude
        let altAcc = location.verticalAccuracy >= 0 ? "\(location.verticalAccuracy)" : "null"
        let heading = location.course >= 0 ? "\(location.course)" : "null"
        let speed = location.speed >= 0 ? "\(location.speed)" : "null"
        let timestamp = Int64(location.timestamp.timeIntervalSince1970 * 1000)

        let js = """
        if (typeof window.__crushLocationSuccess === 'function') {
            window.__crushLocationSuccess(\(id), {
                coords: {
                    latitude: \(lat),
                    longitude: \(lng),
                    accuracy: \(accuracy),
                    altitude: \(altitude),
                    altitudeAccuracy: \(altAcc),
                    heading: \(heading),
                    speed: \(speed)
                },
                timestamp: \(timestamp)
            });
        }
        """
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript(js, completionHandler: nil)
        }
    }

    private func dispatchError(code: Int, message: String, to targetID: Int?) {
        let escaped = (try? JSONSerialization.data(withJSONObject: [message], options: [.fragmentsAllowed]))
            .flatMap { String(data: $0, encoding: .utf8) }
            .map { String($0.dropFirst().dropLast()) } ?? "\"Location unavailable\""
        let targets = targetID.map { Set([$0]) } ?? activeWatchIDs.union(pendingCurrentPositionIDs)
        for id in targets {
            let js = """
            if (typeof window.__crushLocationError === 'function') {
                window.__crushLocationError(\(id), {
                    code: \(code),
                    message: \(escaped),
                    PERMISSION_DENIED: 1,
                    POSITION_UNAVAILABLE: 2,
                    TIMEOUT: 3
                });
            }
            """
            DispatchQueue.main.async { [weak self] in
                self?.webView?.evaluateJavaScript(js, completionHandler: nil)
            }
        }
    }

    private func isUsable(_ location: CLLocation) -> Bool {
        let age = Date().timeIntervalSince(location.timestamp)
        return CLLocationCoordinate2DIsValid(location.coordinate)
            && location.horizontalAccuracy.isFinite
            && location.horizontalAccuracy > 0
            && age >= -5
            && age <= Self.maximumMaximumAge
    }

    private func isFresh(_ location: CLLocation, for id: Int) -> Bool {
        let maximumAge = maximumAgeByID[id] ?? Self.defaultMaximumAge
        return Date().timeIntervalSince(location.timestamp) <= maximumAge
    }

    /// The page's `maximumAge` (milliseconds) in seconds, clamped to
    /// [minimumMaximumAge, maximumMaximumAge]. JS Infinity maps to the cap.
    private static func maximumAge(from body: [String: Any]) -> TimeInterval {
        guard let options = body["options"] as? [String: Any],
              let milliseconds = (options["maximumAge"] as? NSNumber)?.doubleValue,
              !milliseconds.isNaN,
              milliseconds >= 0 else {
            return defaultMaximumAge
        }
        return min(max(milliseconds / 1000, minimumMaximumAge), maximumMaximumAge)
    }

    private func startWithAppropriateAccuracy() {
        if #available(iOS 14.0, *), locationManager.accuracyAuthorization == .reducedAccuracy {
            // Declined once: do not ask again on every watch restart. Turning
            // Precise Location on in Settings brings us back here through
            // locationManagerDidChangeAuthorization or resumeForActiveApp.
            guard !declinedFullAccuracy else { return }
            guard !isRequestingFullAccuracy else { return }
            isRequestingFullAccuracy = true
            locationManager.requestTemporaryFullAccuracyAuthorization(
                withPurposeKey: "CacheNavigation"
            ) { [weak self] _ in
                // The completion receives only an optional error, never the
                // granted level. iOS updates accuracyAuthorization before it
                // calls this, so read the user's answer back from the manager.
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.isRequestingFullAccuracy = false
                    // The user can leave the hunt while the system prompt is
                    // visible. Do not restart sensors for a canceled watch.
                    guard !self.activeWatchIDs.isEmpty || !self.pendingCurrentPositionIDs.isEmpty else { return }
                    guard self.locationManager.accuracyAuthorization == .fullAccuracy else {
                        self.declinedFullAccuracy = true
                        self.reportPreciseLocationRequired()
                        return
                    }
                    self.startLocationServices()
                }
            }
            return
        }
        declinedFullAccuracy = false
        startLocationServices()
    }

    /// Tell the page precise location is required and end its one-shot
    /// requests, but keep its watches registered. The page stops recreating a
    /// denied watch, so dropping it would strand the hunt until a reload; kept,
    /// the watch receives the first fix once Precise Location is turned on,
    /// and the page's success handler clears its denied state.
    private func reportPreciseLocationRequired() {
        dispatchError(
            code: 1,
            message: "Precise location is required for Cache navigation. Enable Precise Location in Settings and try again.",
            to: nil
        )
        for id in pendingCurrentPositionIDs {
            forgetRequest(id)
        }
        pendingCurrentPositionIDs.removeAll()
    }

    private func startLocationServices() {
        guard isAppActive else { return }
        locationManager.startUpdatingLocation()
        isUpdatingLocation = true
        updateHeadingUpdates()
        // Timeouts measure acquisition time only: they start with the sensors.
        for id in activeWatchIDs.union(pendingCurrentPositionIDs) where timeoutWorkItems[id] == nil {
            armTimeout(for: id)
        }
    }

    /// The heading sensor runs only while the page shows a compass and the
    /// location sensors are running for it.
    private func updateHeadingUpdates() {
        guard CLLocationManager.headingAvailable() else { return }
        if hasHeadingConsumer && isAppActive && isUpdatingLocation {
            locationManager.startUpdatingHeading()
        } else {
            locationManager.stopUpdatingHeading()
        }
    }

    private func failActiveRequests(code: Int, message: String) {
        dispatchError(code: code, message: message, to: nil)
        forgetAllRequests()
        stopLocationServices()
    }

    private func stopLocationServices() {
        locationManager.stopUpdatingLocation()
        isUpdatingLocation = false
        locationManager.stopUpdatingHeading()
        // No acquisition while stopped (background, permission prompt, idle):
        // pause every timeout. The next start gives each request a full wait.
        timeoutWorkItems.values.forEach { $0.cancel() }
        timeoutWorkItems.removeAll()
    }

    /// Forget one request's freshness window and pending timeout.
    private func forgetRequest(_ id: Int) {
        maximumAgeByID.removeValue(forKey: id)
        timeoutByID.removeValue(forKey: id)
        timeoutWorkItems.removeValue(forKey: id)?.cancel()
    }

    /// Forget every request, when the page goes away or permission fails.
    private func forgetAllRequests() {
        activeWatchIDs.removeAll()
        pendingCurrentPositionIDs.removeAll()
        maximumAgeByID.removeAll()
        timeoutByID.removeAll()
        timeoutWorkItems.values.forEach { $0.cancel() }
        timeoutWorkItems.removeAll()
    }

    // MARK: - Request timeouts (W3C `timeout` option, error code 3)

    /// The page's `timeout` (milliseconds) in seconds, or nil for none.
    /// A non-finite value (JS Infinity, the spec default) means no timeout.
    private static func timeout(from body: [String: Any]) -> TimeInterval? {
        guard let options = body["options"] as? [String: Any],
              let milliseconds = (options["timeout"] as? NSNumber)?.doubleValue,
              milliseconds.isFinite else {
            return nil
        }
        return max(milliseconds, 0) / 1000
    }

    /// (Re)start the wait for this request's next fix. Only while the sensors
    /// run: startLocationServices arms requests that arrived before that.
    private func armTimeout(for id: Int) {
        timeoutWorkItems.removeValue(forKey: id)?.cancel()
        guard isUpdatingLocation, let timeout = timeoutByID[id] else { return }
        let item = DispatchWorkItem { [weak self] in
            self?.timeoutFired(for: id)
        }
        timeoutWorkItems[id] = item
        DispatchQueue.main.asyncAfter(deadline: .now() + timeout, execute: item)
    }

    private func timeoutFired(for id: Int) {
        timeoutWorkItems.removeValue(forKey: id)
        let isPending = pendingCurrentPositionIDs.contains(id)
        guard isPending || activeWatchIDs.contains(id) else { return }
        // Stopping the sensors cancels every timer; this only guards a timer
        // that had already been dequeued when they stopped.
        guard isUpdatingLocation else { return }
        dispatchError(code: 3, message: "Location request timed out", to: id)
        if isPending {
            // A one-shot request ends with its error.
            pendingCurrentPositionIDs.remove(id)
            forgetRequest(id)
            if activeWatchIDs.isEmpty && pendingCurrentPositionIDs.isEmpty {
                stopLocationServices()
            }
        } else {
            // A watch keeps looking and reports again if the next wait also
            // passes without a fix.
            armTimeout(for: id)
        }
    }
}
