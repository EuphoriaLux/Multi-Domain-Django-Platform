# Provision the crush-hybrid-maintenance Function App in django-app-rg.
#
# PowerShell equivalent of provision.sh for Windows dev machines that
# don't have bash on PATH. Idempotent: skips creation if the resource
# already exists. Pure ASCII on purpose -- Windows PowerShell 5.1 reads
# .ps1 files as CP-1252 by default, so Unicode chars (em-dashes, smart
# quotes) corrupt the parser.
#
# Prerequisites:
#   - az login to the powerup.lu tenant, subscription "Partner Led"
#     (64c21818-0806-461a-919c-1c02b989a2d1)
#   - ADMIN_API_KEY available on the existing crush-contact-sync function
#
# Usage (from repo root):
#   ./azure-functions/hybrid-maintenance/provision.ps1
#
# After the script finishes, verify with:
#   az functionapp show -n crush-hybrid-maintenance -g django-app-rg
# Then flip HYBRID_MAINTENANCE_ENABLED=true once you have confirmed the
# first timer tick runs cleanly in Application Insights.

$ErrorActionPreference = "Stop"

$RG = "django-app-rg"
$LOCATION = "westeurope"
$FUNC_APP = "crush-hybrid-maintenance"
# Shares storage with the two existing Function Apps to match the
# crush-contact-sync pattern (AzureWebJobsStorage points here).
$STORAGE_ACCOUNT = "mediabjnukuybtvjdy"
$APP_SERVICE = "django-app-ajfffwjb5ie3s-app-service"
$SIBLING_FUNC = "crush-contact-sync"

Write-Host "==> Verifying az context"
az account show --query "{name:name, id:id}" -o table

Write-Host "==> Checking whether $FUNC_APP already exists"
$existing = az functionapp show -n $FUNC_APP -g $RG --query "name" -o tsv 2>$null
if ($LASTEXITCODE -eq 0 -and $existing) {
    Write-Host "Function App $FUNC_APP already exists - skipping create"
} else {
    Write-Host "==> Creating Function App $FUNC_APP (Consumption / Linux / Python 3.12)"
    az functionapp create `
        --resource-group $RG `
        --name $FUNC_APP `
        --consumption-plan-location $LOCATION `
        --runtime python `
        --runtime-version 3.12 `
        --functions-version 4 `
        --os-type Linux `
        --storage-account $STORAGE_ACCOUNT `
        --assign-identity '[system]' `
        --disable-app-insights false
    if ($LASTEXITCODE -ne 0) { throw "functionapp create failed" }
}

Write-Host "==> Pulling ADMIN_API_KEY from $SIBLING_FUNC"
# Single-quoted PowerShell literals (doubled '' escapes inner quote) so
# the tokenizer does not try to parse the JMESPath [0] as a type literal.
$queryAdmin = '[?name==''ADMIN_API_KEY''].value | [0]'
$ADMIN_API_KEY = az functionapp config appsettings list `
    -n $SIBLING_FUNC -g $RG `
    --query $queryAdmin -o tsv
if ([string]::IsNullOrWhiteSpace($ADMIN_API_KEY)) {
    Write-Error "ADMIN_API_KEY not found on $SIBLING_FUNC"
    exit 1
}

Write-Host "==> Pulling APPLICATIONINSIGHTS_CONNECTION_STRING from $APP_SERVICE"
$queryAppInsights = '[?name==''APPLICATIONINSIGHTS_CONNECTION_STRING''].value | [0]'
$APPINSIGHTS_CONN = az webapp config appsettings list `
    -n $APP_SERVICE -g $RG `
    --query $queryAppInsights -o tsv
if ([string]::IsNullOrWhiteSpace($APPINSIGHTS_CONN)) {
    Write-Warning "APPLICATIONINSIGHTS_CONNECTION_STRING not set on $APP_SERVICE - Function timers will log locally but will not correlate with Django traces"
}

Write-Host "==> Setting app settings on $FUNC_APP"
# HYBRID_MAINTENANCE_ENABLED stays false so the timers deploy dark.
# Flip it to true only after the first invocation has appeared cleanly
# in Application Insights.
$settings = @(
    "ADMIN_API_KEY=$ADMIN_API_KEY",
    "DJANGO_PRE_SCREENING_INVITES_URL=https://crush.lu/api/admin/pre-screening-invites/",
    "DJANGO_HYBRID_SLA_SWEEP_URL=https://crush.lu/api/admin/hybrid-coach-sla-sweep/",
    "HYBRID_MAINTENANCE_ENABLED=false",
    "ApplicationInsightsAgent_EXTENSION_VERSION=disabled"
)
if (-not [string]::IsNullOrWhiteSpace($APPINSIGHTS_CONN)) {
    $settings += "APPLICATIONINSIGHTS_CONNECTION_STRING=$APPINSIGHTS_CONN"
}

az functionapp config appsettings set `
    -n $FUNC_APP -g $RG `
    --settings $settings `
    --output none
if ($LASTEXITCODE -ne 0) { throw "appsettings set failed" }

Write-Host ""
Write-Host "==> Done."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Merge PR #368 to main. The deploy-hybrid-maintenance-function.yml"
Write-Host "     workflow will publish function_app.py on first push."
Write-Host "  2. Tail logs:"
Write-Host "       az functionapp log tail -n $FUNC_APP -g $RG"
Write-Host "  3. Once the first timer tick runs cleanly, enable the timers:"
Write-Host "       az functionapp config appsettings set -n $FUNC_APP -g $RG --settings HYBRID_MAINTENANCE_ENABLED=true"
Write-Host "  4. Flip the Django feature flag:"
Write-Host "       az webapp config appsettings set -n $APP_SERVICE -g $RG --settings PRE_SCREENING_ENABLED=True"
