# azureproject/email_utils.py
"""
Domain-specific email configuration utilities.
Supports sending emails from different domains (powerup.lu, crush.lu, vinsdelux.com)
"""
import html
import logging
import os
import re
from html.parser import HTMLParser

from django.apps import apps
from django.core.mail import EmailMultiAlternatives
from django.db import OperationalError, ProgrammingError
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


class _EmailHTMLStripper(HTMLParser):
    """Remove style/script content while retaining useful structural tags."""

    SKIP_TAGS = {"style", "script"}

    def __init__(self):
        super().__init__()
        self._result = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1
        elif not self._skip_depth:
            self._result.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif not self._skip_depth:
            self._result.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._skip_depth:
            self._result.append(data)

    def get_output(self):
        return "".join(self._result)


def html_to_plain_text(html_content):
    """Convert an HTML email into a readable plain-text alternative."""
    parser = _EmailHTMLStripper()
    parser.feed(html_content or "")
    text = parser.get_output()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:div|tr|li)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "  • ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"<h[1-6][^>]*>(.*?)</h[1-6]>",
        r"\n\n\1\n",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>',
        r"\2\n\1",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = html.unescape(strip_tags(text))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _normalize_domain(domain):
    """
    Normalize domain by removing www. and test. prefixes.
    This allows a single config entry to handle www, test, and non-prefixed variants.
    Examples:
        www.crush.lu -> crush.lu
        test.crush.lu -> crush.lu
        staging.crush.lu -> crush.lu
    """
    # Strip common prefixes
    prefixes = ['www.', 'test.', 'staging.', 'dev.']
    for prefix in prefixes:
        if domain.startswith(prefix):
            return domain[len(prefix):]
    return domain


def _get_domain_email_configs():
    """
    Get domain-specific email configurations.
    This is a function (not a constant) to ensure environment variables are read
    at runtime after dotenv has loaded the .env file.
    """
    return {
        'crush.lu': {
            # Microsoft Graph API configuration (Graph API only - SMTP disabled by M365)
            'USE_GRAPH_API': True,
            'GRAPH_TENANT_ID': os.getenv('GRAPH_TENANT_ID'),
            'GRAPH_CLIENT_ID': os.getenv('GRAPH_CLIENT_ID'),
            'GRAPH_CLIENT_SECRET': os.getenv('GRAPH_CLIENT_SECRET'),
            'DEFAULT_FROM_EMAIL': os.getenv('CRUSH_DEFAULT_FROM_EMAIL', 'noreply@crush.lu'),
            'REPLY_TO_EMAIL': os.getenv('CRUSH_REPLY_TO_EMAIL', 'support@crush.lu'),
        },
        'powerup.lu': {
            'USE_GRAPH_API': True,
            'GRAPH_TENANT_ID': os.getenv('GRAPH_TENANT_ID'),
            'GRAPH_CLIENT_ID': os.getenv('GRAPH_CLIENT_ID'),
            'GRAPH_CLIENT_SECRET': os.getenv('GRAPH_CLIENT_SECRET'),
            'DEFAULT_FROM_EMAIL': os.getenv('POWERUP_DEFAULT_FROM_EMAIL', 'info@powerup.lu'),
        },
        'vinsdelux.com': {
            'USE_GRAPH_API': True,
            'GRAPH_TENANT_ID': os.getenv('GRAPH_TENANT_ID'),
            'GRAPH_CLIENT_ID': os.getenv('GRAPH_CLIENT_ID'),
            'GRAPH_CLIENT_SECRET': os.getenv('GRAPH_CLIENT_SECRET'),
            'DEFAULT_FROM_EMAIL': os.getenv('VINSDELUX_DEFAULT_FROM_EMAIL', 'info@vinsdelux.com'),
        },
        'arborist.lu': {
            'USE_GRAPH_API': True,
            'GRAPH_TENANT_ID': os.getenv('GRAPH_TENANT_ID'),
            'GRAPH_CLIENT_ID': os.getenv('GRAPH_CLIENT_ID'),
            'GRAPH_CLIENT_SECRET': os.getenv('GRAPH_CLIENT_SECRET'),
            'DEFAULT_FROM_EMAIL': 'tom@arborist.lu',
        },
    }


def get_domain_email_config(request=None, domain=None):
    """
    Get email configuration based on request domain or explicit domain parameter.

    Args:
        request: Django request object (optional)
        domain: Explicit domain string (optional)

    Returns:
        dict: Email configuration for the domain
    """
    if request:
        host = request.get_host().split(':')[0].lower()
    elif domain:
        host = domain.lower()
    else:
        # Default to powerup.lu if no request or domain provided
        host = 'powerup.lu'

    # Normalize domain (strip www.) to use single config for both variants
    host = _normalize_domain(host)

    # Get configs at runtime (ensures .env is loaded)
    configs = _get_domain_email_configs()

    # Get config for this domain, or fallback to powerup.lu
    return configs.get(host, configs['powerup.lu'])


def _is_test_environment():
    """
    Detect if running in a test environment.

    Checks:
    1. Django's EMAIL_BACKEND is set to locmem (in-memory) backend
    2. 'pytest' or 'test' in sys.argv (running via test runner)
    """
    import sys
    from django.conf import settings

    # Check if email backend is the in-memory test backend
    if settings.EMAIL_BACKEND == 'django.core.mail.backends.locmem.EmailBackend':
        return True

    # Check if running via pytest or Django's test command
    if any('pytest' in arg or 'test' in arg for arg in sys.argv):
        return True

    return False


def _without_suppressed_crush_addresses(addresses, email_from):
    """Filter active hard-bounce suppressions without coupling other domains."""
    addresses = list(addresses or [])
    if not email_from.lower().endswith("@crush.lu") or not addresses:
        return addresses

    try:
        suppression_model = apps.get_model("crush_lu", "EmailSuppression")
        suppressed = set(
            suppression_model.objects.filter(
                email__in=[address.strip().lower() for address in addresses],
                is_active=True,
            ).values_list("email", flat=True)
        )
    except (LookupError, OperationalError, ProgrammingError):
        # Deploys can briefly run before the new table exists. Failing open avoids
        # turning a deliverability safeguard into a platform-wide email outage.
        logger.warning("Email suppression table unavailable; sending without filtering")
        return addresses

    filtered = [
        address for address in addresses if address.strip().lower() not in suppressed
    ]
    if len(filtered) != len(addresses):
        logger.info(
            "Skipped %d actively suppressed email recipient(s)",
            len(addresses) - len(filtered),
        )
    return filtered


def send_domain_email(subject, message, recipient_list, request=None, domain=None,
                     html_message=None, from_email=None, cc=None, bcc=None,
                     reply_to=None, fail_silently=False, attachments=None):
    """
    Send email using domain-specific configuration (Graph API, SMTP, or Console in DEBUG).

    In test environments, uses Django's in-memory backend to avoid sending real emails.

    Args:
        subject: Email subject
        message: Plain text message body
        recipient_list: List of recipient email addresses
        request: Django request object (optional, for auto-detecting domain)
        domain: Explicit domain string (optional)
        html_message: HTML message body (optional)
        from_email: Override from email (optional)
        cc: List of CC email addresses (optional)
        bcc: List of BCC email addresses (optional)
        reply_to: Reply address or iterable of reply addresses (optional)
        fail_silently: Whether to suppress exceptions (default: False)
        attachments: Optional iterable of (filename, content, mimetype) tuples
            to attach to the message (e.g. a calendar .ics file).

    Returns:
        int: Number of successfully sent emails
    """
    from django.core.mail import get_connection
    from django.conf import settings

    # Get domain-specific configuration
    config = get_domain_email_config(request=request, domain=domain)

    # Use configured from_email or domain default
    email_from = from_email or config['DEFAULT_FROM_EMAIL']
    recipient_list = _without_suppressed_crush_addresses(recipient_list, email_from)
    cc = _without_suppressed_crush_addresses(cc, email_from)
    bcc = _without_suppressed_crush_addresses(bcc, email_from)
    if not recipient_list:
        return 0

    configured_reply_to = reply_to or config.get('REPLY_TO_EMAIL')
    if isinstance(configured_reply_to, str):
        configured_reply_to = [configured_reply_to]
    configured_reply_to = list(configured_reply_to or [])

    # In TEST mode, use in-memory backend (no real emails sent)
    if _is_test_environment():
        logger.info(f"📧 [TEST] Using in-memory email backend (from {email_from})")
        connection = get_connection(
            backend='django.core.mail.backends.locmem.EmailBackend',
            fail_silently=fail_silently,
        )
    # In STAGING mode, use console backend (prints to Azure logs, no real emails)
    elif os.getenv('STAGING_MODE', '').lower() in ('true', '1', 'yes'):
        logger.info(f"📧 [STAGING] Using console email backend - no real emails sent (from {email_from})")
        logger.info(f"📧 Recipients: {', '.join(recipient_list)}")
        logger.info(f"📧 Subject: {subject}")
        connection = get_connection(
            backend='django.core.mail.backends.console.EmailBackend',
            fail_silently=fail_silently,
        )
    # In DEBUG mode, use file backend to save emails (avoids Windows console encoding issues)
    elif settings.DEBUG:
        email_folder = os.path.join(settings.BASE_DIR, 'sent_emails')
        os.makedirs(email_folder, exist_ok=True)
        logger.info(f"📧 [DEBUG] Saving email to {email_folder} (from {email_from})")
        connection = get_connection(
            backend='django.core.mail.backends.filebased.EmailBackend',
            file_path=email_folder,
            fail_silently=fail_silently,
        )
    else:
        # PRODUCTION: Check if Graph API should be used (for crush.lu)
        use_graph = config.get('USE_GRAPH_API', False)
        has_graph_credentials = all([
            config.get('GRAPH_TENANT_ID'),
            config.get('GRAPH_CLIENT_ID'),
            config.get('GRAPH_CLIENT_SECRET')
        ])

        if use_graph and has_graph_credentials:
            # Use Microsoft Graph API
            from azureproject.graph_email_backend import GraphEmailBackend
            logger.info(f"Using Microsoft Graph API to send email from {email_from}")

            connection = GraphEmailBackend(
                fail_silently=fail_silently,
                tenant_id=config['GRAPH_TENANT_ID'],
                client_id=config['GRAPH_CLIENT_ID'],
                client_secret=config['GRAPH_CLIENT_SECRET'],
                from_email=email_from
            )
        elif use_graph:
            logger.error("Graph API enabled but credentials missing; refusing SMTP fallback")
            raise ValueError("Graph API credentials are required for this domain.")
        else:
            # Use SMTP backend (for domains that don't have Graph API configured)

            # Get SMTP settings with defaults to avoid KeyError
            connection = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=config.get('EMAIL_HOST', 'smtp.office365.com'),
                port=config.get('EMAIL_PORT', 587),
                username=config.get('EMAIL_HOST_USER', ''),
                password=config.get('EMAIL_HOST_PASSWORD', ''),
                use_tls=config.get('EMAIL_USE_TLS', True),
                use_ssl=config.get('EMAIL_USE_SSL', False),
                fail_silently=fail_silently,
            )

    # Always retain a readable plain-text body, with HTML as an alternative.
    # Re-derive plain text centrally because older call sites used strip_tags(),
    # which left CSS and collapsed important line breaks.
    plain_message = html_to_plain_text(html_message) if html_message else message
    email = EmailMultiAlternatives(
        subject=subject,
        body=plain_message,
        from_email=email_from,
        to=recipient_list,
        cc=cc or [],
        bcc=bcc or [],
        reply_to=configured_reply_to,
        connection=connection,
    )

    # Ensure UTF-8 encoding for all content
    email.encoding = 'utf-8'

    if html_message:
        email.attach_alternative(html_message, 'text/html')

    # Attach any extra files (e.g. .ics calendar invites)
    if attachments:
        for filename, content, mimetype in attachments:
            email.attach(filename, content, mimetype)

    # Send email.
    #
    # ``fail_silently`` is deliberately NOT passed here. Every branch above builds
    # the connection with ``get_connection(..., fail_silently=fail_silently)``, and
    # Django 6.1 raises
    #     TypeError: fail_silently cannot be used with a connection.
    # when it is also passed to send() alongside an explicit connection. Since this
    # function always sets one, passing it here broke *every* send path on 6.1.
    #
    # This is not a behaviour change on 6.0 either: EmailMessage.send() forwards the
    # argument to self.get_connection(), which returns the connection it was already
    # given and ignores it. The connection has carried the flag all along.
    return email.send()


def get_domain_from_email(request=None, domain=None):
    """
    Get the default from email address for a domain.

    Args:
        request: Django request object (optional)
        domain: Explicit domain string (optional)

    Returns:
        str: Default from email address for the domain
    """
    config = get_domain_email_config(request=request, domain=domain)
    return config['DEFAULT_FROM_EMAIL']
