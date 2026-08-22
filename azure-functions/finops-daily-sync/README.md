# FinOps Daily Sync - Azure Function

Automated daily synchronization for the PowerUP FinOps Hub: Azure Cost
Management data plus an independent public Azure retail-price catalogue snapshot.

## Overview

This Azure Function triggers the Django webhook endpoint to refresh cost data from Azure Cost Management exports. It runs daily at 3:00 AM UTC to ensure the dashboard displays up-to-date cost information.

## Architecture

```
Azure Function (Timer Trigger)
    ↓ (HTTP POST with auth token)
Django Webhook (/finops/api/sync/)
    ↓
Management Command (sync_daily_costs)
    ↓
Cost Data Import + Aggregation
```

The same Function App also runs `retail_price_daily_sync` at 04:00 UTC. It
archives every paginated Azure Retail Prices response and appends normalized
EUR observations for the default European regions. It does not access customer
Azure subscriptions.

**It syncs one region per HTTP request, and this is load-bearing.** The full
European catalogue is roughly 200,000 rows across 208 pages; no single App
Service request can carry that, because the Azure load balancer cuts a request
off at about 240s on Linux regardless of any client-side timeout. The original
whole-catalogue POST therefore failed *every* night. The timer now:

1. `GET`s `/finops/api/sync/retail-prices/regions/` for the region list **and
   the snapshot date**, so the Function App and Django cannot drift out of step
   about which regions are captured or which day they belong to;
2. `POST`s `{"region": "<code>", "snapshot_date": "<YYYY-MM-DD>"}` to
   `/finops/api/sync/retail-prices/` once per region — each is 6-16 pages and
   finishes well inside the ceiling. The date is pinned for the whole
   invocation because every region is a separate request: left to itself each
   call would re-evaluate "today", and a walk straddling local midnight (which
   a past-due timer can do) would file its regions under two dates and complete
   neither;
3. attempts every region before failing, so one bad region costs one region
   rather than the whole day, and stops early against a wall-clock budget so a
   slow night ends with a logged summary instead of a silent kill.

A region-less POST is rejected with `400` rather than falling back to the whole
catalogue — the old payload shape must fail loudly, not quietly reintroduce the
timeout.

## Configuration

### Azure Function Settings

Set these environment variables in Azure Portal:

| Variable | Value | Description |
|----------|-------|-------------|
| `DJANGO_WEBHOOK_URL` | `https://power-up.lu/finops/api/sync/` | Django webhook endpoint |
| `SECRET_SYNC_TOKEN` | `<secure-token>` | Shared secret for authentication |
| `FINOPS_SYNC_ENABLED` | `true` | Enable/disable sync |
| `DJANGO_RETAIL_PRICE_WEBHOOK_URL` | `https://power-up.lu/finops/api/sync/retail-prices/` | Retail price snapshot endpoint |
| `RETAIL_PRICE_SYNC_ENABLED` | `true` | Enable/disable the independent retail price snapshot |
| `FUNCTIONS_WORKER_RUNTIME` | `python` | Python runtime |
| `FUNCTIONS_EXTENSION_VERSION` | `~4` | Functions v4 runtime |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | `<connection-string>` | Application Insights telemetry |

### Schedule

- **Cron Expression:** `0 0 3 * * *`
- **Frequency:** Daily at 3:00 AM UTC
- **Timeout:** 10 minutes

Retail price snapshots use `0 0 4 * * *` (daily at 4:00 AM UTC). Keep
`RETAIL_PRICE_SYNC_ENABLED=false` until the Django migration and endpoint are
deployed and the new webhook URL is configured.

Deploy order matters: **Django first, then the Function App.** Django and this
Function App ship on separate pipelines, and the per-region endpoint is what
the new timer calls. Deploying the Function App first means every region POST
gets a `400` until Django catches up.

## Local Development

### Prerequisites

- Azure Functions Core Tools v4
- Python 3.11
- Django development server running locally

### Setup

```bash
cd azure-functions/finops-daily-sync

# Copy example settings
cp local.settings.json.example local.settings.json

# Edit local.settings.json with your local values
# DJANGO_WEBHOOK_URL: http://localhost:8000/finops/api/sync/
# SECRET_SYNC_TOKEN: match your local .env file

# Install dependencies
pip install -r requirements.txt

# Run locally
func start
```

### Manual Trigger

```bash
# Trigger via HTTP endpoint (when running locally)
curl -X POST http://localhost:7071/admin/functions/finops_daily_sync
```

## Deployment

### Via GitHub Actions

Deployment is automated via `.github/workflows/deploy-finops-sync-function.yml`:

- **Triggers:** Push to `main` with changes to `azure-functions/finops-daily-sync/**`
- **Manual:** Via GitHub Actions UI (`workflow_dispatch`)

### Manual Deployment

```bash
# Install Azure Functions Core Tools
# https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local

# Login to Azure
az login

# Deploy
cd azure-functions/finops-daily-sync
func azure functionapp publish finops-daily-sync
```

## Monitoring

### Azure Portal

1. Navigate to Function App → Functions → `finops_daily_sync`
2. Click "Monitor" tab
3. View execution history with timestamps and status

### Application Insights

Query recent executions:

```kusto
traces
| where timestamp > ago(7d)
| where cloud_RoleName == "finops-daily-sync"
| order by timestamp desc
```

### View Logs (CLI)

```bash
az webapp log tail --name finops-daily-sync --resource-group django-app-rg
```

## Troubleshooting

### Function not executing

1. Check schedule is correct in `function_app.py`
2. Verify function app is running in Azure Portal
3. Check Application Insights for errors
4. Ensure `FINOPS_SYNC_ENABLED=true`

### Authentication errors

1. Verify `SECRET_SYNC_TOKEN` matches between Function App and Django
2. Check Bearer token is being sent in `X-Sync-Token` header
3. Test health endpoint (no auth required): `/finops/api/sync/status/`

### Deployment failures

1. Check GitHub Actions workflow logs
2. Verify publish profile secret is set correctly
3. Ensure `requirements.txt` dependencies are compatible with Python 3.11
4. Check Azure Functions Core Tools version (v4 required)

### Timeout issues

- Function timeout is set to 10 minutes in `host.json`
- If sync takes longer, consider:
  1. Optimizing Django import command
  2. Reducing data volume per sync
  3. Implementing incremental imports

### Past-due warnings

- Warning: "Function past-due" indicates cold start delay
- Normal for serverless functions
- Does not affect functionality
- Function will execute after warm-up

## Testing

### Test Health Endpoint

```bash
curl https://power-up.lu/finops/api/sync/status/
```

Expected response:
```json
{
  "status": "ok",
  "message": "Webhook endpoint is operational"
}
```

### Test Manual Sync

```bash
curl -X POST \
  https://power-up.lu/finops/api/sync/ \
  -H "X-Sync-Token: YOUR_SECRET_TOKEN"
```

Expected response:
```json
{
  "status": "success",
  "message": "Cost sync initiated successfully",
  "details": {...}
}
```

### Verify Deployment

```bash
# Check last modified timestamp
az functionapp show \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --query "lastModifiedTimeUtc" \
  --output tsv
```

## CI/CD Pipeline

The GitHub Actions workflow (`.github/workflows/deploy-finops-sync-function.yml`) handles:

1. Python 3.11 setup
2. Dependency installation
3. Function app structure verification
4. Deployment to Azure using publish profile

**Required Secret:** `AZURE_FUNCTIONAPP_PUBLISH_PROFILE_FINOPS_SYNC`

To get the publish profile:

```bash
az functionapp deployment list-publishing-profiles \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --xml
```

Then add to GitHub: Settings → Secrets and variables → Actions → New repository secret

## Security

- **Authentication:** Shared secret token (`SECRET_SYNC_TOKEN`)
- **HTTPS Only:** All production traffic uses TLS
- **Secret Management:** Tokens stored in Azure Key Vault (recommended) or Environment Variables
- **Network:** Function can use VNet integration for additional security

## Performance

- **Cold Start:** ~5-10 seconds (first execution after idle period)
- **Warm Execution:** ~1-2 seconds function overhead
- **Total Duration:** Depends on Django import time (typically 2-8 minutes)
- **Memory:** Default allocation is sufficient (<512 MB)

## Related Files

- Django Webhook: `power_up/finops/views_webhook.py`
- Management Command: `power_up/finops/management/commands/sync_daily_costs.py`
- GitHub Workflow: `.github/workflows/deploy-finops-sync-function.yml`
