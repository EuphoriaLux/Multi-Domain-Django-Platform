import AuthenticationServices
import UIKit
import WebKit

extension Notification.Name {
    static let didUpdateAPNSToken = Notification.Name("didUpdateAPNSToken")
    static let didReceiveNotificationURL = Notification.Name("didReceiveNotificationURL")
}

final class NativeAuthSession: NSObject, ASWebAuthenticationPresentationContextProviding {
    private let baseURL: URL
    private let completion: (URL?) -> Void
    private var session: ASWebAuthenticationSession?

    init(baseURL: URL, completion: @escaping (URL?) -> Void) {
        self.baseURL = baseURL
        self.completion = completion
    }

    func start() {
        let handoffURL = URL(string: "/api/mobile/ios/auth/handoff/", relativeTo: baseURL)!.absoluteURL
        var components = URLComponents(url: handoffURL, resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "redirect_uri", value: "crushlu://auth")]

        guard let url = components?.url else {
            completion(nil)
            return
        }

        let authSession = ASWebAuthenticationSession(url: url, callbackURLScheme: "crushlu") { [weak self] callbackURL, _ in
            guard
                let callbackURL,
                let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false),
                let completeURLString = components.queryItems?.first(where: { $0.name == "complete_url" })?.value,
                let completeURL = URL(string: completeURLString)
            else {
                self?.completion(nil)
                return
            }
            self?.completion(completeURL)
        }
        authSession.presentationContextProvider = self
        authSession.prefersEphemeralWebBrowserSession = false
        session = authSession
        authSession.start()
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first { $0.isKeyWindow } ?? ASPresentationAnchor()
    }
}

enum NativeBridge {
    static func registerDeviceToken(_ token: String, in webView: WKWebView) {
        let deviceIdKey = "iosDeviceId"
        var deviceId = UserDefaults.standard.string(forKey: deviceIdKey)
        if deviceId == nil {
            deviceId = UUID().uuidString
            UserDefaults.standard.set(deviceId, forKey: deviceIdKey)
        }

        let payload: [String: Any] = [
            "deviceToken": token,
            "deviceId": deviceId ?? "",
            "environment": "sandbox",
            "bundleId": "lu.crush.app",
            "appVersion": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.0",
            "appBuild": Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1",
            "deviceName": UIDevice.current.name,
            "systemVersion": UIDevice.current.systemVersion
        ]

        guard
            let data = try? JSONSerialization.data(withJSONObject: payload, options: []),
            let json = String(data: data, encoding: .utf8)
        else { return }

        let escaped = json
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "'", with: "\\'")

        let script = """
        fetch('/api/mobile/ios/devices/register/', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-Crush-Client': 'ios-app'
          },
          body: '\(escaped)'
        }).catch(function () {});
        """
        webView.evaluateJavaScript(script)
    }
}
