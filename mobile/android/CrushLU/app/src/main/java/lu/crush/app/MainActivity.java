package lu.crush.app;

import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ProgressBar;

import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AppCompatActivity;
import androidx.constraintlayout.widget.ConstraintLayout;
import androidx.core.graphics.Insets;
import androidx.core.splashscreen.SplashScreen;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import org.json.JSONException;
import org.json.JSONObject;

import java.net.URISyntaxException;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import android.content.SharedPreferences;
import android.os.Build;

public class MainActivity extends AppCompatActivity {
    private static final int FILE_CHOOSER_REQUEST = 1001;
    private static final int NOTIFICATION_PERMISSION_REQUEST = 1002;
    private static final int CAMERA_PERMISSION_REQUEST = 1003;
    private static final String BASE_URL = BuildConfig.BASE_URL;
    private static final String START_URL = BASE_URL + "/en/dashboard/?source=android_app";
    private static final String AUTH_SCHEME = BuildConfig.AUTH_SCHEME;
    private static final String LOGIN_HANDOFF_URL =
            BASE_URL + "/api/mobile/android/auth/handoff/?redirect_uri=" + Uri.encode(AUTH_SCHEME + "://auth");

    // What the WebView is allowed to load is pinned to the scheme *and* host of
    // BASE_URL: https://crush.lu in production, http://10.0.2.2 for the local
    // flavour (the only cleartext origin in network_security_config.xml).
    private static final Uri BASE_URI = Uri.parse(BASE_URL);
    private static final String BASE_SCHEME = BASE_URI.getScheme() == null
            ? "https" : BASE_URI.getScheme().toLowerCase(Locale.ROOT);
    private static final String BASE_HOST = BASE_URI.getHost() == null
            ? "" : BASE_URI.getHost().toLowerCase(Locale.ROOT);
    private static final int BASE_DEFAULT_PORT = "http".equals(BASE_SCHEME) ? 80 : 443;
    private static final int BASE_PORT = BASE_URI.getPort() == -1
            ? BASE_DEFAULT_PORT : BASE_URI.getPort();

    private WebView webView;
    private ProgressBar progressBar;
    private SwipeRefreshLayout swipeRefresh;
    private View offlineView;
    private ValueCallback<Uri[]> filePathCallback;
    // Held while the OS camera prompt is up: the WebView's request must stay
    // un-answered until the user decides, then be granted or denied.
    private PermissionRequest pendingCameraRequest;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        SplashScreen.installSplashScreen(this);
        super.onCreate(savedInstanceState);

        setContentView(R.layout.activity_main);

        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);

        View filler = findViewById(R.id.bottom_system_bar_filler);
        View mainContainer = findViewById(R.id.main_container);

        ViewCompat.setOnApplyWindowInsetsListener(mainContainer, (v, insets) -> {
            Insets systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars());
            Insets ime = insets.getInsets(WindowInsetsCompat.Type.ime());
            
            // Set top padding for status bar
            int extraTopPadding = (int) (16 * getResources().getDisplayMetrics().density);
            v.setPadding(0, systemBars.top + extraTopPadding, 0, 0);

            // Set the filler height to match the navigation bar or keyboard
            if (filler != null) {
                // Use the maximum of navigation bar and keyboard height to ensure
                // we don't overlap with either.
                filler.getLayoutParams().height = Math.max(systemBars.bottom, ime.bottom);
                filler.requestLayout();
            }
            return WindowInsetsCompat.CONSUMED;
        });

        webView = findViewById(R.id.web_view);
        progressBar = findViewById(R.id.progress_bar);
        swipeRefresh = findViewById(R.id.swipe_refresh);
        offlineView = findViewById(R.id.offline_view);

        findViewById(R.id.retry_button).setOnClickListener(v -> loadInternal(webView.getUrl() != null ? webView.getUrl() : START_URL));

        configureWebView();
        configureSwipeRefresh();
        configureBackNavigation();
        maybeRequestNotificationPermission();
        // Create the high-importance channel up front so background pushes,
        // which the system renders via the default_notification_channel_id
        // manifest meta-data, get a heads-up banner instead of a fallback channel.
        MyFirebaseMessagingService.ensureNotificationChannel(this);

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);

        Uri launchUri = getIntent().getData();
        if (launchUri != null) {
            handleUri(launchUri);
        } else {
            String targetUrl = pendingTargetUrl(getIntent());
            loadInternal(targetUrl != null ? targetUrl : START_URL);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        Uri uri = intent.getData();
        if (uri != null) {
            handleUri(uri);
        } else {
            String targetUrl = pendingTargetUrl(intent);
            if (targetUrl != null) {
                loadInternal(targetUrl);
            }
        }
    }

    private String pendingTargetUrl(Intent intent) {
        String targetUrl = intent.getStringExtra("target_url");
        if (targetUrl == null || targetUrl.isEmpty()) {
            // Background FCM notifications are rendered by the system; tapping
            // them delivers the data payload's raw "url" key as an extra.
            targetUrl = intent.getStringExtra("url");
        }
        if (targetUrl == null || targetUrl.isEmpty()) {
            return null;
        }
        return targetUrl.startsWith("/") ? BASE_URL + targetUrl : targetUrl;
    }

    private void maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                        != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{android.Manifest.permission.POST_NOTIFICATIONS},
                    NOTIFICATION_PERMISSION_REQUEST);
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != FILE_CHOOSER_REQUEST || filePathCallback == null) {
            return;
        }
        Uri[] result = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
        filePathCallback.onReceiveValue(result);
        filePathCallback = null;
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setSupportZoom(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        // The WebView only ever shows our own origin, so it never needs the
        // local filesystem or content providers. setAllowFileAccess still
        // defaults to true below API 30, which minSdk 26 includes; both are
        // what turns an injected file:// or content:// URL into data theft.
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setUserAgentString(settings.getUserAgentString() + " CrushLUAndroid/" + BuildConfig.VERSION_NAME);

        webView.setWebViewClient(new CrushWebViewClient());
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progressBar.setProgress(newProgress);
                if (newProgress == 100) {
                    progressBar.setVisibility(View.GONE);
                } else {
                    progressBar.setVisibility(View.VISIBLE);
                }
            }

            @Override
            public boolean onShowFileChooser(
                    WebView webView,
                    ValueCallback<Uri[]> filePathCallback,
                    FileChooserParams fileChooserParams
            ) {
                if (MainActivity.this.filePathCallback != null) {
                    MainActivity.this.filePathCallback.onReceiveValue(null);
                }
                MainActivity.this.filePathCallback = filePathCallback;
                try {
                    startActivityForResult(fileChooserParams.createIntent(), FILE_CHOOSER_REQUEST);
                } catch (ActivityNotFoundException exception) {
                    MainActivity.this.filePathCallback = null;
                    return false;
                }
                return true;
            }

            @Override
            public void onPermissionRequest(PermissionRequest request) {
                // WebChromeClient's default implementation denies outright, so
                // without this override getUserMedia() in the check-in scanner
                // fails with NotAllowedError and no prompt is ever shown.
                boolean wantsCamera = false;
                for (String resource : request.getResources()) {
                    if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) {
                        wantsCamera = true;
                    }
                }
                // Only our own pages may reach the camera; anything else (an
                // embedded third-party frame) is refused.
                if (!wantsCamera || !isInternal(request.getOrigin())) {
                    request.deny();
                    return;
                }

                if (checkSelfPermission(android.Manifest.permission.CAMERA)
                        == PackageManager.PERMISSION_GRANTED) {
                    request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
                    return;
                }

                if (pendingCameraRequest != null) {
                    // A prompt is already up (e.g. Start tapped twice); leaving
                    // the newer request unanswered would wedge the scanner.
                    request.deny();
                    return;
                }
                pendingCameraRequest = request;
                requestPermissions(
                        new String[]{android.Manifest.permission.CAMERA},
                        CAMERA_PERMISSION_REQUEST);
            }

            @Override
            public void onPermissionRequestCanceled(PermissionRequest request) {
                if (request.equals(pendingCameraRequest)) {
                    pendingCameraRequest = null;
                }
            }
        });
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != CAMERA_PERMISSION_REQUEST || pendingCameraRequest == null) {
            return;
        }
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            pendingCameraRequest.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
        } else {
            // Denying (rather than dropping it) lets the page surface its
            // "camera permission denied" message instead of hanging.
            pendingCameraRequest.deny();
        }
        pendingCameraRequest = null;
    }

    private void configureSwipeRefresh() {
        swipeRefresh.setColorSchemeResources(R.color.crush_primary);
        swipeRefresh.setOnRefreshListener(() -> {
            if (webView.getUrl() != null) {
                webView.reload();
            } else {
                loadInternal(START_URL);
            }
        });

        // Disable swipe-to-refresh if the WebView is scrolled down
        webView.getViewTreeObserver().addOnScrollChangedListener(() -> {
            if (webView != null) {
                swipeRefresh.setEnabled(webView.getScrollY() == 0);
            }
        });
    }

    private void configureBackNavigation() {
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack();
                } else {
                    setEnabled(false);
                    onBackPressed();
                }
            }
        });
    }

    private void handleUri(Uri uri) {
        if (AUTH_SCHEME.equals(schemeOf(uri))) {
            // Anything — another app, or a web page, since the scheme's
            // intent-filter is BROWSABLE — can send this. loadInternal() is what
            // decides whether complete_url is ours; an opaque "crushlu:auth?..."
            // has no query to read and would throw here, so check first.
            loadInternal(uri.isHierarchical() ? uri.getQueryParameter("complete_url") : null);
            return;
        }

        if (isInternal(uri)) {
            loadInternal(uri.toString());
        } else {
            openExternal(uri);
        }
    }

    /**
     * Load a URL in the WebView, but only if it is one of ours.
     *
     * <p>MainActivity is exported, so every route into it — the https deep
     * links, the {@code crushlu://auth} callback, the {@code target_url} /
     * {@code url} notification extras — can be forged by any other app on the
     * device. Validation therefore lives here, at the single point that reaches
     * {@link WebView#loadUrl}, rather than at each call site. That keeps
     * {@code javascript:} (which would run against the signed-in crush.lu page
     * currently on screen), {@code file:}, {@code content:} and attacker-owned
     * origins out of a WebView holding the user's session cookies — the
     * cross-app scripting case in Play's Device and Network Abuse policy.
     */
    private void loadInternal(String url) {
        String safeUrl = internalUrlOrNull(url);
        if (safeUrl == null) {
            if (webView.getUrl() != null) {
                // A legitimate page is already on screen; a forged intent must
                // not be able to navigate away from it.
                return;
            }
            // Cold start from a bad intent: show the app rather than nothing.
            safeUrl = START_URL;
        }
        if (!isOnline()) {
            showOffline();
            return;
        }
        offlineView.setVisibility(View.GONE);
        swipeRefresh.setVisibility(View.VISIBLE);
        webView.loadUrl(safeUrl, clientHeaders());
    }

    /** @return {@code url} when {@link #isInternal} accepts it, else {@code null}. */
    private static String internalUrlOrNull(String url) {
        if (url == null) {
            return null;
        }
        String trimmed = url.trim();
        if (trimmed.isEmpty()) {
            return null;
        }
        // WebView's parser strips embedded tabs/newlines and reads "\" as "/"
        // before resolving the host, so a URL carrying them can end up at a
        // different origin than Uri.parse() below sees. Nothing legitimate
        // needs them inside a URL — they arrive percent-encoded.
        for (int i = 0; i < trimmed.length(); i++) {
            char c = trimmed.charAt(i);
            if (c <= ' ' || c == '\\' || c == 0x7f) {
                return null;
            }
        }
        return isInternal(Uri.parse(trimmed)) ? trimmed : null;
    }

    private void startNativeAuth() {
        openExternal(Uri.parse(LOGIN_HANDOFF_URL));
    }

    private boolean shouldStartNativeAuth(Uri uri) {
        // For local testing, allow login in the WebView to test insets/keyboard
        if (BuildConfig.BASE_URL.contains("10.0.2.2")) {
            return false;
        }
        if (!isInternal(uri)) {
            return false;
        }
        String path = uri.getPath() == null ? "" : uri.getPath();
        return path.endsWith("/login/") || path.contains("/accounts/");
    }

    /**
     * @return the URI's scheme, lower-cased, or "" if it has none. Schemes are
     *     case-insensitive (RFC 3986) and Uri returns them as written, so an
     *     explicit intent carrying "CrushLu://auth" would otherwise slip past
     *     an exact-case comparison.
     */
    private static String schemeOf(Uri uri) {
        String scheme = uri == null ? null : uri.getScheme();
        return scheme == null ? "" : scheme.toLowerCase(Locale.ROOT);
    }

    private static boolean isInternal(Uri uri) {
        if (uri == null || BASE_HOST.isEmpty()) {
            return false;
        }
        // Scheme is checked as well as host: "javascript:", "file:", "content:"
        // and "intent:" URIs have no host and are rejected here, and an
        // http:// link to our own domain is not a substitute for https://.
        if (!BASE_SCHEME.equals(schemeOf(uri))) {
            return false;
        }
        // Credentials in the authority only ever serve to make a hostile host
        // read as ours ("https://crush.lu@evil.example/").
        if (uri.getUserInfo() != null) {
            return false;
        }
        String host = uri.getHost();
        if (host == null) {
            return false;
        }
        String normalized = host.toLowerCase(Locale.ROOT);
        if (!normalized.equals(BASE_HOST) && !normalized.endsWith("." + BASE_HOST)) {
            return false;
        }
        // getHost() drops the port, so match that too — an origin is host *and*
        // port, and nothing of ours is served anywhere but the default one.
        int port = uri.getPort() == -1 ? BASE_DEFAULT_PORT : uri.getPort();
        return port == BASE_PORT;
    }

    private Map<String, String> clientHeaders() {
        Map<String, String> headers = new HashMap<>();
        headers.put("X-Crush-Client", "android-app");
        return headers;
    }

    private void openExternal(Uri uri) {
        Intent intent;
        if ("intent".equals(uri.getScheme())) {
            try {
                intent = Intent.parseUri(uri.toString(), Intent.URI_INTENT_SCHEME);
                // A web page must not be able to target internal components.
                intent.setComponent(null);
                intent.setSelector(null);
            } catch (URISyntaxException exception) {
                return;
            }
        } else {
            intent = new Intent(Intent.ACTION_VIEW, uri);
        }
        intent.addCategory(Intent.CATEGORY_BROWSABLE);
        try {
            startActivity(intent);
        } catch (ActivityNotFoundException | SecurityException exception) {
            // No installed app handles this link, or a parsed intent: URI asked
            // for something this app isn't permitted to launch (e.g. an
            // ACTION_CALL that needs CALL_PHONE). Dropping it beats crashing.
        }
    }

    private void showOffline() {
        progressBar.setVisibility(View.GONE);
        swipeRefresh.setRefreshing(false);
        swipeRefresh.setVisibility(View.GONE);
        offlineView.setVisibility(View.VISIBLE);
    }

    private boolean isOnline() {
        ConnectivityManager manager = getSystemService(ConnectivityManager.class);
        if (manager == null) {
            return true;
        }
        NetworkCapabilities capabilities = manager.getNetworkCapabilities(manager.getActiveNetwork());
        return capabilities != null
                && (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)
                || capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                || capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET));
    }

    private void fixWebStyles() {
        String js = "(function() { " +
                    "  var css = 'nav, header, .navbar, .nav-bar { " +
                    "    padding-bottom: 8px !important; " +
                    "  } " +
                    "  .bottom-nav { " +
                    "    padding-bottom: 4px !important; " +
                    "  }'; " +
                    "  var style = document.getElementById('crush-fix-style') || document.createElement('style'); " +
                    "  style.id = 'crush-fix-style'; " +
                    "  style.innerHTML = css; " +
                    "  if (!style.parentElement) document.head.appendChild(style); " +
                    "})();";

        webView.evaluateJavascript(js, null);
    }

    private final class CrushWebViewClient extends WebViewClient {
        @Override
        public void onPageFinished(WebView view, String url) {
            swipeRefresh.setRefreshing(false);
            fixWebStyles();
            CookieManager.getInstance().flush();
            registerFcmTokenIfAuthenticated();
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            return handleNavigation(request.getUrl());
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) {
                showOffline();
            }
        }

        @Override
        public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
            // onReceivedError only fires for network-level failures; a main-document
            // 5xx (origin/proxy outage) surfaces here instead. Show the offline view
            // rather than leaving the raw server error page in the WebView. 4xx pages
            // (e.g. a 404) carry real content, so they are left to render normally.
            if (request.isForMainFrame() && errorResponse.getStatusCode() >= 500) {
                showOffline();
            }
        }

        private boolean handleNavigation(Uri uri) {
            String scheme = schemeOf(uri);
            if (AUTH_SCHEME.equals(scheme)) {
                handleUri(uri);
                return true;
            }
            if (scheme.equals("tel") || scheme.equals("mailto") || scheme.equals("whatsapp") || scheme.equals("intent")) {
                openExternal(uri);
                return true;
            }
            if (shouldStartNativeAuth(uri)) {
                startNativeAuth();
                return true;
            }
            if (isInternal(uri)) {
                loadInternal(uri.toString());
                return true;
            }
            openExternal(uri);
            return true;
        }
    }

    private void registerFcmTokenIfAuthenticated() {
        final SharedPreferences prefs = getSharedPreferences("CrushPreferences", MODE_PRIVATE);
        final String token = prefs.getString("fcm_token", "");
        if (token.isEmpty()) {
            com.google.firebase.messaging.FirebaseMessaging.getInstance().getToken()
                .addOnCompleteListener(task -> {
                    if (task.isSuccessful() && task.getResult() != null) {
                        String fcmToken = task.getResult();
                        prefs.edit().putString("fcm_token", fcmToken).apply();
                        injectTokenRegistrationScript(fcmToken);
                    }
                });
        } else {
            injectTokenRegistrationScript(token);
        }
    }

    private void injectTokenRegistrationScript(String token) {
        String deviceId = android.provider.Settings.Secure.getString(
                getContentResolver(), android.provider.Settings.Secure.ANDROID_ID);

        JSONObject payload = new JSONObject();
        try {
            payload.put("registrationToken", token);
            payload.put("deviceId", deviceId == null ? "" : deviceId);
            payload.put("deviceName", Build.MANUFACTURER + " " + Build.MODEL);
            payload.put("appVersion", BuildConfig.VERSION_NAME);
            payload.put("appBuild", String.valueOf(BuildConfig.VERSION_CODE));
            payload.put("systemVersion", "Android " + Build.VERSION.RELEASE);
        } catch (JSONException exception) {
            return;
        }

        // The values reach the page as one JSON string literal that
        // JSONObject.quote() escapes in full, so nothing interpolated below can
        // close the quote and become script. (Escaping only ' by hand, as this
        // did, leaves backslashes and newlines to break out.)
        String js = "(function() { " +
            "  var data = JSON.parse(" + JSONObject.quote(payload.toString()) + "); " +
            "  if (window.FCM_TOKEN_REGISTERED === data.registrationToken) return; " +
            "  var csrfToken = ''; " +
            "  var parts = document.cookie.split('; '); " +
            "  for (var i = 0; i < parts.length; i++) { " +
            "    if (parts[i].indexOf('csrftoken=') === 0) { " +
            "      csrfToken = parts[i].substring(10); " +
            "      break; " +
            "    } " +
            "  } " +
            "  fetch('/api/mobile/android/devices/register/', { " +
            "    method: 'POST', " +
            "    headers: { " +
            "      'Content-Type': 'application/json', " +
            "      'X-CSRFToken': csrfToken " +
            "    }, " +
            "    body: JSON.stringify(data) " +
            "  }) " +
            "  .then(function(res) { return res.json(); }) " +
            "  .then(function(res) { " +
            "    if (res.success) { " +
            "      window.FCM_TOKEN_REGISTERED = data.registrationToken; " +
            "      console.log('FCM token registered'); " +
            "    } " +
            "  }) " +
            "  .catch(function(err) { console.error('FCM registration error', err); }); " +
            "})();";

        webView.evaluateJavascript(js, null);
    }
}
