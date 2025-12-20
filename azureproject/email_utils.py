# azureproject/email_utils.py
"""
Domain-specific email configuration utilities.
Supports sending emails from different domains (powerup.lu, crush.lu, vinsdelux.com)
"""
import os
from django.core.mail import EmailMessage


def _normalize_domain(domain):
    """
    Normalize domain by removing www. prefix.
    This allows a single config entry to handle both www and non-www variants.
    """
    if domain.startswith('www.'):
        return domain[4:]
    return domain


# Domain-specific email configurations
# Note: www.* variants are handled by _normalize_domain() in get_domain_email_config()
DOMAIN_EMAIL_CONFIG = {
    'crush.lu': {
        # Microsoft Graph API configuration (Graph API only - SMTP disabled by M365)
        'USE_GRAPH_API': os.getenv('CRUSH_USE_GRAPH_API', 'True').lower() == 'true',
        'GRAPH_TENANT_ID': os.getenv('CRUSH_GRAPH_TENANT_ID'),
        'GRAPH_CLIENT_ID': os.getenv('CRUSH_GRAPH_CLIENT_ID'),
        'GRAPH_CLIENT_SECRET': os.getenv('CRUSH_GRAPH_CLIENT_SECRET'),
        'DEFAULT_FROM_EMAIL': os.getenv('CRUSH_DEFAULT_FROM_EMAIL', 'noreply@crush.lu'),
    },
    'powerup.lu': {
        'EMAIL_HOST': os.getenv('EMAIL_HOST', 'mail.power-up.lu'),
        'EMAIL_PORT': int(os.getenv('EMAIL_PORT', '465')),
        'EMAIL_HOST_USER': os.getenv('EMAIL_HOST_USER'),
        'EMAIL_HOST_PASSWORD': os.getenv('EMAIL_HOST_PASSWORD'),
        'EMAIL_USE_TLS': os.getenv('EMAIL_USE_TLS', 'False').lower() == 'true',
        'EMAIL_USE_SSL': os.getenv('EMAIL_USE_SSL', 'True').lower() == 'true',
        'DEFAULT_FROM_EMAIL': os.getenv('DEFAULT_FROM_EMAIL', os.getenv('EMAIL_HOST_USER')),
    },
    'vinsdelux.com': {
        'EMAIL_HOST': os.getenv('VINSDELUX_EMAIL_HOST', os.getenv('EMAIL_HOST', 'mail.power-up.lu')),
        'EMAIL_PORT': int(os.getenv('VINSDELUX_EMAIL_PORT', os.getenv('EMAIL_PORT', '465'))),
        'EMAIL_HOST_USER': os.getenv('VINSDELUX_EMAIL_HOST_USER', os.getenv('EMAIL_HOST_USER')),
        'EMAIL_HOST_PASSWORD': os.getenv('VINSDELUX_EMAIL_HOST_PASSWORD', os.getenv('EMAIL_HOST_PASSWORD')),
        'EMAIL_USE_TLS': os.getenv('VINSDELUX_EMAIL_USE_TLS', os.getenv('EMAIL_USE_TLS', 'False')).lower() == 'true',
        'EMAIL_USE_SSL': os.getenv('VINSDELUX_EMAIL_USE_SSL', os.getenv('EMAIL_USE_SSL', 'True')).lower() == 'true',
        'DEFAULT_FROM_EMAIL': os.getenv('VINSDELUX_DEFAULT_FROM_EMAIL', os.getenv('DEFAULT_FROM_EMAIL', os.getenv('EMAIL_HOST_USER'))),
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

    # Get config for this domain, or fallback to powerup.lu
    return DOMAIN_EMAIL_CONFIG.get(host, DOMAIN_EMAIL_CONFIG['powerup.lu'])


def send_domain_email(subject, message, recipient_list, request=None, domain=None,
                     html_message=None, from_email=None, fail_silently=False):
    """
    Send email using domain-specific configuration (Graph API, SMTP, or Console in DEBUG).

    Args:
        subject: Email subject
        message: Plain text message body
        recipient_list: List of recipient email addresses
        request: Django request object (optional, for auto-detecting domain)
        domain: Explicit domain string (optional)
        html_message: HTML message body (optional)
        from_email: Override from email (optional)
        fail_silently: Whether to suppress exceptions (default: False)

    Returns:
        int: Number of successfully sent emails
    """
    from django.core.mail import get_connection
    from django.conf import settings
    import logging
    logger = logging.getLogger(__name__)

    # Get domain-specific configuration
    config = get_domain_email_config(request=request, domain=domain)

    # Use configured from_email or domain default
    email_from = from_email or config['DEFAULT_FROM_EMAIL']

    # In DEBUG mode, use console backend to print emails to terminal
    if settings.DEBUG:
        logger.info(f"📧 [DEBUG] Printing email to console (from {email_from})")
        connection = get_connection(
            backend='django.core.mail.backends.console.EmailBackend',
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
        else:
            # Use SMTP backend (for domains that don't have Graph API configured)
            if use_graph:
                logger.warning("Graph API enabled but credentials missing, falling back to SMTP")

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

    # Create email message
    email = EmailMessage(
        subject=subject,
        body=html_message if html_message else message,
        from_email=email_from,
        to=recipient_list,
        connection=connection,
    )

    # Set content type to HTML if html_message provided
    if html_message:
        email.content_subtype = 'html'

    # Send email
    return email.send(fail_silently=fail_silently)


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
