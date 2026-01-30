# Azure CLI Deployment Guide - FinOps Hub

## 🚀 Quick Start

### Deploy Azure Function

```bash
# Run function deployment (generates sync token)
./scripts/deploy-finops-function.sh --generate-token

# Or use existing token from environment
export SECRET_SYNC_TOKEN='your-token-here'
./scripts/deploy-finops-function.sh
```

### Run Database Migrations

```bash
# Activate virtual environment
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\Activate.ps1  # Windows

# Run migrations
python manage.py migrate finops
```

---

## 📋 What Gets Deployed

1. **Database Migration** - Adds `CostAnomaly` table
2. **Azure Function App** - Daily sync at 3 AM UTC
3. **Storage Account** - For Function App
4. **App Service Config** - Adds sync token
5. **Application Insights** - Links monitoring

---

## 🔧 Prerequisites

Install Azure CLI:
- **Windows:** `winget install Microsoft.AzureCLI`
- **Mac:** `brew install azure-cli`
- **Linux:** `curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash`

Login to Azure:
```bash
az login
```

Set subscription (if you have multiple):
```bash
az account set --subscription "Your Subscription Name"
```

---

## 📦 Option 1: Complete Deployment (PowerShell Only)

**Deploys everything in one command:**

```powershell
# Navigate to project root
cd C:\Users\User\Github-Local\Multi-Domain-Django-Platform

# Run deployment
.\scripts\deploy-all.ps1
```

**What it does:**
- ✅ Checks prerequisites
- ✅ Generates sync token (or uses existing)
- ✅ Runs database migrations
- ✅ Creates Function App
- ✅ Configures storage
- ✅ Deploys function code
- ✅ Updates App Service settings
- ✅ Verifies deployment

**Expected duration:** 5-10 minutes

---

## 📦 Option 2: Function Only Deployment

### PowerShell

```powershell
# Generate new token and deploy
.\scripts\deploy-finops-function.ps1 -GenerateToken

# Or use existing token from environment
$env:SECRET_SYNC_TOKEN = "your-token-here"
.\scripts\deploy-finops-function.ps1
```

### Bash

```bash
# Generate new token and deploy
./scripts/deploy-finops-function.sh --generate-token

# Or use existing token from environment
export SECRET_SYNC_TOKEN="your-token-here"
./scripts/deploy-finops-function.sh
```

### Custom Configuration

```powershell
# PowerShell - Custom settings
.\scripts\deploy-finops-function.ps1 `
    -ResourceGroup "my-resource-group" `
    -FunctionAppName "my-function-app" `
    -Location "eastus" `
    -GenerateToken
```

```bash
# Bash - Custom settings
./scripts/deploy-finops-function.sh \
    --resource-group "my-resource-group" \
    --function-app-name "my-function-app" \
    --location "eastus" \
    --generate-token
```

---

## 🗄️ Database Migrations (Manual)

If you skipped migrations or need to run separately:

```bash
# Activate virtual environment
.venv\Scripts\Activate.ps1  # Windows
source .venv/bin/activate    # Linux/Mac

# Run migrations
python manage.py migrate finops

# Verify table created
python manage.py sqlmigrate finops 0003
```

**Expected output:**
```
Applying finops.0003_add_cost_anomaly_model... OK
```

---

## ⚙️ App Service Configuration (Manual)

If you need to update Django App Service settings manually:

```bash
# Set the sync token
az webapp config appsettings set \
  --name YOUR_APP_SERVICE_NAME \
  --resource-group django-app-rg \
  --settings SECRET_SYNC_TOKEN="your-token-here"

# Verify settings
az webapp config appsettings list \
  --name YOUR_APP_SERVICE_NAME \
  --resource-group django-app-rg \
  --query "[?name=='SECRET_SYNC_TOKEN']"
```

---

## ✅ Verification Steps

### 1. Check Function App Created

```bash
az functionapp list \
  --resource-group django-app-rg \
  --query "[?name=='finops-daily-sync'].{Name:name, State:state, Runtime:kind}" \
  --output table
```

**Expected:**
```
Name               State     Runtime
-----------------  --------  ---------------
finops-daily-sync  Running   functionapp,linux
```

### 2. Check Function Deployed

```bash
az functionapp function list \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --output table
```

**Expected:**
```
Name                 ScriptFile
-------------------  ------------
finops_daily_sync    function_app.py
```

### 3. Check Configuration

```bash
az functionapp config appsettings list \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --query "[?name=='FINOPS_SYNC_ENABLED' || name=='DJANGO_WEBHOOK_URL'].{Name:name, Value:value}" \
  --output table
```

**Expected:**
```
Name                    Value
----------------------  -------------------------------
DJANGO_WEBHOOK_URL      https://power-up.lu/finops/api/sync/
FINOPS_SYNC_ENABLED     true
```

### 4. Test Manual Trigger

**Via Azure Portal:**
1. Portal → Function App → Functions → `finops_daily_sync`
2. Click "Code + Test"
3. Click "Test/Run"
4. Click "Run"

**Expected output:**
```json
{
  "status": "success",
  "message": "Cost sync initiated successfully"
}
```

**Via Azure CLI:**
```bash
# Get function URL
FUNCTION_URL=$(az functionapp function show \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --function-name finops_daily_sync \
  --query "invokeUrlTemplate" -o tsv)

# Trigger function
curl -X POST "$FUNCTION_URL"
```

### 5. Check Logs

```bash
# Real-time logs
az webapp log tail \
  --name finops-daily-sync \
  --resource-group django-app-rg

# Expected output (after trigger):
# [timestamp] Initiating FinOps daily cost sync
# [timestamp] Target webhook: https://power-up.lu/finops/api/sync/
# [timestamp] Sync completed successfully
```

### 6. Verify Database Table

```bash
# Connect to Django shell
python manage.py shell

# In Python shell:
from power_up.finops.models import CostAnomaly
print(CostAnomaly.objects.count())  # Should return 0 (no anomalies yet)
print(CostAnomaly._meta.db_table)   # Should print: finops_hub_costanomaly
exit()
```

### 7. Test Public Dashboard

```bash
# Test dashboard loads without auth
curl -I https://power-up.lu/finops/

# Expected: HTTP/1.1 200 OK (NOT 302 redirect)
```

```bash
# Test anomaly dashboard
curl -I https://power-up.lu/finops/anomalies/

# Expected: HTTP/1.1 200 OK
```

---

## 🔍 Monitoring

### View Function Execution History

```bash
# List recent invocations
az monitor activity-log list \
  --resource-group django-app-rg \
  --resource-id $(az functionapp show --name finops-daily-sync --resource-group django-app-rg --query id -o tsv) \
  --start-time $(date -u -d '1 day ago' '+%Y-%m-%dT%H:%M:%SZ') \
  --query "[].{Time:eventTimestamp, Status:status.value, Operation:operationName.localizedValue}" \
  --output table
```

### Query Application Insights

```bash
# Get App Insights resource ID
APP_INSIGHTS_ID=$(az monitor app-insights component show \
  --app finops-daily-sync \
  --resource-group django-app-rg \
  --query id -o tsv)

# Query recent traces
az monitor app-insights query \
  --app $APP_INSIGHTS_ID \
  --analytics-query "traces | where timestamp > ago(1d) | order by timestamp desc | take 20" \
  --output table
```

---

## 🛠️ Troubleshooting

### Function Not Running

```bash
# Check status
az functionapp show \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --query "{State:state, HostName:defaultHostName}" \
  --output table

# If stopped, start it
az functionapp start \
  --name finops-daily-sync \
  --resource-group django-app-rg
```

### Token Mismatch

```bash
# Check Function App token
az functionapp config appsettings list \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --query "[?name=='SECRET_SYNC_TOKEN'].value" -o tsv

# Check Django App Service token
az webapp config appsettings list \
  --name YOUR_APP_SERVICE_NAME \
  --resource-group django-app-rg \
  --query "[?name=='SECRET_SYNC_TOKEN'].value" -o tsv

# They must match exactly!
```

### Deployment Failed

```bash
# Check deployment status
az functionapp deployment list-publishing-credentials \
  --name finops-daily-sync \
  --resource-group django-app-rg

# Redeploy manually
cd azure-functions/finops-daily-sync
zip -r function.zip .
az functionapp deployment source config-zip \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --src function.zip
```

### Migration Issues

```bash
# Check migration status
python manage.py showmigrations finops

# If stuck, fake the migration
python manage.py migrate finops 0003 --fake

# If table exists but migration not recorded
python manage.py migrate finops 0003 --fake-initial
```

---

## 🔄 Update/Redeploy

### Update Function Code Only

```bash
# Just run deployment script again
.\scripts\deploy-finops-function.ps1  # PowerShell
./scripts/deploy-finops-function.sh   # Bash
```

### Update Configuration Only

```bash
# Update single setting
az functionapp config appsettings set \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --settings FINOPS_SYNC_ENABLED=false

# Update multiple settings
az functionapp config appsettings set \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  --settings FINOPS_SYNC_ENABLED=true DJANGO_WEBHOOK_URL=https://new-url.com/api/sync/
```

### Restart Services

```bash
# Restart Function App
az functionapp restart --name finops-daily-sync --resource-group django-app-rg

# Restart Django App Service
az webapp restart --name YOUR_APP_SERVICE_NAME --resource-group django-app-rg
```

---

## 🗑️ Rollback/Cleanup

### Stop Function (Don't Delete)

```bash
az functionapp stop --name finops-daily-sync --resource-group django-app-rg
```

### Delete Function App

```bash
# Delete function app only (keeps storage)
az functionapp delete --name finops-daily-sync --resource-group django-app-rg

# Delete with confirmation
az functionapp delete --name finops-daily-sync --resource-group django-app-rg --yes
```

### Rollback Migration

```bash
# Rollback to previous migration
python manage.py migrate finops 0002

# This drops the CostAnomaly table
```

---

## 📊 Cost Estimates

**Azure Function App (Consumption Plan):**
- Executions: 1/day = ~30/month
- Duration: ~5 min/execution
- Memory: 512 MB
- **Estimated cost: <$1/month**

**Azure Function App (Premium P0v3):**
- Same tier as contact-sync
- Shared resources
- **No additional cost** (already paying for plan)

**Storage Account:**
- Standard LRS
- Minimal usage
- **Estimated cost: <$0.50/month**

**Total estimated monthly cost: <$1.50** (or $0 if using existing Premium plan)

---

## 📞 Support

### Get Help

```bash
# Script help
.\scripts\deploy-all.ps1 -Help                    # PowerShell
./scripts/deploy-finops-function.sh --help        # Bash

# Azure CLI help
az functionapp --help
az webapp --help
```

### Export Configuration

```bash
# Export Function App settings
az functionapp config appsettings list \
  --name finops-daily-sync \
  --resource-group django-app-rg \
  > finops-config-backup.json

# Export App Service settings
az webapp config appsettings list \
  --name YOUR_APP_SERVICE_NAME \
  --resource-group django-app-rg \
  > app-service-config-backup.json
```

---

## ✅ Success Checklist

After deployment, verify all of these:

- [ ] Function App exists and is "Running"
- [ ] Function `finops_daily_sync` is deployed
- [ ] Storage account created
- [ ] Configuration settings correct (6 variables)
- [ ] SECRET_SYNC_TOKEN matches in both apps
- [ ] Database migration 0003 applied
- [ ] Table `finops_hub_costanomaly` exists
- [ ] Manual function trigger succeeds
- [ ] Webhook returns HTTP 200
- [ ] Public dashboard loads (no auth)
- [ ] Anomaly dashboard loads
- [ ] Logs visible in Application Insights
- [ ] Function scheduled for 3 AM UTC

**If all checked, deployment is successful! ✅**

---

## 📚 Related Documentation

- **Detailed Guide:** `FINOPS_DEPLOYMENT_CHECKLIST.md`
- **Implementation Details:** `FINOPS_IMPLEMENTATION_SUMMARY.md`
- **Manual Steps:** `BEFORE_PRODUCTION_DEPLOYMENT.md`
- **Function Docs:** `azure-functions/finops-daily-sync/README.md`
