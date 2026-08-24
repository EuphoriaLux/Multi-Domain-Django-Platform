import PassKit
import SafariServices
import SwiftUI
import WebKit

/// Message-handler name used to ferry the downloaded .pkpass bytes (base64)
/// from the WKWebView's JS fetch back into Swift. Declared here so the
/// Coordinator registration and the injected JS stay in sync.
private let pkpassMessageName = "pkpassDownload"

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
        context.coordinator.load(appState.navigation)
        context.coordinator.registerForNativeEvents()
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.load(appState.navigation)
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, PKAddPassesViewControllerDelegate {
        weak var webView: WKWebView?
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

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            if let token = UserDefaults.standard.string(forKey: AppDelegate.apnsDeviceTokenKey) {
                NativeBridge.registerDeviceToken(token, in: webView)
            }
        }

        // MARK: - Apple Wallet (.pkpass)

        /// Callback from the injected JS fetch: the downloaded `.pkpass` bytes,
        /// base64-encoded, or an error payload. This runs on the page origin so
        /// `credentials: 'same-origin'` carried the session cookie.
        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == pkpassMessageName else { return }
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
            let path = url.path
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
