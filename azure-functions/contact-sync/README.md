# Contact Sync Function App

Azure Function that synchronizes Crush.lu approved profiles to Microsoft Outlook contacts.

## Overview

This function runs daily at 3:00 AM UTC and triggers the Django management command to sync approved Crush.lu profiles to Outlook contacts via Microsoft Graph API.

## Architecture

```
Azure Function (Timer Trigger)
    ↓ HTTP POST with Bearer token
Django API (/api/admin/sync-contacts/)
    ↓ Calls management command
sync_contacts_to_outlook.py
    ↓ Uses GraphContactsService
Microsoft Graph API
```

## Schedule

- **Cron Expression**: `0 0 3 * * *`
- **Frequency**: Daily at 3:00 AM UTC
- **Timeout**: 10 minutes (configured in host.json)

## Local Development

### Prerequisites

- Azure Functions Core Tools v4
- Python 3.11
- Django development server running locally

### Setup

1. Copy the example settings file:
   ```bash
   cp local.settings.json.example local.settings.json
   ```

2. Edit `local.settings.json`:
   ```json
   {
     "Values": {
       "DJANGO_MANAGEMENT_COMMAND_URL": "http://localhost:8000/api/admin/sync-contacts/",
       "ADMIN_API_KEY": "your-admin-api-key-here",
       "OUTLOOK_CONTACT_SYNC_ENABLED": "true"
     }
   }
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the function locally:
   ```bash
   func start
   ```

### Manual Testing

Trigger the function manually via HTTP:
```bash
curl -X POST http://localhost:7071/admin/functions/contact_sync_trigger
```

## Deployment

### Automated (Recommended)

The function is automatically deployed via GitHub Actions when changes are pushed to this directory.

**Workflow**: `.github/workflows/deploy-contact-sync-function.yml`

**Triggers**:
- Push to `main` branch with changes in `azure-functions/contact-sync/**`
- Manual trigger via GitHub Actions UI

### Manual Deployment

If needed, deploy manually using Azure Functions Core Tools:

```bash
func azure functionapp publish crush-contact-sync
```

## Configuration

### Environment Variables

Set in Azure Portal → Function App → Configuration:

| Variable | Description | Example |
|----------|-------------|---------|
| `DJANGO_MANAGEMENT_COMMAND_URL` | Django API endpoint | `https://.../api/admin/sync-contacts/` |
| `ADMIN_API_KEY` | Bearer token for authentication | `your-secret-key` |
| `OUTLOOK_CONTACT_SYNC_ENABLED` | Enable/disable sync | `true` |
| `FUNCTIONS_WORKER_RUNTIME` | Python runtime | `python` |
| `FUNCTIONS_EXTENSION_VERSION` | Functions version | `~4` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights logging | `InstrumentationKey=...` |

## Monitoring

### View Logs

```bash
az webapp log tail --name crush-contact-sync --resource-group django-app-rg
```

### View Execution History

1. Azure Portal → Function App → Functions → `contact_sync_trigger`
2. Click "Monitor" tab
3. View execution history with timestamps and results

### Application Insights

Query logs in Azure Portal → Application Insights:

```kusto
traces
| where timestamp > ago(1d)
| where cloud_RoleName == "crush-contact-sync"
| order by timestamp desc
```

## Troubleshooting

### Function Not Executing

1. Verify function app is running: Azure Portal → Overview
2. Check Application Insights for errors
3. Verify schedule expression in `function_app.py`

### Authentication Errors

1. Verify `ADMIN_API_KEY` matches Django configuration
2. Test health endpoint (no auth): `/api/admin/sync-contacts/health/`
3. Check Bearer token format in logs

### Deployment Failures

1. Check GitHub Actions workflow logs
2. Verify publish profile secret: `AZURE_FUNCTIONAPP_PUBLISH_PROFILE_CONTACT_SYNC`
3. Ensure Python 3.11 compatibility

## Files

| File | Purpose |
|------|---------|
| `function_app.py` | Main function code with timer trigger |
| `requirements.txt` | Python dependencies |
| `host.json` | Function app configuration (timeout, logging) |
| `.funcignore` | Files to exclude from deployment |
| `local.settings.json.example` | Template for local development |

## Related Documentation

- Full documentation: `CLAUDE.md` → Azure Functions section
- Django API: `crush_lu/api_admin_sync.py`
- Management command: `crush_lu/management/commands/sync_contacts_to_outlook.py`
- Graph service: `crush_lu/services/graph_contacts_service.py`
