#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Verify Azure Functions timer triggers executed successfully

.DESCRIPTION
    Checks Application Insights logs for timer trigger executions.
    Run this after 3:15 AM UTC to verify overnight timer triggers.

.EXAMPLE
    .\scripts\verify-timer-triggers.ps1
    .\scripts\verify-timer-triggers.ps1 -Hours 24  # Check last 24 hours
#>

param(
    [int]$Hours = 24,
    [switch]$Detailed
)

$resourceGroup = "django-app-rg"
$appInsightsName = "django-app-ajfffwjb5ie3s-app-service"

Write-Host "🔍 Checking timer trigger executions (last $Hours hours)..." -ForegroundColor Cyan
Write-Host ""

# Check for timer executions
$query = @"
traces
| where timestamp > ago($($Hours)h)
| where cloud_RoleName in ('crush-contact-sync', 'finops-daily-sync')
| where message contains 'Starting' or message contains 'Initiating'
| project timestamp, cloud_RoleName, message
| order by timestamp desc
"@

Write-Host "📊 Timer Trigger Executions:" -ForegroundColor Yellow
Write-Host "----------------------------"

try {
    $result = az monitor app-insights query `
        --resource-group $resourceGroup `
        --app $appInsightsName `
        --analytics-query $query `
        --output json | ConvertFrom-Json

    if ($result.tables[0].rows.Count -eq 0) {
        Write-Host "❌ No timer executions found" -ForegroundColor Red
        Write-Host ""
        Write-Host "Expected to see:" -ForegroundColor Yellow
        Write-Host "  • crush-contact-sync at ~03:00 UTC"
        Write-Host "  • finops-daily-sync at ~03:00 UTC"
        Write-Host ""
        Write-Host "If it's before 3:15 AM UTC today, this is expected." -ForegroundColor Gray
        Write-Host "Timer triggers are scheduled for 3:00 AM UTC daily." -ForegroundColor Gray
        exit 1
    }

    foreach ($row in $result.tables[0].rows) {
        $timestamp = [DateTime]$row[0]
        $functionName = $row[1]
        $message = $row[2]

        $icon = if ($functionName -eq "crush-contact-sync") { "📧" } else { "💰" }
        Write-Host "$icon $functionName" -ForegroundColor Green
        Write-Host "   Time: $($timestamp.ToString('yyyy-MM-dd HH:mm:ss')) UTC"
        Write-Host "   Msg:  $message"
        Write-Host ""
    }

    Write-Host "✅ Timer triggers are working!" -ForegroundColor Green

    if ($Detailed) {
        Write-Host ""
        Write-Host "📋 Detailed logs (last 10 minutes):" -ForegroundColor Yellow
        Write-Host "------------------------------------"

        $detailQuery = @"
traces
| where timestamp > ago(10m)
| where cloud_RoleName in ('crush-contact-sync', 'finops-daily-sync')
| project timestamp, cloud_RoleName, severityLevel, message
| order by timestamp desc
| take 20
"@

        $detailResult = az monitor app-insights query `
            --resource-group $resourceGroup `
            --app $appInsightsName `
            --analytics-query $detailQuery `
            --output table

        Write-Host $detailResult
    }

} catch {
    Write-Host "❌ Error querying Application Insights: $_" -ForegroundColor Red
    exit 1
}

# Check Azure Files timer state
Write-Host ""
Write-Host "📁 Timer State Files:" -ForegroundColor Yellow
Write-Host "--------------------"

$shares = @(
    @{name="crush-contact-sync-content"; path="data/Functions/DailyContactSync"},
    @{name="finops-daily-sync-content"; path="data/Functions/finops_daily_sync"}
)

foreach ($share in $shares) {
    Write-Host "$($share.name):" -ForegroundColor Cyan

    $files = az storage file list `
        --account-name mediabjnukuybtvjdy `
        --share-name $share.name `
        --path $share.path `
        --output json 2>&1 | ConvertFrom-Json

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ⏳ Directory not created yet (timer hasn't fired)" -ForegroundColor Gray
    } else {
        foreach ($file in $files) {
            if ($file.name -like "*.Status") {
                Write-Host "  ✅ $($file.name) (last modified: $($file.properties.lastModified))" -ForegroundColor Green
            }
        }
    }
}

Write-Host ""
Write-Host "💡 Tip: Timer triggers run daily at 3:00 AM UTC (4:00 AM CET in winter, 5:00 AM CEST in summer)" -ForegroundColor Gray
