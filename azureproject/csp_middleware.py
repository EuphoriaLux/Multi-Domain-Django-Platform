"""
Content Security Policy (CSP) middleware for Crush.lu and multi-domain Django application.

Implements CSP with nonce support for inline scripts. In report-only mode,
violations are logged but not blocked, allowing gradual rollout and testing.

Security Features:
- Nonce-based script/style allowlisting
- Report-Only mode for safe testing
- CSP violation reporting endpoint
- Skip CSP for admin and health check endpoints
"""
import secrets
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


class CSPMiddleware:
    """
    Middleware to add Content-Security-Policy header.

    In report-only mode, violations are logged but not blocked.
    This allows gradual rollout and testing before enforcement.

    Usage:
        1. Add to MIDDLEWARE after SecurityMiddleware
        2. Set CSP_REPORT_ONLY = True for testing (default)
        3. Set CSP_REPORT_ONLY = False to enforce

    Nonce usage in templates:
        <script nonce="{{ request.csp_nonce }}">
            // Inline script
        </script>
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Generate nonce for this request
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce

        response = self.get_response(request)

        # Skip CSP for certain paths
        if self._should_skip_csp(request):
            return response

        # Build and apply CSP policy
        csp_policy = self._build_csp_policy(nonce)

        # Use Report-Only header initially for safe testing
        if getattr(settings, 'CSP_REPORT_ONLY', True):
            response['Content-Security-Policy-Report-Only'] = csp_policy
        else:
            response['Content-Security-Policy'] = csp_policy

        # Add Permissions-Policy header (formerly Feature-Policy)
        # Restricts access to browser features for security
        response['Permissions-Policy'] = self._build_permissions_policy()

        return response

    def _should_skip_csp(self, request):
        """Check if CSP should be skipped for this request."""
        skip_paths = [
            '/admin/',      # Django admin has its own scripts
            '/healthz/',    # Health check endpoint
            '/healthz',
            '/csp-report/', # CSP report endpoint itself
        ]
        return any(request.path.startswith(path) for path in skip_paths)

    def _build_csp_policy(self, nonce):
        """
        Build CSP policy string from settings.

        Policy is designed to:
        - Allow CDN scripts (HTMX, Alpine.js, Bootstrap, Sortable.js)
        - Allow Firebase for phone verification
        - Allow OAuth providers (Google, Facebook, Microsoft)
        - Allow Azure Blob Storage for media
        - Support HTMX and Alpine.js inline handlers (requires 'unsafe-inline' for now)
        """
        directives = []

        # default-src: Only allow self by default
        directives.append("default-src 'self'")

        # script-src: Allow CDN sources, nonce for inline, and OAuth/Firebase
        script_src = [
            "'self'",
            f"'nonce-{nonce}'",
            # Temporary: unsafe-inline for HTMX/Alpine.js event handlers
            # TODO: Migrate to nonce-based approach
            "'unsafe-inline'",
            # Required for Alpine.js reactive system (uses eval internally)
            "'unsafe-eval'",
            # CDN sources (with SRI)
            "https://unpkg.com",
            "https://cdn.jsdelivr.net",
            # Firebase/Google
            "https://www.gstatic.com",
            "https://apis.google.com",
            "https://www.googletagmanager.com",
            "https://www.google.com",  # reCAPTCHA
            # Facebook SDK
            "https://connect.facebook.net",
            # Microsoft
            "https://login.microsoftonline.com",
        ]
        directives.append(f"script-src {' '.join(script_src)}")

        # style-src: Tailwind requires unsafe-inline for JIT styles
        style_src = [
            "'self'",
            "'unsafe-inline'",  # Required for Tailwind JIT and inline styles
            "https://cdn.jsdelivr.net",
            "https://fonts.googleapis.com",
        ]
        directives.append(f"style-src {' '.join(style_src)}")

        # img-src: Allow data URIs, HTTPS, and blob for photo previews
        img_src = [
            "'self'",
            "data:",
            "blob:",
            "https:",  # Allow all HTTPS images (CDNs, Azure Blob, etc.)
        ]
        directives.append(f"img-src {' '.join(img_src)}")

        # font-src: Google Fonts and CDN
        font_src = [
            "'self'",
            "https://fonts.gstatic.com",
            "https://cdn.jsdelivr.net",
        ]
        directives.append(f"font-src {' '.join(font_src)}")

        # connect-src: API endpoints, analytics, and WebSocket
        connect_src = [
            "'self'",
            # Analytics
            "https://www.google-analytics.com",
            "https://www.googletagmanager.com",
            # Firebase
            "https://identitytoolkit.googleapis.com",
            "https://securetoken.googleapis.com",
            "https://www.googleapis.com",
            # reCAPTCHA
            "https://www.google.com",
            # Geo-IP lookup for phone country detection
            "https://ipapi.co",
            # Azure Blob Storage (media files)
            "https://*.blob.core.windows.net",
            # WebSocket for HTMX (if used)
            "wss:",
        ]
        directives.append(f"connect-src {' '.join(connect_src)}")

        # frame-src: OAuth popups and Firebase reCAPTCHA
        frame_src = [
            "'self'",
            "https://accounts.google.com",
            "https://www.facebook.com",
            "https://login.microsoftonline.com",
            "https://www.google.com",  # reCAPTCHA
        ]
        directives.append(f"frame-src {' '.join(frame_src)}")

        # form-action: Where forms can submit to
        form_action = [
            "'self'",
        ]
        directives.append(f"form-action {' '.join(form_action)}")

        # base-uri: Restrict <base> tag
        directives.append("base-uri 'self'")

        # object-src: Disable plugins
        directives.append("object-src 'none'")

        # frame-ancestors: Prevent clickjacking (supplement to X-Frame-Options)
        directives.append("frame-ancestors 'self'")

        # upgrade-insecure-requests: Upgrade HTTP to HTTPS
        # Only add in enforcement mode (ignored in report-only mode and causes console warnings)
        if not settings.DEBUG and not getattr(settings, 'CSP_REPORT_ONLY', True):
            directives.append("upgrade-insecure-requests")

        # Report violations to our endpoint
        report_uri = getattr(settings, 'CSP_REPORT_URI', None)
        if report_uri:
            directives.append(f"report-uri {report_uri}")

        return "; ".join(directives)

    def _build_permissions_policy(self):
        """
        Build Permissions-Policy header.

        This header restricts which browser features can be used,
        reducing the attack surface for malicious scripts.

        Format: feature=(allowlist), feature=(), ...
        - () = disabled entirely
        - (self) = allowed only on same origin
        - (*) = allowed everywhere (not recommended)
        """
        policies = [
            # Disable dangerous/unused features
            "accelerometer=()",
            "ambient-light-sensor=()",
            "autoplay=()",
            "battery=()",
            "camera=()",              # Not used - disable for privacy
            "display-capture=()",
            "document-domain=()",
            "encrypted-media=()",
            "execution-while-not-rendered=()",
            "execution-while-out-of-viewport=()",
            "fullscreen=(self)",      # Allow fullscreen on same origin
            "gamepad=()",
            "geolocation=()",         # Not used - disable for privacy
            "gyroscope=()",
            "hid=()",
            "identity-credentials-get=()",
            "idle-detection=()",
            "local-fonts=()",
            "magnetometer=()",
            "microphone=()",          # Not used - disable for privacy
            "midi=()",
            "otp-credentials=()",
            "payment=()",
            "picture-in-picture=()",
            "publickey-credentials-create=()",
            "publickey-credentials-get=()",
            "screen-wake-lock=()",
            "serial=()",
            "speaker-selection=()",
            "usb=()",
            "web-share=(self)",       # Allow Web Share API on same origin
            "xr-spatial-tracking=()",
        ]

        return ", ".join(policies)
