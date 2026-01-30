# Wrapper script to migrate Crush.lu private container
#
# Usage:
#   .\scripts\run_crush_private_migration.ps1 [-DryRun] [-Verify]

param(
    [switch]$DryRun = $false,
    [switch]$Verify = $false,
    [switch]$DeleteSource = $false
)

# Storage account credentials - set these before running
# $env:AZURE_ACCOUNT_NAME = "your-storage-account-name"
# $env:AZURE_ACCOUNT_KEY = "your-storage-account-key"

if (-not $env:AZURE_ACCOUNT_NAME -or -not $env:AZURE_ACCOUNT_KEY) {
    Write-Error "AZURE_ACCOUNT_NAME and AZURE_ACCOUNT_KEY environment variables must be set"
    exit 1
}

# Build Python command arguments
$args = @()

if ($DryRun) {
    $args += "--dry-run"
}

if ($Verify) {
    $args += "--verify"
}

if ($DeleteSource) {
    $args += "--delete-source"
}

# Run migration script
Write-Host "Migrating Crush.lu private container..." -ForegroundColor Cyan
Write-Host "FROM: crush-profiles-private -> TO: crush-lu-private" -ForegroundColor Cyan
Write-Host ""

& .venv/Scripts/python.exe scripts/migrate_crush_private_container.py @args
