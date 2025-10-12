# Crush.lu Email Configuration - Microsoft Graph API (Recommended)

This guide shows how to send emails from @crush.lu using **Microsoft Graph API** instead of SMTP. This is the modern, Microsoft-recommended approach.

## Why Microsoft Graph API vs SMTP?

### Advantages of Graph API:
- ✅ **No user account needed** - Uses app-only authentication
- ✅ **Better security** - Uses OAuth 2.0 client credentials flow
- ✅ **Higher rate limits** - No 10,000/day SMTP limit
- ✅ **Better monitoring** - Detailed analytics in Azure AD
- ✅ **Modern approach** - Microsoft's recommended method
- ✅ **No password management** - Uses certificates or client secrets

### SMTP Approach:
- ❌ Requires dedicated mailbox with license
- ❌ App password management
- ❌ Lower rate limits
- ❌ Legacy authentication method

## Architecture Overview

```
Django App (crush.lu)
    ↓
Microsoft Graph API Client
    ↓
Azure AD App Registration
    ↓
Microsoft 365 Mailbox (sends on behalf of)
    ↓
Email sent from noreply@crush.lu
```

## Step 1: Azure AD App Registration

### 1.1 Create App Registration

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to **Azure Active Directory** → **App registrations**
3. Click **+ New registration**
4. Configure:
   ```
   Name: Crush.lu Email Service
   Supported account types: Accounts in this organizational directory only
   Redirect URI: (leave blank - not needed for app-only auth)
   ```
5. Click **Register**
6. **Copy and save**:
   - **Application (client) ID**
   - **Directory (tenant) ID**

### 1.2 Create Client Secret

1. In your app registration, go to **Certificates & secrets**
2. Click **+ New client secret**
3. Configure:
   ```
   Description: Crush.lu Django App Secret
   Expires: 24 months (or custom)
   ```
4. Click **Add**
5. **⚠️ IMMEDIATELY COPY THE VALUE** - it won't be shown again!
6. Save this as `CRUSH_GRAPH_CLIENT_SECRET`

### 1.3 Grant API Permissions

1. Go to **API permissions**
2. Click **+ Add a permission**
3. Select **Microsoft Graph** → **Application permissions** (NOT Delegated)
4. Add these permissions:
   - `Mail.Send` - Send mail as any user
5. Click **Add permissions**
6. Click **✓ Grant admin consent for [Your Organization]**
7. Confirm the consent

### 1.4 Verify Configuration

Your app should now have:
- ✅ Application (client) ID
- ✅ Directory (tenant) ID
- ✅ Client secret
- ✅ `Mail.Send` permission with admin consent granted

## Step 2: Create Shared Mailbox (No License Required!)

Instead of creating a user (requires license), create a **shared mailbox** (FREE):

### 2.1 Create Shared Mailbox

1. Go to [Microsoft 365 Admin Center](https://admin.microsoft.com)
2. Navigate to **Teams & groups** → **Shared mailboxes**
3. Click **+ Add a shared mailbox**
4. Configure:
   ```
   Name: Crush.lu Notifications
   Email address: noreply@crush.lu
   ```
5. Click **Add**
6. **No license needed!** Shared mailboxes are free (up to 50GB)

### 2.2 Configure Send-As Permissions (Optional)

If you want the app to send as specific mailbox:
1. Select the shared mailbox `noreply@crush.lu`
2. Go to **Settings** → **Permissions**
3. Under **Send as**, add your app's service principal (optional)

## Step 3: Install Python Dependencies

### 3.1 Add Required Packages

Add to `requirements.txt`:
```
msal==1.28.0
msgraph-core==1.0.0
requests==2.31.0
```

### 3.2 Install Packages

```bash
pip install msal msgraph-core requests
```

Or in your Azure App Service:
```bash
# These will be installed automatically on deployment from requirements.txt
```

## Step 4: Update Django Code

### 4.1 Create Graph API Email Backend

Create `azureproject/graph_email_backend.py`:

```python
# azureproject/graph_email_backend.py
"""
Microsoft Graph API email backend for Django.
Sends emails using Microsoft Graph instead of SMTP.
"""
import logging
from django.core.mail.backends.base import BaseEmailBackend
from django.conf import settings
import msal
import requests

logger = logging.getLogger(__name__)


class GraphEmailBackend(BaseEmailBackend):
    """
    Email backend that uses Microsoft Graph API to send emails.
    """

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.tenant_id = kwargs.get('tenant_id') or getattr(settings, 'GRAPH_TENANT_ID', None)
        self.client_id = kwargs.get('client_id') or getattr(settings, 'GRAPH_CLIENT_ID', None)
        self.client_secret = kwargs.get('client_secret') or getattr(settings, 'GRAPH_CLIENT_SECRET', None)
        self.from_email = kwargs.get('from_email') or getattr(settings, 'GRAPH_FROM_EMAIL', 'noreply@crush.lu')

        if not all([self.tenant_id, self.client_id, self.client_secret]):
            if not fail_silently:
                raise ValueError("Microsoft Graph credentials not configured")

    def get_access_token(self):
        """Get access token using client credentials flow"""
        authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        scope = ["https://graph.microsoft.com/.default"]

        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=authority,
            client_credential=self.client_secret,
        )

        result = app.acquire_token_silent(scope, account=None)
        if not result:
            result = app.acquire_token_for_client(scopes=scope)

        if "access_token" in result:
            return result["access_token"]
        else:
            error = result.get("error_description", "Unknown error")
            logger.error(f"Failed to acquire token: {error}")
            raise Exception(f"Failed to acquire access token: {error}")

    def send_messages(self, email_messages):
        """Send one or more EmailMessage objects"""
        if not email_messages:
            return 0

        token = self.get_access_token()
        sent_count = 0

        for message in email_messages:
            try:
                self._send_message(message, token)
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send email: {e}")
                if not self.fail_silently:
                    raise

        return sent_count

    def _send_message(self, message, token):
        """Send a single EmailMessage using Graph API"""
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

        # Determine sender
        from_email = message.from_email or self.from_email

        # Send email
        endpoint = f"https://graph.microsoft.com/v1.0/users/{from_email}/sendMail"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        response = requests.post(endpoint, headers=headers, json=email_payload)

        if response.status_code not in [200, 202]:
            error_msg = response.text
            logger.error(f"Graph API error: {error_msg}")
            raise Exception(f"Failed to send email via Graph API: {error_msg}")

        logger.info(f"Email sent successfully via Graph API to {message.to}")
```

### 4.2 Update Email Utils for Graph API

Update `azureproject/email_utils.py` to add Graph API support:

```python
# Add to DOMAIN_EMAIL_CONFIG for crush.lu:
'crush.lu': {
    'USE_GRAPH_API': os.getenv('CRUSH_USE_GRAPH_API', 'True').lower() == 'true',
    'GRAPH_TENANT_ID': os.getenv('CRUSH_GRAPH_TENANT_ID'),
    'GRAPH_CLIENT_ID': os.getenv('CRUSH_GRAPH_CLIENT_ID'),
    'GRAPH_CLIENT_SECRET': os.getenv('CRUSH_GRAPH_CLIENT_SECRET'),
    'DEFAULT_FROM_EMAIL': os.getenv('CRUSH_DEFAULT_FROM_EMAIL', 'noreply@crush.lu'),
    # Fallback to SMTP if Graph not configured
    'EMAIL_HOST': os.getenv('CRUSH_EMAIL_HOST', 'smtp.office365.com'),
    'EMAIL_PORT': int(os.getenv('CRUSH_EMAIL_PORT', '587')),
    'EMAIL_HOST_USER': os.getenv('CRUSH_EMAIL_HOST_USER', 'noreply@crush.lu'),
    'EMAIL_HOST_PASSWORD': os.getenv('CRUSH_EMAIL_HOST_PASSWORD', ''),
    'EMAIL_USE_TLS': os.getenv('CRUSH_EMAIL_USE_TLS', 'True').lower() == 'true',
    'EMAIL_USE_SSL': os.getenv('CRUSH_EMAIL_USE_SSL', 'False').lower() == 'true',
},
```

Update `send_domain_email()` function:

```python
def send_domain_email(subject, message, recipient_list, request=None, domain=None,
                     html_message=None, from_email=None, fail_silently=False):
    """Send email using domain-specific configuration (Graph API or SMTP)"""
    from django.core.mail import get_connection, EmailMessage

    config = get_domain_email_config(request=request, domain=domain)

    # Check if Graph API should be used
    if config.get('USE_GRAPH_API') and config.get('GRAPH_CLIENT_ID'):
        from azureproject.graph_email_backend import GraphEmailBackend

        connection = GraphEmailBackend(
            fail_silently=fail_silently,
            tenant_id=config['GRAPH_TENANT_ID'],
            client_id=config['GRAPH_CLIENT_ID'],
            client_secret=config['GRAPH_CLIENT_SECRET'],
            from_email=from_email or config['DEFAULT_FROM_EMAIL']
        )
    else:
        # Fallback to SMTP
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

    email_from = from_email or config['DEFAULT_FROM_EMAIL']
    email = EmailMessage(
        subject=subject,
        body=html_message if html_message else message,
        from_email=email_from,
        to=recipient_list,
        connection=connection,
    )

    if html_message:
        email.content_subtype = 'html'

    return email.send(fail_silently=fail_silently)
```

## Step 5: Azure App Service Configuration

### 5.1 Add Environment Variables

In Azure App Service → Configuration → Application settings:

```bash
# Microsoft Graph API Configuration (Recommended)
CRUSH_USE_GRAPH_API=True
CRUSH_GRAPH_TENANT_ID=<your-tenant-id-from-step-1.1>
CRUSH_GRAPH_CLIENT_ID=<your-client-id-from-step-1.1>
CRUSH_GRAPH_CLIENT_SECRET=<your-client-secret-from-step-1.2>
CRUSH_DEFAULT_FROM_EMAIL=noreply@crush.lu

# SMTP Fallback (Optional - if Graph API fails)
CRUSH_EMAIL_HOST=smtp.office365.com
CRUSH_EMAIL_PORT=587
CRUSH_EMAIL_HOST_USER=noreply@crush.lu
CRUSH_EMAIL_HOST_PASSWORD=<optional-app-password>
CRUSH_EMAIL_USE_TLS=True
```

### 5.2 Update requirements.txt

Ensure these are in your `requirements.txt`:
```
msal==1.28.0
msgraph-core==1.0.0
requests==2.31.0
```

### 5.3 Save and Restart

1. Click **Save**
2. App Service will restart automatically

## Step 6: DNS Configuration (Same as SMTP)

You still need proper DNS records for email authentication:

### 6.1 SPF Record
```
Name: @
Type: TXT
Value: v=spf1 include:spf.protection.outlook.com -all
```

### 6.2 DMARC Record
```
Name: _dmarc
Type: TXT
Value: v=DMARC1; p=quarantine; rua=mailto:noreply@crush.lu
```

### 6.3 MX Records
Should point to Microsoft Exchange (already configured in M365)

### 6.4 DKIM (Recommended)
Enable in M365 Admin Center → Settings → Domains → crush.lu → Enable DKIM

## Step 7: Testing

### 7.1 Test in Django Shell

```python
from azureproject.email_utils import send_domain_email
from django.http import HttpRequest

request = HttpRequest()
request.META['HTTP_HOST'] = 'crush.lu'

result = send_domain_email(
    subject='Test Email via Graph API',
    message='This is a test email sent using Microsoft Graph API.',
    html_message='<h1>Test Email</h1><p>This is sent via <strong>Microsoft Graph API</strong>.</p>',
    recipient_list=['your-email@example.com'],
    request=request,
    fail_silently=False
)

print(f"Email sent: {result}")
```

### 7.2 Test via Crush.lu Platform

1. Go to https://crush.lu/signup/
2. Create test account and profile
3. Verify emails are received from noreply@crush.lu

### 7.3 Monitor in Azure AD

1. Go to **Azure AD** → **Enterprise applications**
2. Find your app "Crush.lu Email Service"
3. Check **Sign-in logs** for API calls
4. Monitor for any errors

## Step 8: Troubleshooting

### Common Issues

#### 8.1 "Insufficient privileges to complete the operation"

**Cause**: API permissions not granted or admin consent not given

**Solution**:
- Go to App registration → API permissions
- Ensure `Mail.Send` is listed under Application permissions
- Click "Grant admin consent" again
- Wait 5-10 minutes for propagation

#### 8.2 "Mailbox not found"

**Cause**: Shared mailbox doesn't exist or wrong email address

**Solution**:
- Verify shared mailbox `noreply@crush.lu` exists in M365 Admin
- Check `CRUSH_DEFAULT_FROM_EMAIL` matches exactly
- Ensure domain crush.lu is verified in M365

#### 8.3 "Invalid client secret"

**Cause**: Client secret expired or incorrect

**Solution**:
- Generate new client secret in App registration
- Update `CRUSH_GRAPH_CLIENT_SECRET` in Azure App Service
- Restart app service

#### 8.4 Graph API calls failing, falling back to SMTP

**Cause**: Missing or incorrect credentials

**Solution**:
- Verify all three values are set: tenant_id, client_id, client_secret
- Check environment variables in Azure App Service
- Review application logs for specific error messages

## Comparison: Graph API vs SMTP

| Feature | Graph API | SMTP |
|---------|-----------|------|
| **Authentication** | OAuth 2.0 (app-only) | Username/password |
| **Mailbox Required** | Shared mailbox (FREE) | User mailbox (license required) |
| **Rate Limits** | High (10,000+ per 10 min) | 10,000 per day |
| **Security** | Client secret/certificate | App password |
| **Monitoring** | Azure AD logs | Limited |
| **Microsoft Recommendation** | ✅ Recommended | ⚠️ Legacy |
| **Setup Complexity** | Medium | Low |
| **Cost** | FREE | ~$4-6/month for user license |

## Cost Comparison

### Graph API Approach:
- **Shared Mailbox**: FREE (up to 50GB)
- **Azure AD App**: FREE
- **API Calls**: FREE (included)
- **Total**: **$0/month**

### SMTP Approach:
- **User Mailbox**: $6/month (Microsoft 365 Business Basic)
- **Or Exchange Online Plan 1**: $4/month
- **Total**: **$4-6/month**

## Summary Checklist

- [ ] Create Azure AD App Registration
- [ ] Copy Application (client) ID and Directory (tenant) ID
- [ ] Create and save client secret
- [ ] Grant `Mail.Send` API permission with admin consent
- [ ] Create shared mailbox `noreply@crush.lu` (FREE)
- [ ] Add to `requirements.txt`: msal, msgraph-core, requests
- [ ] Create `azureproject/graph_email_backend.py`
- [ ] Update `azureproject/email_utils.py` with Graph API support
- [ ] Add Azure App Service environment variables
- [ ] Configure DNS records (SPF, DMARC, DKIM)
- [ ] Test email sending via Django shell
- [ ] Test via Crush.lu platform
- [ ] Monitor Azure AD sign-in logs

## Migration Path

If you've already set up SMTP, you can migrate gradually:

1. **Phase 1**: Set up Graph API alongside SMTP
   - Keep `CRUSH_USE_GRAPH_API=False` initially
   - Configure all Graph variables
   - Test in development

2. **Phase 2**: Enable Graph API in production
   - Set `CRUSH_USE_GRAPH_API=True`
   - Keep SMTP variables as fallback
   - Monitor for 1 week

3. **Phase 3**: Remove SMTP fallback
   - If Graph API stable, remove SMTP variables
   - Delete user mailbox if created (save license cost)

## Additional Resources

- [Microsoft Graph Mail API](https://learn.microsoft.com/en-us/graph/api/user-sendmail)
- [MSAL Python Documentation](https://msal-python.readthedocs.io/)
- [Azure AD App Registration](https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app)
- [Shared Mailboxes](https://learn.microsoft.com/en-us/microsoft-365/admin/email/create-a-shared-mailbox)












from azureproject.email_utils import send_domain_email

send_domain_email(
    subject='Real Test from Crush.lu',
    message='If you get this, it works!',
    html_message='<h1>Success!</h1>',
    recipient_list=['tom.scheuer1993@gmail.com'],  # YOUR EMAIL!
    domain='crush.lu',
    fail_silently=False  # Will show errors
)

# Check logs for:
# "Using Microsoft Graph API to send email" 
# OR
# "Graph API enabled but credentials missing, using SMTP backend"