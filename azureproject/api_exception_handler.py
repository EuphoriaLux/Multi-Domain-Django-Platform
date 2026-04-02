"""
Custom DRF exception handler for production API error sanitization.

In production, we want to:
- Log detailed error information for debugging
- Return generic error messages to clients (prevent information disclosure)
- Preserve specific validation errors (they're expected/safe)
"""
import logging
from django.conf import settings
from rest_framework.views import exception_handler
from rest_framework.exceptions import (
    ValidationError,
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    NotFound,
    MethodNotAllowed,
    Throttled,
)

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Custom exception handler that sanitizes error messages in production.

    - ValidationError: Keep detailed messages (client input errors)
    - AuthenticationFailed/NotAuthenticated: Keep messages (expected flow)
    - PermissionDenied/NotFound: Keep messages (common responses)
    - Throttled: Keep messages (includes retry info)
    - Other exceptions: Log details, return generic message in production
    """
    # Get the standard DRF response
    response = exception_handler(exc, context)

    if response is None:
        # Unhandled exception - log and return 500
        logger.error(
            f"[API-ERROR] Unhandled exception: {type(exc).__name__}: {exc}",
            exc_info=True
        )
        return None  # Let Django handle it

    # Safe exceptions - keep original messages
    safe_exceptions = (
        ValidationError,
        AuthenticationFailed,
        NotAuthenticated,
        PermissionDenied,
        NotFound,
        MethodNotAllowed,
        Throttled,
    )

    if isinstance(exc, safe_exceptions):
        return response

    # For other API exceptions in production, sanitize the message
    if not settings.DEBUG:
        # Log the actual error for debugging
        view = context.get('view')
        request = context.get('request')
        logger.error(
            f"[API-ERROR] {type(exc).__name__} in {view.__class__.__name__ if view else 'unknown'}: "
            f"path={request.path if request else 'unknown'}, "
            f"user={request.user if request else 'unknown'}, "
            f"error={exc}",
            exc_info=True
        )

        # Return sanitized response
        response.data = {
            'detail': 'An error occurred processing your request.',
            'error_code': type(exc).__name__
        }

    return response
