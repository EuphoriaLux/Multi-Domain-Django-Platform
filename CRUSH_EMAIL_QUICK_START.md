# Crush.lu Email Quick Start Guide

## TL;DR - Choose Your Approach

### Option 1: Microsoft Graph API (Recommended) ⭐
- **Cost**: FREE
- **Setup Time**: 15 minutes
- **Maintenance**: Low
- **See**: [CRUSH_EMAIL_SETUP_GRAPH_API.md](CRUSH_EMAIL_SETUP_GRAPH_API.md)

### Option 2: SMTP (Traditional)
- **Cost**: $4-6/month (mailbox license)
- **Setup Time**: 10 minutes
- **Maintenance**: Medium (password rotation)
- **See**: [CRUSH_EMAIL_SETUP.md](CRUSH_EMAIL_SETUP.md)

## Recommended: Microsoft Graph API Setup

### 1. Azure AD App Registration (5 min)

```bash
# In Azure Portal:
Azure AD → App registrations → New registration
  Name: Crush.lu Email Service
  → Register
  → Copy: Application (client) ID
  → Copy: Directory (tenant) ID

# Create Secret:
Certificates & secrets → New client secret
  → Copy the secret value immediately

# Grant Permissions:
API permissions → Add permission → Microsoft Graph → Application
  → Select: Mail.Send
  → Grant admin consent ✓
```

### 2. Create FREE Shared Mailbox (2 min)

```bash
# In M365 Admin Center:
Teams & groups → Shared mailboxes → Add
  Name: Crush.lu Notifications
  Email: noreply@crush.lu
  → No license needed!
```

### 3. Azure App Service Variables (3 min)

```bash
CRUSH_USE_GRAPH_API=True
CRUSH_GRAPH_TENANT_ID=<tenant-id-from-step-1>
CRUSH_GRAPH_CLIENT_ID=<client-id-from-step-1>
CRUSH_GRAPH_CLIENT_SECRET=<secret-from-step-1>
CRUSH_DEFAULT_FROM_EMAIL=noreply@crush.lu
```

### 4. DNS Records (5 min)

```bash
# SPF (authorize Microsoft to send)
@ TXT v=spf1 include:spf.protection.outlook.com -all

# DMARC (email authentication)
_dmarc TXT v=DMARC1; p=quarantine; rua=mailto:noreply@crush.lu
```

### 5. Deploy & Test

```bash
# The code is already integrated!
# Just push to Azure and test:

python manage.py shell
>>> from azureproject.email_utils import send_domain_email
>>> send_domain_email(
...     subject='Test from Graph API',
...     message='Testing',
...     html_message='<h1>It works!</h1>',
...     recipient_list=['your-email@example.com'],
...     domain='crush.lu'
... )
1  # Success!
```

## What's Already Implemented

### ✅ Code Files Created

1. **[azureproject/graph_email_backend.py](azureproject/graph_email_backend.py)**
   - Custom Django email backend using Microsoft Graph API
   - Automatic token management
   - Support for HTML emails and attachments

2. **[azureproject/email_utils.py](azureproject/email_utils.py)**
   - Domain detection (crush.lu, powerup.lu, vinsdelux.com)
   - Automatic Graph API vs SMTP selection
   - Fallback mechanism if Graph fails

3. **[crush_lu/email_helpers.py](crush_lu/email_helpers.py)**
   - Profile submission confirmations
   - Coach notifications
   - Event registration emails
   - Approval/rejection notifications

### ✅ Integration Points

Email notifications are already integrated in:

- **Profile Creation**: [crush_lu/views.py:143-159](crush_lu/views.py#L143-L159)
- **Profile Editing**: [crush_lu/views.py:225-240](crush_lu/views.py#L225-L240)
- **Coach Reviews**: [crush_lu/views.py:509-551](crush_lu/views.py#L509-L551)
- **Event Registration**: [crush_lu/views.py:418-427](crush_lu/views.py#L418-L427)
- **Event Cancellation**: [crush_lu/views.py:455-461](crush_lu/views.py#L455-L461)

### ✅ Dependencies Added

Updated [requirements.txt](requirements.txt):
- `msal==1.28.0` - Microsoft Authentication Library
- `requests==2.31.0` - HTTP library (updated)

## Environment Variable Reference

### Required for Graph API
```bash
CRUSH_USE_GRAPH_API=True                    # Enable Graph API
CRUSH_GRAPH_TENANT_ID=xxxxxxxx-xxxx-...     # Azure AD Tenant ID
CRUSH_GRAPH_CLIENT_ID=xxxxxxxx-xxxx-...     # App Registration Client ID
CRUSH_GRAPH_CLIENT_SECRET=xxxxxxxxxx        # Client Secret
CRUSH_DEFAULT_FROM_EMAIL=noreply@crush.lu   # Sender address
```

### Optional SMTP Fallback
```bash
CRUSH_EMAIL_HOST=smtp.office365.com
CRUSH_EMAIL_PORT=587
CRUSH_EMAIL_HOST_USER=noreply@crush.lu
CRUSH_EMAIL_HOST_PASSWORD=<app-password>
CRUSH_EMAIL_USE_TLS=True
```

### Disable Graph API (use SMTP only)
```bash
CRUSH_USE_GRAPH_API=False
# Then provide CRUSH_EMAIL_* variables
```

## How It Works

### Request Flow

```
1. User submits profile on crush.lu
   ↓
2. Django view calls: send_profile_submission_confirmation(user, request)
   ↓
3. email_helpers.py calls: send_domain_email(..., request=request)
   ↓
4. email_utils.py detects domain from request.get_host() = 'crush.lu'
   ↓
5. Checks config: USE_GRAPH_API=True + credentials exist?
   ↓
6a. YES → Uses GraphEmailBackend
   ↓
   - Gets OAuth token from Azure AD
   - Calls Microsoft Graph API: POST /users/{email}/sendMail
   - Email sent from noreply@crush.lu

6b. NO → Falls back to SMTP
   ↓
   - Uses SMTP settings
   - Connects to smtp.office365.com:587
   - Email sent via traditional SMTP
```

### Automatic Fallback

The system automatically falls back to SMTP if:
- `CRUSH_USE_GRAPH_API=False`
- Graph credentials are missing
- Graph API returns an error
- msal package not installed

## Testing Checklist

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (create .env file)
CRUSH_USE_GRAPH_API=True
CRUSH_GRAPH_TENANT_ID=...
CRUSH_GRAPH_CLIENT_ID=...
CRUSH_GRAPH_CLIENT_SECRET=...
CRUSH_DEFAULT_FROM_EMAIL=noreply@crush.lu

# Test in shell
python manage.py shell
>>> from azureproject.email_utils import send_domain_email
>>> send_domain_email(
...     subject='Local Test',
...     message='Testing locally',
...     recipient_list=['your-email@example.com'],
...     domain='crush.lu'
... )
```

### Production Testing
1. Deploy to Azure App Service
2. Create test account: https://crush.lu/signup/
3. Complete profile creation
4. Check email: Should receive "Profile Submitted" email from noreply@crush.lu
5. Have coach approve profile
6. Check email: Should receive "Profile Approved" email
7. Register for event
8. Check email: Should receive "Event Registration" confirmation

## Monitoring

### Azure Portal
```bash
# View Graph API calls
Azure AD → App registrations → Crush.lu Email Service
  → Overview → Check "Usage" metrics

# View sign-in logs (API calls)
Azure AD → Sign-in logs → Filter by application

# View App Service logs
App Service → Log stream
  → Look for "Using Microsoft Graph API to send email"
```

### Django Logs
```python
# In views, check logs for email sending
import logging
logger = logging.getLogger(__name__)

# Successful email:
# INFO: Using Microsoft Graph API to send email from noreply@crush.lu
# INFO: Email sent successfully via Graph API to ['user@example.com']

# Fallback:
# WARNING: Failed to initialize Graph API backend, falling back to SMTP
# INFO: Graph API enabled but credentials missing, using SMTP backend
```

## Troubleshooting

### "Failed to acquire access token"
```bash
# Check:
1. Tenant ID, Client ID, Client Secret are correct
2. Client secret hasn't expired (regenerate if needed)
3. API permission Mail.Send is granted with admin consent
4. Wait 5-10 minutes after granting consent (propagation time)
```

### "Mailbox not found for user noreply@crush.lu"
```bash
# Check:
1. Shared mailbox exists in M365 Admin Center
2. Email address is exactly: noreply@crush.lu
3. Domain crush.lu is verified in M365
4. CRUSH_DEFAULT_FROM_EMAIL matches the mailbox exactly
```

### Emails going to spam
```bash
# Check DNS records:
1. SPF: v=spf1 include:spf.protection.outlook.com -all
2. DMARC: v=DMARC1; p=quarantine; rua=mailto:noreply@crush.lu
3. Enable DKIM in M365 Admin → Domains → crush.lu
4. Verify MX records point to Microsoft Exchange
```

### Graph API not being used (falling back to SMTP)
```bash
# Check logs for reason:
# "Graph API enabled but credentials missing" → Missing env vars
# "Failed to initialize Graph API backend" → Check exception details
# Verify in Azure App Service → Configuration that all CRUSH_GRAPH_* are set
```

## Cost Breakdown

### Graph API (Recommended)
| Item | Cost |
|------|------|
| Azure AD App Registration | FREE |
| Shared Mailbox (50GB) | FREE |
| Graph API calls | FREE |
| **Total** | **$0/month** |

### SMTP Alternative
| Item | Cost |
|------|------|
| Microsoft 365 Business Basic | $6/user/month |
| Or Exchange Online Plan 1 | $4/user/month |
| **Total** | **$4-6/month** |

## Next Steps

After email is working:

1. **Create Email Templates** (Optional)
   - Create HTML templates in `crush_lu/templates/crush_lu/emails/`
   - Better formatting, branding, mobile responsiveness

2. **Set Up Monitoring**
   - Azure Monitor alerts for failed emails
   - Dashboard for email metrics

3. **Schedule Email Campaigns** (Future)
   - Event reminders (1 day before)
   - Re-engagement emails (inactive users)
   - Weekly event digests

4. **Extend to Other Domains**
   - VinsDelux: `VINSDELUX_GRAPH_*` variables
   - PowerUP: Keep existing SMTP or migrate to Graph

## Support

- **Graph API Setup**: [CRUSH_EMAIL_SETUP_GRAPH_API.md](CRUSH_EMAIL_SETUP_GRAPH_API.md)
- **SMTP Setup**: [CRUSH_EMAIL_SETUP.md](CRUSH_EMAIL_SETUP.md)
- **Code Reference**: [azureproject/email_utils.py](azureproject/email_utils.py)
- **Email Helpers**: [crush_lu/email_helpers.py](crush_lu/email_helpers.py)
