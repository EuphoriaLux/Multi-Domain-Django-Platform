"""
Security headers middleware for multi-domain Django application.

CSP (Content Security Policy) is now handled by Django 6.0's native
ContentSecurityPolicyMiddleware — see SECURE_CSP_REPORT_ONLY in settings.py.

This module retains the Permissions-Policy header which Django does not
provide natively.
"""


class PermissionsPolicyMiddleware:
    """
    Middleware to add the Permissions-Policy header.

    Restricts which browser features can be used, reducing the attack
    surface for malicious scripts.

    Format: feature=(allowlist), feature=(), ...
    - () = disabled entirely
    - (self) = allowed only on same origin

    Note: Only use standardized features recognized by Chrome/Firefox.
    Deprecated/non-standard features cause console warnings.
    """

    PERMISSIONS_POLICY = ", ".join(
        [
            "accelerometer=()",
            "autoplay=()",
            "camera=(self)",  # Needed for QR check-in scanner
            "display-capture=()",
            "encrypted-media=()",
            "fullscreen=(self)",  # Allow fullscreen on same origin
            "gamepad=()",
            "geolocation=()",  # Not used - disable for privacy
            "gyroscope=()",
            "hid=()",
            "identity-credentials-get=()",
            "idle-detection=()",
            "local-fonts=()",
            "magnetometer=()",
            "microphone=()",  # Not used - disable for privacy
            "midi=()",
            "otp-credentials=()",
            "payment=()",
            "picture-in-picture=()",
            "publickey-credentials-create=()",
            "publickey-credentials-get=()",
            "screen-wake-lock=()",
            "serial=()",
            "usb=()",
            "web-share=(self)",  # Allow Web Share API on same origin
            "xr-spatial-tracking=()",
        ]
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Skip for admin and health check endpoints
        if not request.path.startswith(("/admin/", "/healthz")):
            response["Permissions-Policy"] = self.PERMISSIONS_POLICY
        return response
