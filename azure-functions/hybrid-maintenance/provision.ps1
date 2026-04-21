# Provision the crush-hybrid-maintenance Function App in django-app-rg.
#
# PowerShell equivalent of provision.sh for Windows dev machines that
# don't have bash on PATH. Idempotent: skips creation if the resource
# already exists. Pure ASCII on purpose -- Windows PowerShell 5.1 reads
# .ps1 files as CP-1252 by default, so Unicode chars (em-dashes, smart
# quotes) corrupt the parser.
#
# The Function App itself runs on Consumption, which does not support
# deployment slots. The -Slot parameter instead controls which Django
# App Service slot this function points at:
#   - production (default): calls https://crush.lu/..., pulls App
#     Insights connection string from the production slot.
#   - staging: calls https://test.crush.lu/..., pulls App Insights
#     connection string from the staging slot.
# Re-run with a different -Slot to flip targets on the same Function App.
#
# Prerequisites:
#   - az login to the powerup.lu tenant, subscription "Partner Led"
#     (64c21818-0806-461a-919c-1c02b989a2d1)
#   - ADMIN_API_KEY available on the existing crush-contact-sync function
#
# Usage (from repo root):
#   ./azure-functions/hybrid-maintenance/provision.ps1             # prod
#   ./azure-functions/hybrid-maintenance/provision.ps1 -Slot staging
#
# After the script finishes, verify with:
#   az functionapp show -n crush-hybrid-maintenance -g django-app-rg
# Then flip HYBRID_MAINTENANCE_ENABLED=true once you have confirmed the
# first timer tick runs cleanly in Application Insights.

[CmdletBinding()]
param(
    [ValidateSet('production', 'staging')]
    [string]$Slot = 'production'
)

$ErrorActionPreference = "Stop"

$RG = "django-app-rg"
$LOCATION = "westeurope"
$FUNC_APP = "crush-hybrid-maintenance"
# Shares storage with the two existing Function Apps to match the
# crush-contact-sync pattern (AzureWebJobsStorage points here).
$STORAGE_ACCOUNT = "mediabjnukuybtvjdy"
$APP_SERVICE = "django-app-ajfffwjb5ie3s-app-service"
$SIBLING_FUNC = "crush-contact-sync"
# Shared Premium v3 App Service Plan used by the Django app and the
# two existing sibling Function Apps (crush-contact-sync and
# finops-daily-sync). Linux Consumption dynamic workers are not
# available in this resource group, so we reuse this plan.
$APP_SERVICE_PLAN = "django-app-ajfffwjb5ie3s-service-plan"

if ($Slot -eq 'staging') {
    $DJANGO_HOST = "test.crush.lu"
    $SLOT_ARGS = @('--slot', 'staging')
} else {
    $DJANGO_HOST = "crush.lu"
    $SLOT_ARGS = @()
}

Write-Host "==> Verifying az context (target Django slot: $Slot -> $DJANGO_HOST)"
az account show --query "{name:name, id:id}" -o table

Write-Host "==> Checking whether $FUNC_APP already exists"
# Use list+filter instead of `functionapp show` so a missing resource
# returns an empty string rather than writing ResourceNotFound to stderr
# (which PowerShell 5.1 escalates to a terminating NativeCommandError
# even with 2>$null when $ErrorActionPreference = Stop).
$existing = az functionapp list -g $RG --query "[?name=='$FUNC_APP'].name" -o tsv
if ($LASTEXITCODE -ne 0) { throw "functionapp list failed" }
if ($existing) {
    Write-Host "Function App $FUNC_APP already exists - skipping create"
} else {
    Write-Host "==> Creating Function App $FUNC_APP (shared plan $APP_SERVICE_PLAN / Linux / Python 3.12)"
    az functionapp create `
        --resource-group $RG `
        --name $FUNC_APP `
        --plan $APP_SERVICE_PLAN `
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
# crush-contact-sync runs on Consumption (no slots), so there is a
# single ADMIN_API_KEY regardless of which Django slot we target.
$queryAdmin = '[?name==''ADMIN_API_KEY''].value | [0]'
$ADMIN_API_KEY = az functionapp config appsettings list `
    -n $SIBLING_FUNC -g $RG `
    --query $queryAdmin -o tsv
if ([string]::IsNullOrWhiteSpace($ADMIN_API_KEY)) {
    Write-Error "ADMIN_API_KEY not found on $SIBLING_FUNC"
    exit 1
}
if ($Slot -eq 'staging') {
    Write-Warning "ADMIN_API_KEY is inherited from $SIBLING_FUNC (production). The Django staging slot marks ADMIN_API_KEY as a slot setting - verify the staging slot accepts this same key, otherwise set a distinct value manually on $FUNC_APP before enabling the timers."
}

Write-Host "==> Pulling APPLICATIONINSIGHTS_CONNECTION_STRING from $APP_SERVICE ($Slot slot)"
$queryAppInsights = '[?name==''APPLICATIONINSIGHTS_CONNECTION_STRING''].value | [0]'
$APPINSIGHTS_CONN = az webapp config appsettings list `
    -n $APP_SERVICE -g $RG @SLOT_ARGS `
    --query $queryAppInsights -o tsv
if ([string]::IsNullOrWhiteSpace($APPINSIGHTS_CONN)) {
    Write-Warning "APPLICATIONINSIGHTS_CONNECTION_STRING not set on $APP_SERVICE ($Slot slot) - Function timers will log locally but will not correlate with Django traces"
}

Write-Host "==> Setting app settings on $FUNC_APP"
# HYBRID_MAINTENANCE_ENABLED stays false so the timers deploy dark.
# Flip it to true only after the first invocation has appeared cleanly
# in Application Insights.
$settings = @(
    "ADMIN_API_KEY=$ADMIN_API_KEY",
    "DJANGO_PRE_SCREENING_INVITES_URL=https://$DJANGO_HOST/api/admin/pre-screening-invites/",
    "DJANGO_HYBRID_SLA_SWEEP_URL=https://$DJANGO_HOST/api/admin/hybrid-coach-sla-sweep/",
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
Write-Host "==> Done. Function App is now pointed at: https://$DJANGO_HOST ($Slot slot)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Merge PR #368 to main. The deploy-hybrid-maintenance-function.yml"
Write-Host "     workflow will publish function_app.py on first push."
Write-Host "  2. Tail logs:"
Write-Host "       az functionapp log tail -n $FUNC_APP -g $RG"
Write-Host "  3. Once the first timer tick runs cleanly, enable the timers:"
Write-Host "       az functionapp config appsettings set -n $FUNC_APP -g $RG --settings HYBRID_MAINTENANCE_ENABLED=true"
if ($Slot -eq 'staging') {
    Write-Host "  4. Flip the Django feature flag on the staging slot:"
    Write-Host "       az webapp config appsettings set -n $APP_SERVICE -g $RG --slot staging --settings PRE_SCREENING_ENABLED=True"
    Write-Host "  5. When ready to promote, re-run this script without -Slot to re-target crush.lu:"
    Write-Host "       ./azure-functions/hybrid-maintenance/provision.ps1"
} else {
    Write-Host "  4. Flip the Django feature flag on production:"
    Write-Host "       az webapp config appsettings set -n $APP_SERVICE -g $RG --settings PRE_SCREENING_ENABLED=True"
}
