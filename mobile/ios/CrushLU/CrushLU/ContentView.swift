import Network
import SwiftUI
import UserNotifications

final class AppState: ObservableObject {
    static var baseURL: URL {
        #if DEBUG
        return URL(string: "https://test.crush.lu")!
        #else
        return URL(string: "https://crush.lu")!
        #endif
    }

    var baseURL: URL {
        Self.baseURL
    }

    @Published var selectedDestination: AppDestination = .dashboard
    /// The navigation the shell has most recently been *asked* to perform.
    ///
    /// Identity, not equality: two taps on the same push notification are two
    /// requests. Comparing URLs instead would make them one, because the APNS
    /// payload's `url` is drawn from a fixed set of five constant paths with no
    /// query string — byte-identical repeats are the norm, not an edge case.
    @Published private(set) var navigation: NavigationRequest
    @Published var isOnline = true
    @Published var showPushPrompt = false

    private let monitor = NWPathMonitor()
    private let monitorQueue = DispatchQueue(label: "lu.crush.app.network")

    init() {
        navigation = NavigationRequest(
            url: URL(string: AppDestination.dashboard.path, relativeTo: Self.baseURL)!.absoluteURL
        )
        monitor.pathUpdateHandler = { [weak self] path in
            DispatchQueue.main.async {
                self?.isOnline = path.status == .satisfied
            }
        }
        monitor.start(queue: monitorQueue)

        UNUserNotificationCenter.current().getNotificationSettings { [weak self] settings in
            DispatchQueue.main.async {
                self?.showPushPrompt = settings.authorizationStatus == .notDetermined
            }
        }
    }

    func go(to destination: AppDestination) {
        selectedDestination = destination
        navigation = NavigationRequest(
            url: URL(string: destination.path, relativeTo: baseURL)!.absoluteURL
        )
    }

    func load(_ url: URL) {
        navigation = NavigationRequest(url: url)
    }
}

/// One request to put a URL on screen.
///
/// The `id` is what the web view de-duplicates on, which keeps two different
/// things apart that a URL comparison conflates: SwiftUI re-running
/// `updateUIView` for an unrelated state change (same request, must not reload
/// — that is what would re-submit a spent one-time auth code), and the app
/// being asked to navigate again (new request, must load, even to a URL that
/// is already on screen).
struct NavigationRequest {
    let id = UUID()
    let url: URL
}

enum AppDestination: String, CaseIterable, Identifiable {
    case dashboard
    case events
    case connect
    case profile

    var id: String { rawValue }

    var title: String {
        switch self {
        case .dashboard: return "Home"
        case .events: return "Events"
        case .connect: return "Connect"
        case .profile: return "Profile"
        }
    }

    var symbolName: String {
        switch self {
        case .dashboard: return "house.fill"
        case .events: return "calendar"
        case .connect: return "heart.fill"
        case .profile: return "person.crop.circle"
        }
    }

    var path: String {
        switch self {
        case .dashboard: return "/en/dashboard/?source=ios_app"
        case .events: return "/en/events/?source=ios_app"
        case .connect: return "/en/crush-connect/?source=ios_app"
        case .profile: return "/en/profile/edit/?source=ios_app"
        }
    }
}

struct ContentView: View {
    @StateObject private var appState = AppState()

    var body: some View {
        VStack(spacing: 0) {
            if !appState.isOnline {
                OfflineBanner()
            }

            CrushWebView(appState: appState)

            // No native tab bar: crush.lu renders its own bottom navigation and
            // that one is strictly richer — it is role-aware (coaches get a Coach
            // tab) and carries the live badge counts. Duplicating it natively
            // showed the user two nav bars at once, the native one being a
            // subset. Android's shell has never had one. See PR notes.

            if appState.showPushPrompt {
                PushPermissionPrompt {
                    AppDelegate.requestPushAuthorization { granted in
                        appState.showPushPrompt = !granted
                    }
                }
            }
        }
        .ignoresSafeArea(.keyboard, edges: .bottom)
        .onReceive(NotificationCenter.default.publisher(for: .didReceiveNotificationURL)) { notification in
            guard let url = notification.object as? URL else { return }
            if url.scheme == nil {
                appState.load(URL(string: url.absoluteString, relativeTo: appState.baseURL)!.absoluteURL)
            } else {
                appState.load(url)
            }
        }
    }
}

private struct OfflineBanner: View {
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "wifi.slash")
            Text("Offline")
                .font(.footnote.weight(.semibold))
        }
        .foregroundStyle(.white)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(Color(red: 0.15, green: 0.17, blue: 0.22))
    }
}

private struct PushPermissionPrompt: View {
    let onEnable: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "bell.badge")
                .foregroundStyle(Color(red: 0.43, green: 0.18, blue: 0.73))
            Text("Notifications")
                .font(.footnote.weight(.semibold))
            Spacer()
            Button("Enable", action: onEnable)
                .font(.footnote.weight(.semibold))
                .buttonStyle(.borderedProminent)
                .tint(Color(red: 0.43, green: 0.18, blue: 0.73))
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.thinMaterial)
    }
}

