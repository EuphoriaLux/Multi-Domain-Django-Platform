# Crush.lu Email Configuration Guide

This guide explains how to configure email sending from the @crush.lu domain for your Azure-deployed Django application.

## Overview

The application now supports **domain-specific email configuration**, allowing different domains (crush.lu, powerup.lu, vinsdelux.com) to send emails from their respective addresses using different SMTP servers.

## Architecture

- **Email Utility Module**: [azureproject/email_utils.py](azureproject/email_utils.py)
  - `send_domain_email()` - Sends emails using domain-specific SMTP config
  - `get_domain_email_config()` - Returns email settings for a domain
  - `get_domain_from_email()` - Gets default from address for a domain

- **Crush.lu Email Helpers**: [crush_lu/email_helpers.py](crush_lu/email_helpers.py)
  - Profile submission confirmations
  - Coach assignment notifications
  - Profile approval/rejection/revision emails
  - Event registration confirmations
  - Event waitlist notifications
  - Event cancellation confirmations
  - Event reminders

## Step 1: Microsoft 365 Email Setup

### 1.1 Create Mailbox in M365 Admin Center

1. Log in to [Microsoft 365 Admin Center](https://admin.microsoft.com)
2. Navigate to **Users** → **Active users**
3. Click **Add a user**
4. Create mailbox: `noreply@crush.lu` (or `info@crush.lu`)
5. Assign a license (Exchange Online required)
6. Note the password (will be changed to app password)

### 1.2 Generate App Password for SMTP

Microsoft 365 requires app passwords for SMTP authentication when MFA is enabled.

1. Go to [My Account Security Info](https://mysignins.microsoft.com/security-info)
2. Sign in as `noreply@crush.lu`
3. Click **Add sign-in method** → **App password**
4. Name it: "Crush.lu Django App"
5. **Copy the generated password** - you'll need this for `CRUSH_EMAIL_HOST_PASSWORD`

### 1.3 Enable SMTP AUTH (if needed)

1. In M365 Admin Center, go to **Users** → **Active users**
2. Select `noreply@crush.lu`
3. Go to **Mail** tab
4. Under **Email apps**, click **Manage email apps**
5. Ensure **Authenticated SMTP** is enabled

### 1.4 Microsoft 365 SMTP Settings

```
SMTP Server: smtp.office365.com
Port: 587 (TLS) or 465 (SSL) - recommended: 587
Authentication: Required
Use TLS: Yes (if port 587)
Use SSL: Yes (if port 465)
```

## Step 2: Azure App Service Configuration

### 2.1 Add Environment Variables

Navigate to your Azure App Service:
1. Go to **Configuration** → **Application settings**
2. Add the following environment variables:

```bash
# Crush.lu Email Configuration
CRUSH_EMAIL_HOST=smtp.office365.com
CRUSH_EMAIL_PORT=587
CRUSH_EMAIL_HOST_USER=noreply@crush.lu
CRUSH_EMAIL_HOST_PASSWORD=<app-password-from-step-1.2>
CRUSH_EMAIL_USE_TLS=True
CRUSH_EMAIL_USE_SSL=False
CRUSH_DEFAULT_FROM_EMAIL=noreply@crush.lu
```

### 2.2 Verify Existing Variables

Ensure these are already set (for other domains):
```bash
# Default/PowerUP Email (existing)
EMAIL_HOST=mail.power-up.lu
EMAIL_PORT=465
EMAIL_HOST_USER=info@power-up.lu
EMAIL_HOST_PASSWORD=<existing-password>
EMAIL_USE_SSL=True
DEFAULT_FROM_EMAIL=info@power-up.lu

# Custom domains configuration
CUSTOM_DOMAINS=powerup.lu,vinsdelux.com,crush.lu,www.powerup.lu,www.vinsdelux.com,www.crush.lu
ALLOWED_HOSTS_ENV=powerup.lu,vinsdelux.com,crush.lu
```

### 2.3 Save and Restart

1. Click **Save** at the top of the Configuration page
2. App Service will automatically restart
3. Verify restart completes successfully

## Step 3: DNS Configuration (Azure DNS)

### 3.1 Add SPF Record

SPF (Sender Policy Framework) authorizes M365 to send emails from your domain.

1. Go to **Azure Portal** → **DNS zones** → `crush.lu`
2. Click **+ Record set**
3. Add TXT record:
   ```
   Name: @
   Type: TXT
   TTL: 3600
   Value: v=spf1 include:spf.protection.outlook.com -all
   ```

### 3.2 Add DMARC Record

DMARC (Domain-based Message Authentication) improves deliverability.

1. Add TXT record:
   ```
   Name: _dmarc
   Type: TXT
   TTL: 3600
   Value: v=DMARC1; p=quarantine; rua=mailto:noreply@crush.lu
   ```

### 3.3 Verify MX Records

Ensure MX records point to Microsoft Exchange (should already be set):
```
Priority: 0
Host: crush.lu
Points to: crush-lu.mail.protection.outlook.com
```

### 3.4 Add DKIM Records (Optional but Recommended)

1. In M365 Admin Center, go to **Settings** → **Domains**
2. Select `crush.lu`
3. Click **Enable DKIM**
4. Copy the two CNAME records provided
5. Add them to Azure DNS:
   ```
   Name: selector1._domainkey
   Type: CNAME
   Value: selector1-crush-lu._domainkey.<tenant>.onmicrosoft.com

   Name: selector2._domainkey
   Type: CNAME
   Value: selector2-crush-lu._domainkey.<tenant>.onmicrosoft.com
   ```

## Step 4: Testing Email Configuration

### 4.1 Test in Django Shell (Azure Cloud Shell)

1. Connect to Azure App Service via SSH or Cloud Shell
2. Activate Django shell:
   ```bash
   python manage.py shell
   ```

3. Test email sending:
   ```python
   from azureproject.email_utils import send_domain_email
   from django.http import HttpRequest

   # Create mock request for crush.lu domain
   request = HttpRequest()
   request.META['HTTP_HOST'] = 'crush.lu'

   # Send test email
   result = send_domain_email(
       subject='Test Email from Crush.lu',
       message='This is a test email to verify SMTP configuration.',
       recipient_list=['your-email@example.com'],  # Your test email
       request=request,
       fail_silently=False
   )

   print(f"Email sent: {result}")  # Should print 1 if successful
   ```

### 4.2 Test via Crush.lu Platform

1. Navigate to https://crush.lu/signup/
2. Create a new test account
3. Complete profile creation
4. Check if you receive:
   - Profile submission confirmation email
5. Have a coach review and approve the profile
6. Check if you receive:
   - Profile approval notification
7. Register for an event
8. Check if you receive:
   - Event registration confirmation

### 4.3 Monitor Email Logs

Check Azure App Service logs for email-related errors:

```bash
az webapp log tail --name <your-app-name> --resource-group <your-resource-group> --filter "email"
```

Or view in Azure Portal:
1. Go to **App Service** → **Log stream**
2. Look for log entries containing "email", "SMTP", or "Failed to send"

## Step 5: Troubleshooting

### Common Issues and Solutions

#### 5.1 SMTP Authentication Failure

**Error**: `535 5.7.3 Authentication unsuccessful`

**Solutions**:
- Verify app password is correct (regenerate if needed)
- Check SMTP AUTH is enabled for the mailbox
- Ensure MFA is configured for the account
- Try using the full email as username: `noreply@crush.lu`

#### 5.2 TLS/SSL Connection Issues

**Error**: `Connection refused` or `SSL handshake failed`

**Solutions**:
- Verify port 587 is open in Azure (should be by default)
- Check `EMAIL_USE_TLS=True` and `EMAIL_USE_SSL=False` for port 587
- Or use port 465 with `EMAIL_USE_SSL=True` and `EMAIL_USE_TLS=False`

#### 5.3 Emails Going to Spam

**Solutions**:
- Verify SPF record is correctly configured
- Enable and configure DKIM
- Ensure DMARC policy is set
- Check sender reputation at [Microsoft SNDS](https://sendersupport.olc.protection.outlook.com/snds/)
- Warm up the domain by sending low volumes initially

#### 5.4 Domain Not Recognized

**Error**: Emails send from wrong domain (powerup.lu instead of crush.lu)

**Solutions**:
- Verify `request.get_host()` returns correct domain
- Check `DomainURLRoutingMiddleware` is working
- Ensure `ALLOWED_HOSTS` includes crush.lu and www.crush.lu
- Test with explicit domain parameter:
  ```python
  send_domain_email(..., domain='crush.lu')
  ```

#### 5.5 Missing Email Templates

**Error**: `TemplateDoesNotExist: crush_lu/emails/...`

**Solutions**:
- Email templates are not yet created (templates are rendered in email helpers but files don't exist)
- For now, emails will send as plain text
- Create templates later in `crush_lu/templates/crush_lu/emails/` directory

## Step 6: Environment Variable Reference

### Crush.lu Specific Variables
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CRUSH_EMAIL_HOST` | No | `smtp.office365.com` | M365 SMTP server |
| `CRUSH_EMAIL_PORT` | No | `587` | SMTP port (587 for TLS, 465 for SSL) |
| `CRUSH_EMAIL_HOST_USER` | **Yes** | - | Email address (e.g., noreply@crush.lu) |
| `CRUSH_EMAIL_HOST_PASSWORD` | **Yes** | - | App password from M365 |
| `CRUSH_EMAIL_USE_TLS` | No | `True` | Use TLS encryption (for port 587) |
| `CRUSH_EMAIL_USE_SSL` | No | `False` | Use SSL encryption (for port 465) |
| `CRUSH_DEFAULT_FROM_EMAIL` | No | `noreply@crush.lu` | Default sender address |

### VinsDelux Variables (Optional)
If you want VinsDelux to use custom email:
```bash
VINSDELUX_EMAIL_HOST=<smtp-server>
VINSDELUX_EMAIL_PORT=<port>
VINSDELUX_EMAIL_HOST_USER=<email>
VINSDELUX_EMAIL_HOST_PASSWORD=<password>
VINSDELUX_EMAIL_USE_TLS=True
VINSDELUX_DEFAULT_FROM_EMAIL=<email>
```

## Step 7: Email Template Creation (Optional)

The email helper functions reference HTML templates that don't exist yet. To create them:

### 7.1 Create Template Directory
```bash
mkdir -p crush_lu/templates/crush_lu/emails
```

### 7.2 Example: Profile Submission Confirmation Template

Create `crush_lu/templates/crush_lu/emails/profile_submission_confirmation.html`:
```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: Arial, sans-serif; line-height: 1.6; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background: linear-gradient(135deg, #9B59B6, #FF6B9D); color: white; padding: 20px; text-align: center; }
        .content { padding: 20px; background: #f9f9f9; }
        .button { background: #9B59B6; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Profile Submitted! 🎉</h1>
        </div>
        <div class="content">
            <p>Hey {{ first_name }},</p>
            <p>Thanks for submitting your Crush.lu profile! Our team is reviewing it and we'll get back to you soon.</p>
            <p><strong>What happens next?</strong></p>
            <ul>
                <li>Our coaches will review your profile within 24-48 hours</li>
                <li>You'll receive an email once your profile is approved</li>
                <li>Then you can start browsing and registering for events!</li>
            </ul>
            <p>We're excited to have you join our community!</p>
            <p>Best,<br>The Crush.lu Team</p>
        </div>
    </div>
</body>
</html>
```

### 7.3 Other Templates to Create

1. `profile_approved.html` - Profile approval notification
2. `profile_revision_request.html` - Revision request feedback
3. `profile_rejected.html` - Profile rejection notification
4. `coach_assignment.html` - Coach notification for new review
5. `event_registration_confirmation.html` - Event registration success
6. `event_waitlist.html` - Waitlist notification
7. `event_cancellation.html` - Cancellation confirmation
8. `event_reminder.html` - Event reminder (for future use)

## Step 8: Monitoring and Maintenance

### 8.1 Set Up Email Alerts

Configure Azure Monitor alerts for email failures:
1. Go to **App Service** → **Alerts**
2. Create alert rule for log patterns containing "Failed to send"
3. Configure action group to notify admins

### 8.2 Regular Checks

- Monitor deliverability rates in M365 Message Trace
- Review bounce rates and adjust sender reputation
- Update DNS records if infrastructure changes
- Rotate app passwords periodically (every 6 months)

### 8.3 Email Sending Limits

Microsoft 365 SMTP has rate limits:
- **10,000 recipients per day** (across all emails)
- **30 messages per minute**
- Adjust sending patterns if hitting limits

## Summary Checklist

- [ ] Create mailbox `noreply@crush.lu` in M365 Admin Center
- [ ] Generate app password for SMTP authentication
- [ ] Enable SMTP AUTH for the mailbox
- [ ] Add Azure App Service environment variables (CRUSH_EMAIL_*)
- [ ] Configure SPF record in Azure DNS
- [ ] Configure DMARC record in Azure DNS
- [ ] Verify MX records point to Microsoft Exchange
- [ ] (Optional) Configure DKIM for better deliverability
- [ ] Test email sending via Django shell
- [ ] Test email sending via Crush.lu platform
- [ ] Monitor logs for email-related errors
- [ ] (Optional) Create HTML email templates
- [ ] (Optional) Set up monitoring alerts

## Additional Resources

- [Microsoft 365 SMTP Settings](https://support.microsoft.com/en-us/office/pop-imap-and-smtp-settings-8361e398-8af4-4e97-b147-6c6c4ac95353)
- [Django Email Documentation](https://docs.djangoproject.com/en/5.1/topics/email/)
- [Azure App Service Configuration](https://learn.microsoft.com/en-us/azure/app-service/configure-common)
- [SPF Record Syntax](https://www.dmarcanalyzer.com/spf/)
- [DMARC Configuration Guide](https://dmarc.org/overview/)

## Support

For issues with:
- **Email configuration**: Check logs in Azure App Service
- **M365 setup**: Contact Microsoft 365 support
- **DNS records**: Verify in Azure DNS zone
- **Code issues**: Review [azureproject/email_utils.py](azureproject/email_utils.py) and [crush_lu/email_helpers.py](crush_lu/email_helpers.py)
