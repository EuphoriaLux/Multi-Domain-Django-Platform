# FinOps Hub - Automated Cost Data Sync Guide

## Current Situation

Your FinOps system does **NOT** automatically check for new Azure cost export files. You must manually run:

```bash
python manage.py sync_daily_costs
```

This guide provides **three options** for automating daily cost data synchronization.

---

## Option 1: Azure App Service WebJobs (Recommended)

### Overview
Azure WebJobs run scheduled tasks directly on your App Service without additional infrastructure.

### Setup Steps

1. **Create WebJob Directory Structure**

   In your Azure App Service, create this folder structure:
   ```
   wwwroot/
   └── App_Data/
       └── jobs/
           └── triggered/
               └── sync-costs/
                   ├── run.sh
                   └── settings.job
   ```

2. **Upload Files via SSH/FTP**

   Upload the files from `scripts/` directory:
   - `sync_costs.sh` → rename to `run.sh`
   - `settings.job` → keep as-is

3. **Make Script Executable**

   ```bash
   chmod +x /home/site/wwwroot/App_Data/jobs/triggered/sync-costs/run.sh
   ```

4. **Configure Environment Variable**

   In Azure Portal → App Service → Configuration:
   - No additional variables needed (uses existing AZURE_ACCOUNT_NAME/KEY)

5. **Verify WebJob**

   - Go to Azure Portal → Your App Service → WebJobs
   - You should see "sync-costs" listed
   - Check "Logs" to view execution history

### Schedule

- **Default**: Runs daily at 2:00 AM UTC
- **Customize**: Edit `settings.job`:
  ```json
  {
    "schedule": "0 0 2 * * *",  // CRON format: sec min hour day month weekday
    "description": "Sync Azure cost data daily at 2 AM UTC"
  }
  ```

### Pros & Cons

✅ **Pros:**
- No extra infrastructure needed
- Built into Azure App Service
- Easy to set up
- Logs integrated with App Service

❌ **Cons:**
- Requires manual file upload to Azure
- Limited to basic scheduling
- Not version controlled easily

---

## Option 2: Azure Logic Apps (Serverless, No Code)

### Overview
Use Azure Logic Apps to trigger your Django webhook on a schedule.

### Setup Steps

1. **Set Environment Variable**

   Add to your App Service Configuration:
   ```
   SECRET_SYNC_TOKEN=<generate-random-secure-token>
   ```

   Generate token with:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. **Create Logic App**

   Azure Portal → Create Resource → Logic App:
   - Name: `finops-daily-sync`
   - Region: Same as App Service
   - Plan Type: Consumption (pay-per-use)

3. **Configure Logic App Workflow**

   In Logic App Designer, create this flow:

   **Trigger:**
   - Type: `Recurrence`
   - Interval: `1 day`
   - Start time: `02:00:00` (2 AM UTC)

   **Action:**
   - Type: `HTTP`
   - Method: `POST`
   - URI: `https://yourapp.azurewebsites.net/finops/api/sync/`
   - Headers:
     ```json
     {
       "X-Sync-Token": "<your-secret-token>",
       "Content-Type": "application/json"
     }
     ```

4. **Test Logic App**

   Click "Run Trigger" → "Run" to test manually

5. **Monitor Executions**

   Logic App → Overview → Runs history

### Webhook Endpoints

The system provides two webhook endpoints:

**Trigger Sync (Protected):**
```bash
POST /finops/api/sync/
Headers:
  X-Sync-Token: <your-secret-token>

Response:
{
  "success": true,
  "message": "Cost sync completed successfully",
  "output": "... command output ...",
  "errors": null
}
```

**Check Status (Public):**
```bash
GET /finops/api/sync/status/

Response:
{
  "success": true,
  "stats": {
    "total_exports": 10,
    "completed": 9,
    "failed": 0,
    "processing": 1
  },
  "latest_export": {
    "subscription": "PartnerLed-power_up",
    "billing_period_start": "2025-10-01",
    "billing_period_end": "2025-10-31",
    "records_imported": 1234,
    "import_completed_at": "2025-10-17T02:15:32Z"
  }
}
```

### Pros & Cons

✅ **Pros:**
- Visual designer (no code)
- Reliable Azure-native solution
- Easy to modify schedule
- Built-in retry logic
- Monitoring and alerts

❌ **Cons:**
- Additional Azure resource (minimal cost)
- Requires webhook endpoint setup
- Network dependency

---

## Option 3: Azure Functions (Serverless, Code-Based)

### Overview
Create a Python Azure Function that runs on schedule and calls the webhook.

### Setup Steps

1. **Create Azure Function**

   ```bash
   # Install Azure Functions Core Tools
   npm install -g azure-functions-core-tools@4

   # Create function app
   mkdir finops-sync-function
   cd finops-sync-function
   func init . --python

   # Create timer-triggered function
   func new --name sync-costs --template "Timer trigger"
   ```

2. **Edit `sync-costs/__init__.py`**

   ```python
   import azure.functions as func
   import logging
   import requests
   import os

   def main(mytimer: func.TimerRequest) -> None:
       logging.info('Starting FinOps cost sync')

       # Webhook URL and token
       webhook_url = os.environ['FINOPS_WEBHOOK_URL']
       sync_token = os.environ['SECRET_SYNC_TOKEN']

       # Call Django webhook
       try:
           response = requests.post(
               webhook_url,
               headers={'X-Sync-Token': sync_token},
               timeout=600  # 10 minutes
           )
           response.raise_for_status()

           result = response.json()
           logging.info(f'Sync completed: {result}')

       except Exception as e:
           logging.error(f'Sync failed: {str(e)}')
           raise
   ```

3. **Edit `sync-costs/function.json`**

   ```json
   {
     "scriptFile": "__init__.py",
     "bindings": [
       {
         "name": "mytimer",
         "type": "timerTrigger",
         "direction": "in",
         "schedule": "0 0 2 * * *"
       }
     ]
   }
   ```

4. **Deploy to Azure**

   ```bash
   # Create Function App (if not exists)
   az functionapp create --name finops-sync-func \
     --resource-group your-rg \
     --consumption-plan-location westeurope \
     --runtime python --runtime-version 3.11 \
     --storage-account yourstorageaccount

   # Deploy
   func azure functionapp publish finops-sync-func

   # Set environment variables
   az functionapp config appsettings set \
     --name finops-sync-func \
     --resource-group your-rg \
     --settings \
       "FINOPS_WEBHOOK_URL=https://yourapp.azurewebsites.net/finops/api/sync/" \
       "SECRET_SYNC_TOKEN=<your-token>"
   ```

### Pros & Cons

✅ **Pros:**
- Full code control
- Azure-native serverless
- Highly scalable
- Pay only for executions

❌ **Cons:**
- More complex setup
- Requires deployment pipeline
- Additional Azure resource

---

## Comparison Matrix

| Feature | WebJobs | Logic Apps | Azure Functions |
|---------|---------|------------|-----------------|
| **Setup Complexity** | Low | Very Low | Medium |
| **Cost** | Free (included) | ~$0.01/day | ~$0.01/day |
| **Customization** | Medium | Low | High |
| **Monitoring** | Basic | Excellent | Excellent |
| **Reliability** | Good | Excellent | Excellent |
| **Version Control** | Medium | Hard | Easy |
| **Recommended For** | Quick setup | Non-developers | Developers |

---

## Recommended Approach

### For Production: **Azure Logic Apps**

Why?
1. No code deployment required
2. Visual monitoring and alerts
3. Easy schedule changes via portal
4. Built-in retry logic
5. Enterprise-grade reliability

### For Development/Testing: **Manual Execution**

Run manually when needed:
```bash
python manage.py sync_daily_costs
```

---

## Testing the Automation

### 1. Test Webhook Manually

```bash
# Generate a token
export SYNC_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Add to App Service environment variables
# THEN test:

curl -X POST https://yourapp.azurewebsites.net/finops/api/sync/ \
  -H "X-Sync-Token: $SYNC_TOKEN" \
  -v
```

### 2. Check Sync Status

```bash
curl https://yourapp.azurewebsites.net/finops/api/sync/status/ | jq
```

### 3. Monitor Logs

Azure Portal → App Service → Log stream

---

## Monitoring & Alerts

### Set Up Email Alerts

1. Azure Portal → Logic App → Settings → Alerts
2. Add alert rule:
   - Condition: Run fails
   - Action: Email notification
   - Frequency: Immediately

### Monitor Cost Exports

Check the FinOps dashboard:
```
https://yourapp.azurewebsites.net/finops/
```

View import history:
- Total exports processed
- Latest import timestamp
- Failed imports (if any)

---

## Troubleshooting

### Webhook Returns 403 (Invalid Token)

1. Check environment variable is set:
   ```bash
   az webapp config appsettings list --name yourapp --resource-group your-rg | grep SECRET_SYNC_TOKEN
   ```

2. Verify token matches in both places:
   - App Service environment variable
   - Logic App HTTP header

### Import Fails with "Bad Request"

1. Check blob storage credentials:
   ```bash
   az webapp config appsettings list --name yourapp | grep AZURE_ACCOUNT
   ```

2. Verify container name: `msexports`

3. Check blob reader fix was deployed (see [blob_reader.py:177-183](finops_hub/utils/blob_reader.py#L177))

### No New Files Detected

Azure Cost Management generates exports daily. Files appear:
- **Daily exports**: Next day after usage
- **Monthly exports**: End of month + 1-2 days

Check Azure Portal → Cost Management → Exports to verify exports are running.

---

## Next Steps

1. Choose automation option (recommend Logic Apps)
2. Set up SECRET_SYNC_TOKEN environment variable
3. Create Logic App or WebJob
4. Test manually first
5. Monitor first few automated runs
6. Set up email alerts

---

## Questions?

- View import logs: `/finops/` dashboard
- Check export status: `/finops/api/sync/status/`
- Manual sync: `python manage.py sync_daily_costs`
