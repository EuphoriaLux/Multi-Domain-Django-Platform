# azureproject/email_utils.py
"""
Domain-specific email configuration utilities.
Supports sending emails from different domains (powerup.lu, crush.lu, vinsdelux.com)
"""
import os
from django.core.mail import EmailMessage


# Domain-specific email configurations
DOMAIN_EMAIL_CONFIG = {
    'crush.lu': {
        # Microsoft Graph API configuration (recommended)
        'USE_GRAPH_API': os.getenv('CRUSH_USE_GRAPH_API', 'True').lower() == 'true',
        'GRAPH_TENANT_ID': os.getenv('CRUSH_GRAPH_TENANT_ID'),
        'GRAPH_CLIENT_ID': os.getenv('CRUSH_GRAPH_CLIENT_ID'),
        'GRAPH_CLIENT_SECRET': os.getenv('CRUSH_GRAPH_CLIENT_SECRET'),
        'DEFAULT_FROM_EMAIL': os.getenv('CRUSH_DEFAULT_FROM_EMAIL', 'noreply@crush.lu'),
        # SMTP fallback configuration (if Graph API not available)
        'EMAIL_HOST': os.getenv('CRUSH_EMAIL_HOST', 'smtp.office365.com'),
        'EMAIL_PORT': int(os.getenv('CRUSH_EMAIL_PORT', '587')),
        'EMAIL_HOST_USER': os.getenv('CRUSH_EMAIL_HOST_USER', 'noreply@crush.lu'),
        'EMAIL_HOST_PASSWORD': os.getenv('CRUSH_EMAIL_HOST_PASSWORD', os.getenv('EMAIL_HOST_PASSWORD', '')),
        'EMAIL_USE_TLS': os.getenv('CRUSH_EMAIL_USE_TLS', 'True').lower() == 'true',
        'EMAIL_USE_SSL': os.getenv('CRUSH_EMAIL_USE_SSL', 'False').lower() == 'true',
    },
    'www.crush.lu': {
        # Microsoft Graph API configuration (recommended)
        'USE_GRAPH_API': os.getenv('CRUSH_USE_GRAPH_API', 'True').lower() == 'true',
        'GRAPH_TENANT_ID': os.getenv('CRUSH_GRAPH_TENANT_ID'),
        'GRAPH_CLIENT_ID': os.getenv('CRUSH_GRAPH_CLIENT_ID'),
        'GRAPH_CLIENT_SECRET': os.getenv('CRUSH_GRAPH_CLIENT_SECRET'),
        'DEFAULT_FROM_EMAIL': os.getenv('CRUSH_DEFAULT_FROM_EMAIL', 'noreply@crush.lu'),
        # SMTP fallback configuration (if Graph API not available)
        'EMAIL_HOST': os.getenv('CRUSH_EMAIL_HOST', 'smtp.office365.com'),
        'EMAIL_PORT': int(os.getenv('CRUSH_EMAIL_PORT', '587')),
        'EMAIL_HOST_USER': os.getenv('CRUSH_EMAIL_HOST_USER', 'noreply@crush.lu'),
        'EMAIL_HOST_PASSWORD': os.getenv('CRUSH_EMAIL_HOST_PASSWORD', os.getenv('EMAIL_HOST_PASSWORD', '')),
        'EMAIL_USE_TLS': os.getenv('CRUSH_EMAIL_USE_TLS', 'True').lower() == 'true',
        'EMAIL_USE_SSL': os.getenv('CRUSH_EMAIL_USE_SSL', 'False').lower() == 'true',
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
    'www.powerup.lu': {
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
    'www.vinsdelux.com': {
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

    # Get config for this domain, or fallback to powerup.lu
    return DOMAIN_EMAIL_CONFIG.get(host, DOMAIN_EMAIL_CONFIG['powerup.lu'])


def send_domain_email(subject, message, recipient_list, request=None, domain=None,
                     html_message=None, from_email=None, fail_silently=False):
    """
    Send email using domain-specific configuration (Graph API or SMTP).

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
    import logging
    logger = logging.getLogger(__name__)

    # Get domain-specific configuration
    config = get_domain_email_config(request=request, domain=domain)

    # Use configured from_email or domain default
    email_from = from_email or config['DEFAULT_FROM_EMAIL']

    # Check if Graph API should be used (for crush.lu)
    use_graph = config.get('USE_GRAPH_API', False)
    has_graph_credentials = all([
        config.get('GRAPH_TENANT_ID'),
        config.get('GRAPH_CLIENT_ID'),
        config.get('GRAPH_CLIENT_SECRET')
    ])

    if use_graph and has_graph_credentials:
        try:
            from azureproject.graph_email_backend import GraphEmailBackend
            logger.info(f"Using Microsoft Graph API to send email from {email_from}")

            connection = GraphEmailBackend(
                fail_silently=fail_silently,
                tenant_id=config['GRAPH_TENANT_ID'],
                client_id=config['GRAPH_CLIENT_ID'],
                client_secret=config['GRAPH_CLIENT_SECRET'],
                from_email=email_from
            )
        except Exception as e:
            logger.warning(f"Failed to initialize Graph API backend, falling back to SMTP: {e}")
            # Fall back to SMTP
            connection = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=config['EMAIL_HOST'],
                port=config['EMAIL_PORT'],
                username=config['EMAIL_HOST_USER'],
                password=config['EMAIL_HOST_PASSWORD'],
                use_tls=config['EMAIL_USE_TLS'],
                use_ssl=config['EMAIL_USE_SSL'],
                fail_silently=fail_silently,
            )
    else:
        # Use SMTP backend
        if use_graph:
            logger.info("Graph API enabled but credentials missing, using SMTP backend")

        connection = get_connection(
            backend='django.core.mail.backends.smtp.EmailBackend',
            host=config['EMAIL_HOST'],
            port=config['EMAIL_PORT'],
            username=config['EMAIL_HOST_USER'],
            password=config['EMAIL_HOST_PASSWORD'],
            use_tls=config['EMAIL_USE_TLS'],
            use_ssl=config['EMAIL_USE_SSL'],
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
