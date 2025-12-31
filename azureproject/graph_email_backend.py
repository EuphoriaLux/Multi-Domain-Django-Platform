# azureproject/graph_email_backend.py
"""
Microsoft Graph API email backend for Django.
Sends emails using Microsoft Graph instead of SMTP.
"""
import logging
from django.core.mail.backends.base import BaseEmailBackend
from django.conf import settings

logger = logging.getLogger(__name__)


class GraphEmailBackend(BaseEmailBackend):
    """
    Email backend that uses Microsoft Graph API to send emails.
    More modern and recommended approach than SMTP for Microsoft 365.
    """

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.tenant_id = kwargs.get('tenant_id') or getattr(settings, 'GRAPH_TENANT_ID', None)
        self.client_id = kwargs.get('client_id') or getattr(settings, 'GRAPH_CLIENT_ID', None)
        self.client_secret = kwargs.get('client_secret') or getattr(settings, 'GRAPH_CLIENT_SECRET', None)
        self.from_email = kwargs.get('from_email') or getattr(settings, 'GRAPH_FROM_EMAIL', 'noreply@crush.lu')

        if not all([self.tenant_id, self.client_id, self.client_secret]):
            if not fail_silently:
                raise ValueError("Microsoft Graph credentials not configured. "
                               "Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID, and GRAPH_CLIENT_SECRET.")

    def get_access_token(self):
        """Get access token using client credentials flow (app-only authentication)"""
        try:
            import msal
        except ImportError:
            raise ImportError("msal package is required for Graph API email backend. "
                            "Install with: pip install msal")

        authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        scope = ["https://graph.microsoft.com/.default"]

        # Get Azure region to suppress MSAL region mismatch warning
        azure_region = getattr(settings, 'MSAL_REGION', 'westeurope')
        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=authority,
            client_credential=self.client_secret,
            azure_region=azure_region,
        )

        # Try to get token from cache first
        result = app.acquire_token_silent(scope, account=None)
        if not result:
            # No cached token, acquire new one
            result = app.acquire_token_for_client(scopes=scope)

        if "access_token" in result:
            return result["access_token"]
        else:
            error = result.get("error_description", result.get("error", "Unknown error"))
            logger.error(f"Failed to acquire Graph API access token: {error}")
            raise Exception(f"Failed to acquire access token: {error}")

    def send_messages(self, email_messages):
        """Send one or more EmailMessage objects"""
        if not email_messages:
            return 0

        try:
            token = self.get_access_token()
        except Exception as e:
            logger.error(f"Failed to get access token: {e}")
            if not self.fail_silently:
                raise
            return 0

        sent_count = 0
        for message in email_messages:
            try:
                self._send_message(message, token)
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send email to {message.to}: {e}")
                if not self.fail_silently:
                    raise

        return sent_count

    def _send_message(self, message, token):
        """Send a single EmailMessage using Graph API"""
        try:
            import requests
        except ImportError:
            raise ImportError("requests package is required for Graph API email backend. "
                            "Install with: pip install requests")

        # Prepare recipients
        to_recipients = [{"emailAddress": {"address": addr}} for addr in message.to]
        cc_recipients = [{"emailAddress": {"address": addr}} for addr in message.cc] if message.cc else []
        bcc_recipients = [{"emailAddress": {"address": addr}} for addr in message.bcc] if message.bcc else []

        # Determine content type
        content_type = "HTML" if message.content_subtype == "html" else "Text"

        # Prepare email payload
        email_payload = {
            "message": {
                "subject": message.subject,
                "body": {
                    "contentType": content_type,
                    "content": message.body
                },
                "toRecipients": to_recipients,
            },
            "saveToSentItems": "true"
        }

        if cc_recipients:
            email_payload["message"]["ccRecipients"] = cc_recipients
        if bcc_recipients:
            email_payload["message"]["bccRecipients"] = bcc_recipients

        # Handle attachments if present
        if message.attachments:
            attachments = []
            for attachment in message.attachments:
                # attachment is tuple: (filename, content, mimetype)
                if isinstance(attachment, tuple) and len(attachment) >= 2:
                    filename, content, mimetype = attachment[0], attachment[1], attachment[2] if len(attachment) > 2 else 'application/octet-stream'

                    # Encode content to base64
                    import base64
                    if isinstance(content, str):
                        content = content.encode('utf-8')
                    encoded_content = base64.b64encode(content).decode('utf-8')

                    attachments.append({
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": filename,
                        "contentType": mimetype,
                        "contentBytes": encoded_content
                    })

            if attachments:
                email_payload["message"]["attachments"] = attachments

        # Determine sender (use from_email from message or default)
        from_email = message.from_email or self.from_email

        # Send email via Graph API
        endpoint = f"https://graph.microsoft.com/v1.0/users/{from_email}/sendMail"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        response = requests.post(endpoint, headers=headers, json=email_payload, timeout=30)

        if response.status_code not in [200, 202]:
            error_msg = response.text
            logger.error(f"Graph API error (status {response.status_code}): {error_msg}")
            raise Exception(f"Failed to send email via Graph API: HTTP {response.status_code} - {error_msg}")

        logger.info(f"Email sent successfully via Graph API to {message.to} from {from_email}")
