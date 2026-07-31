#!/usr/bin/env bash
# Provision the crush-hybrid-maintenance Function App in django-app-rg.
#
# Idempotent: skips creation if the resource already exists. Meant to be
# run once from a developer machine before the GitHub Actions workflow
# (.github/workflows/deploy-hybrid-maintenance-function.yml) takes over.
#
# Prerequisites:
#   - az login to the `powerup.lu` tenant, subscription "Partner Led"
#     (64c21818-0806-461a-919c-1c02b989a2d1)
#   - ADMIN_API_KEY available on the existing crush-contact-sync function
#     (the script copies it so both functions use the same bearer token)
#   - App Insights connection string on the existing django-app App Service
#
# Usage:
#   bash azure-functions/hybrid-maintenance/provision.sh
#
# After the script finishes, verify with:
#   az functionapp show -n crush-hybrid-maintenance -g django-app-rg
# Then flip HYBRID_MAINTENANCE_ENABLED=true once you've confirmed the
# first timer tick runs cleanly in Application Insights.

set -euo pipefail

RG="django-app-rg"
LOCATION="westeurope"
FUNC_APP="crush-hybrid-maintenance"
# Shares storage with the two existing Function Apps; matches the pattern
# used by crush-contact-sync (AzureWebJobsStorage points at this account).
STORAGE_ACCOUNT="mediabjnukuybtvjdy"
APP_SERVICE="django-app-ajfffwjb5ie3s-app-service"
SIBLING_FUNC="crush-contact-sync"
# Shared Premium v3 plan used by Django + both sibling function apps.
# Linux Consumption dynamic workers are not available in this RG.
APP_SERVICE_PLAN="django-app-ajfffwjb5ie3s-service-plan"

echo "==> Verifying az context"
az account show --query "{name:name, id:id}" -o table

echo "==> Checking whether $FUNC_APP already exists"
if az functionapp show -n "$FUNC_APP" -g "$RG" --query "name" -o tsv 2>/dev/null; then
  echo "Function App $FUNC_APP already exists — skipping create"
else
  echo "==> Creating Function App $FUNC_APP (shared plan $APP_SERVICE_PLAN / Linux / Python 3.12)"
  az functionapp create \
    --resource-group "$RG" \
    --name "$FUNC_APP" \
    --plan "$APP_SERVICE_PLAN" \
    --runtime python \
    --runtime-version 3.12 \
    --functions-version 4 \
    --os-type Linux \
    --storage-account "$STORAGE_ACCOUNT" \
    --assign-identity '[system]' \
    --disable-app-insights false
fi

echo "==> Pulling ADMIN_API_KEY from $SIBLING_FUNC to keep both functions aligned"
ADMIN_API_KEY=$(az functionapp config appsettings list \
  -n "$SIBLING_FUNC" -g "$RG" \
  --query "[?name=='ADMIN_API_KEY'].value | [0]" -o tsv)
if [[ -z "$ADMIN_API_KEY" ]]; then
  echo "ERROR: ADMIN_API_KEY not found on $SIBLING_FUNC" >&2
  exit 1
fi

echo "==> Pulling APPLICATIONINSIGHTS_CONNECTION_STRING from $APP_SERVICE"
APPINSIGHTS_CONN=$(az webapp config appsettings list \
  -n "$APP_SERVICE" -g "$RG" \
  --query "[?name=='APPLICATIONINSIGHTS_CONNECTION_STRING'].value | [0]" -o tsv)
if [[ -z "$APPINSIGHTS_CONN" ]]; then
  echo "WARN: APPLICATIONINSIGHTS_CONNECTION_STRING not set on $APP_SERVICE — timer logs will still appear in Function App telemetry, but won't correlate with Django traces"
fi

echo "==> Setting app settings on $FUNC_APP"
SETTINGS=(
  "ADMIN_API_KEY=$ADMIN_API_KEY"
  "DJANGO_PRE_SCREENING_INVITES_URL=https://crush.lu/api/admin/pre-screening-invites/"
  "DJANGO_HYBRID_SLA_SWEEP_URL=https://crush.lu/api/admin/hybrid-coach-sla-sweep/"
  "DJANGO_WEEKLY_KPIS_URL=https://crush.lu/api/admin/weekly-kpis/"
  "DJANGO_ROTATE_CONNECT_QUESTIONS_URL=https://crush.lu/api/admin/rotate-connect-questions/"
  "DJANGO_CAMPAIGN_DISPATCH_URL=https://crush.lu/api/admin/campaigns/dispatch/"
  "DJANGO_PROFILE_REMINDERS_URL=https://crush.lu/api/admin/profile-reminders/"
  "DJANGO_GDPR_RETENTION_URL=https://crush.lu/api/admin/gdpr-retention/"
  "DJANGO_CRUSH_LEAD_REMINDERS_URL=https://crush.lu/api/admin/crush-lead-reminders/"
  "DJANGO_EVENT_REMINDERS_URL=https://crush.lu/api/admin/event-reminders/"
  "DJANGO_EVENT_RECAPS_URL=https://crush.lu/api/admin/event-recaps/"
  "DJANGO_EVENT_FEEDBACK_URL=https://crush.lu/api/admin/event-feedback/"
  # HYBRID_MAINTENANCE_ENABLED is deliberately NOT in this array — it is
  # written separately below, and only when it does not already exist.
  #
  # It used to be pinned to "false" here so the timers deploy dark. That is
  # right for a first provision and a trap on every run after it: this script
  # is also the documented home of every DJANGO_*_URL, so the natural way to
  # add a new URL is to re-run it — which would have re-set the master switch
  # to false and silently stopped all eleven timers. _call_admin_endpoint
  # checks that flag first and returns quietly, so every invocation would keep
  # reporting Success while nothing ran at all.
  "ApplicationInsightsAgent_EXTENSION_VERSION=disabled"
)
if [[ -n "$APPINSIGHTS_CONN" ]]; then
  SETTINGS+=("APPLICATIONINSIGHTS_CONNECTION_STRING=$APPINSIGHTS_CONN")
fi

az functionapp config appsettings set \
  -n "$FUNC_APP" -g "$RG" \
  --settings "${SETTINGS[@]}" \
  --output none

# Seed the master switch to false on a FIRST provision only; never touch an
# existing value (see the note in the settings array).
EXISTING_ENABLED=$(az functionapp config appsettings list \
  -n "$FUNC_APP" -g "$RG" \
  --query "[?name=='HYBRID_MAINTENANCE_ENABLED'].value | [0]" --output tsv)
if [[ -z "$EXISTING_ENABLED" ]]; then
  echo "==> HYBRID_MAINTENANCE_ENABLED not present — seeding to false (timers deploy dark)"
  az functionapp config appsettings set \
    -n "$FUNC_APP" -g "$RG" \
    --settings "HYBRID_MAINTENANCE_ENABLED=false" \
    --output none
else
  echo "==> HYBRID_MAINTENANCE_ENABLED already set to '$EXISTING_ENABLED' — left unchanged"
fi

echo "==> Done."
echo
echo "Next steps:"
echo "  1. Merge this PR to main — the deploy-hybrid-maintenance-function.yml"
echo "     workflow will publish function_app.py on first push."
echo "  2. Tail logs:"
echo "     az functionapp log tail -n $FUNC_APP -g $RG"
echo "  3. Once the first timer tick runs cleanly:"
echo "     az functionapp config appsettings set -n $FUNC_APP -g $RG \\"
echo "       --settings HYBRID_MAINTENANCE_ENABLED=true"
echo "  4. On the Django App Service, flip the feature flag:"
echo "     az webapp config appsettings set -n $APP_SERVICE -g $RG \\"
echo "       --settings PRE_SCREENING_ENABLED=True"
