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
            "accelerometer=(self)",  # Required for Crush Cache compass orientation
            "autoplay=(self)",  # Same-origin Crush Cache arrival audio
            "camera=(self)",  # Needed for QR check-in scanner
            "display-capture=()",
            "encrypted-media=()",
            "fullscreen=(self)",  # Allow fullscreen on same origin
            "gamepad=()",
            "geolocation=(self)",  # Required for Crush Cache scavenger hunts
            "gyroscope=(self)",  # Required for Crush Cache compass orientation
            "hid=()",
            "identity-credentials-get=()",
            "idle-detection=()",
            "local-fonts=()",
            "magnetometer=(self)",
            "microphone=()",  # Off except where a view calls allow_microphone()
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

    # In-app video calls need the microphone, on their own pages only.
    PERMISSIONS_POLICY_WITH_MICROPHONE = PERMISSIONS_POLICY.replace(
        "microphone=()", "microphone=(self)"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Skip for admin and health check endpoints
        if not request.path.startswith(("/admin/", "/healthz", "/readyz")):
            if getattr(response, "allows_microphone", False):
                response["Permissions-Policy"] = self.PERMISSIONS_POLICY_WITH_MICROPHONE
            else:
                response["Permissions-Policy"] = self.PERMISSIONS_POLICY
        return response


def allow_microphone(response):
    """Let this one response's page use the microphone (same origin only).

    Every other page keeps ``microphone=()``. Spec:
    ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)
    """
    response.allows_microphone = True
    return response
