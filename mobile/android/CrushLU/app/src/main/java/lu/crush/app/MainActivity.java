package lu.crush.app;

import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ProgressBar;

import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.splashscreen.SplashScreen;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import android.content.SharedPreferences;
import android.os.Build;

public class MainActivity extends AppCompatActivity {
    private static final int FILE_CHOOSER_REQUEST = 1001;
    private static final String BASE_URL = BuildConfig.BASE_URL;
    private static final String START_URL = BASE_URL + "/en/dashboard/?source=android_app";
    private static final String AUTH_SCHEME = BuildConfig.AUTH_SCHEME;
    private static final String LOGIN_HANDOFF_URL =
            BASE_URL + "/api/mobile/android/auth/handoff/?redirect_uri=" + Uri.encode(AUTH_SCHEME + "://auth");

    private WebView webView;
    private ProgressBar progressBar;
    private SwipeRefreshLayout swipeRefresh;
    private View offlineView;
    private ValueCallback<Uri[]> filePathCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        SplashScreen.installSplashScreen(this);
        super.onCreate(savedInstanceState);

        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);

        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.web_view);
        progressBar = findViewById(R.id.progress_bar);
        swipeRefresh = findViewById(R.id.swipe_refresh);
        offlineView = findViewById(R.id.offline_view);

        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main_container), (v, insets) -> {
            int statusBarHeight = insets.getInsets(WindowInsetsCompat.Type.statusBars()).top;
            int extraPadding = (int) (16 * getResources().getDisplayMetrics().density);
            v.setPadding(0, statusBarHeight + extraPadding, 0, 0);
            return insets;
        });

        findViewById(R.id.retry_button).setOnClickListener(v -> loadInternal(webView.getUrl() != null ? webView.getUrl() : START_URL));

        configureWebView();
        configureSwipeRefresh();
        configureBackNavigation();

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);

        Uri launchUri = getIntent().getData();
        if (launchUri != null) {
            handleUri(launchUri);
        } else {
            String targetUrl = getIntent().getStringExtra("target_url");
            if (targetUrl != null && !targetUrl.isEmpty()) {
                if (targetUrl.startsWith("/")) {
                    targetUrl = BASE_URL + targetUrl;
                }
                loadInternal(targetUrl);
            } else {
                loadInternal(START_URL);
            }
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
            String targetUrl = intent.getStringExtra("target_url");
            if (targetUrl != null && !targetUrl.isEmpty()) {
                if (targetUrl.startsWith("/")) {
                    targetUrl = BASE_URL + targetUrl;
                }
                loadInternal(targetUrl);
            }
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
        });
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
        if (AUTH_SCHEME.equals(uri.getScheme())) {
            String completeUrl = uri.getQueryParameter("complete_url");
            if (completeUrl != null && !completeUrl.isEmpty()) {
                loadInternal(completeUrl);
            }
            return;
        }

        if (isInternal(uri)) {
            loadInternal(uri.toString());
        } else {
            openExternal(uri);
        }
    }

    private void loadInternal(String url) {
        if (!isOnline()) {
            showOffline();
            return;
        }
        offlineView.setVisibility(View.GONE);
        swipeRefresh.setVisibility(View.VISIBLE);
        webView.loadUrl(url, clientHeaders());
    }

    private void startNativeAuth() {
        openExternal(Uri.parse(LOGIN_HANDOFF_URL));
    }

    private boolean shouldStartNativeAuth(Uri uri) {
        if (!isInternal(uri)) {
            return false;
        }
        String path = uri.getPath() == null ? "" : uri.getPath();
        return path.endsWith("/login/") || path.contains("/accounts/");
    }

    private boolean isInternal(Uri uri) {
        String host = uri.getHost();
        if (host == null) {
            return false;
        }
        String normalized = host.toLowerCase(Locale.ROOT);
        Uri base = Uri.parse(BASE_URL);
        String baseHost = base.getHost() != null ? base.getHost().toLowerCase(Locale.ROOT) : "";
        return normalized.equals(baseHost) || normalized.endsWith("." + baseHost);
    }

    private Map<String, String> clientHeaders() {
        Map<String, String> headers = new HashMap<>();
        headers.put("X-Crush-Client", "android-app");
        return headers;
    }

    private void openExternal(Uri uri) {
        Intent intent = new Intent(Intent.ACTION_VIEW, uri);
        intent.addCategory(Intent.CATEGORY_BROWSABLE);
        startActivity(intent);
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

    private void fixWebHeaderStyle() {
        String js = "(function() { " +
                    "  var css = 'nav, header, .navbar, .nav-bar { " +
                    "    padding-bottom: 12px !important; " +
                    "    min-height: 65px !important; " +
                    "  }'; " +
                    "  var style = document.getElementById('crush-fix-style') || document.createElement('style'); " +
                    "  style.id = 'crush-fix-style'; " +
                    "  style.innerHTML = css; " +
                    "  if (!style.parentElement) document.head.appendChild(style); " +
                    "})();";

        webView.evaluateJavascript(js, null);
        webView.postDelayed(() -> webView.evaluateJavascript(js, null), 1000);
    }

    private final class CrushWebViewClient extends WebViewClient {
        @Override
        public void onPageFinished(WebView view, String url) {
            swipeRefresh.setRefreshing(false);
            fixWebHeaderStyle();
            CookieManager.getInstance().flush();
            registerFcmTokenIfAuthenticated();
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            return handleNavigation(request.getUrl());
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, String url) {
            return handleNavigation(Uri.parse(url));
        }

        private boolean handleNavigation(Uri uri) {
            String scheme = uri.getScheme();
            if (AUTH_SCHEME.equals(scheme)) {
                handleUri(uri);
                return true;
            }
            if (scheme != null && (scheme.equals("tel") || scheme.equals("mailto") || scheme.equals("whatsapp") || scheme.equals("intent"))) {
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
        String deviceId = android.provider.Settings.Secure.getString(getContentResolver(), android.provider.Settings.Secure.ANDROID_ID);
        String deviceName = Build.MANUFACTURER + " " + Build.MODEL;
        String systemVersion = "Android " + Build.VERSION.RELEASE;
        String appVersion = BuildConfig.VERSION_NAME;
        String appBuild = String.valueOf(BuildConfig.VERSION_CODE);

        String js = String.format(
            "(function() { " +
            "  var cookies = document.cookie; " +
            "  var token = '%s'; " +
            "  if (window.FCM_TOKEN_REGISTERED === token) return; " +
            "  var csrfToken = ''; " +
            "  var parts = cookies.split('; '); " +
            "  for (var i = 0; i < parts.length; i++) { " +
            "    if (parts[i].indexOf('csrftoken=') === 0) { " +
            "      csrfToken = parts[i].substring(10); " +
            "      break; " +
            "    } " +
            "  } " +
            "  var data = { " +
            "    registrationToken: token, " +
            "    deviceId: '%s', " +
            "    deviceName: '%s', " +
            "    appVersion: '%s', " +
            "    appBuild: '%s', " +
            "    systemVersion: '%s' " +
            "  }; " +
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
            "      window.FCM_TOKEN_REGISTERED = token; " +
            "      console.log('FCM token registered'); " +
            "    } " +
            "  }) " +
            "  .catch(function(err) { console.error('FCM registration error', err); }); " +
            "})();",
            token, deviceId, deviceName, appVersion, appBuild, systemVersion
        );

        webView.evaluateJavascript(js, null);
    }
}
