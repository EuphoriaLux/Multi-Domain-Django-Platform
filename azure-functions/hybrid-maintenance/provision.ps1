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
# Capture the CURRENT target before the settings below overwrite every URL, so
# the master-switch logic further down can tell "re-run to add a URL for the
# same slot" from "-Slot flipped the target to the other slot".
$queryPrevUrl = '[?name==''DJANGO_PRE_SCREENING_INVITES_URL''].value | [0]'
$previousUrl = az functionapp config appsettings list -n $FUNC_APP -g $RG `
    --query $queryPrevUrl -o tsv
$previousHost = ""
if (-not [string]::IsNullOrWhiteSpace($previousUrl)) {
    $previousHost = ([System.Uri]$previousUrl).Host
}

# HYBRID_MAINTENANCE_ENABLED is deliberately NOT in the array below -- it is
# written separately, and only when it does not already exist.
#
# It used to be pinned to "false" here so the timers "deploy dark". That is
# right for a first provision and a trap on every run after it: this script is
# also the documented home of every DJANGO_*_URL, so the natural way to add a
# new URL is to re-run it -- which would have re-set the master switch to false
# and silently stopped all twelve timers. `_call_admin_endpoint` checks that
# flag before anything else and returns quietly, so every invocation would keep
# reporting *Success* while nothing ran at all.
$settings = @(
    "ADMIN_API_KEY=$ADMIN_API_KEY",
    "DJANGO_PRE_SCREENING_INVITES_URL=https://$DJANGO_HOST/api/admin/pre-screening-invites/",
    "DJANGO_HYBRID_SLA_SWEEP_URL=https://$DJANGO_HOST/api/admin/hybrid-coach-sla-sweep/",
    "DJANGO_WEEKLY_KPIS_URL=https://$DJANGO_HOST/api/admin/weekly-kpis/",
    "DJANGO_ROTATE_CONNECT_QUESTIONS_URL=https://$DJANGO_HOST/api/admin/rotate-connect-questions/",
    "DJANGO_CAMPAIGN_DISPATCH_URL=https://$DJANGO_HOST/api/admin/campaigns/dispatch/",
    "DJANGO_PROFILE_REMINDERS_URL=https://$DJANGO_HOST/api/admin/profile-reminders/",
    "DJANGO_GDPR_RETENTION_URL=https://$DJANGO_HOST/api/admin/gdpr-retention/",
    "DJANGO_CRUSH_LEAD_REMINDERS_URL=https://$DJANGO_HOST/api/admin/crush-lead-reminders/",
    "DJANGO_EVENT_REMINDERS_URL=https://$DJANGO_HOST/api/admin/event-reminders/",
    "DJANGO_EVENT_RECAPS_URL=https://$DJANGO_HOST/api/admin/event-recaps/",
    "DJANGO_EVENT_FEEDBACK_URL=https://$DJANGO_HOST/api/admin/event-feedback/",
    "DJANGO_ECHO_SYNC_URL=https://$DJANGO_HOST/api/admin/echo-sync/",
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

# Master switch. Two cases deploy dark, one preserves.
#
# Preserving an existing "true" is right when the run only adds or refreshes a
# URL for the SAME target -- that is the re-run this change exists to make
# safe. It is WRONG when -Slot flips the target: every URL was just repointed,
# so leaving the timers on swings all twelve -- including the production
# campaign dispatcher -- onto the other slot on the next tick, using an
# ADMIN_API_KEY that may not even be valid there (see the staging warning
# above). Retargeting therefore deploys dark, exactly like a first provision,
# and the operator re-enables after verifying one clean tick.
$queryEnabled = '[?name==''HYBRID_MAINTENANCE_ENABLED''].value | [0]'
$existingEnabled = az functionapp config appsettings list -n $FUNC_APP -g $RG `
    --query $queryEnabled -o tsv
$retargeted = (-not [string]::IsNullOrWhiteSpace($previousHost)) -and ($previousHost -ne $DJANGO_HOST)

if ([string]::IsNullOrWhiteSpace($existingEnabled)) {
    Write-Host "==> HYBRID_MAINTENANCE_ENABLED not present -- seeding to false (timers deploy dark)"
    az functionapp config appsettings set `
        -n $FUNC_APP -g $RG `
        --settings "HYBRID_MAINTENANCE_ENABLED=false" `
        --output none
    if ($LASTEXITCODE -ne 0) { throw "appsettings set failed" }
} elseif ($retargeted) {
    Write-Warning "Target changed from $previousHost to $DJANGO_HOST - forcing HYBRID_MAINTENANCE_ENABLED=false so the timers do not start hitting the new slot before you have verified it. Re-enable with: az functionapp config appsettings set -n $FUNC_APP -g $RG --settings HYBRID_MAINTENANCE_ENABLED=true"
    az functionapp config appsettings set `
        -n $FUNC_APP -g $RG `
        --settings "HYBRID_MAINTENANCE_ENABLED=false" `
        --output none
    if ($LASTEXITCODE -ne 0) { throw "appsettings set failed" }
} else {
    Write-Host "==> HYBRID_MAINTENANCE_ENABLED already set to '$existingEnabled', target unchanged ($DJANGO_HOST) -- left unchanged"
}

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
