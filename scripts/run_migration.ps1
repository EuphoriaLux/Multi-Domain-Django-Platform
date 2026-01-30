# Wrapper script to run blob migration with proper environment variables
#
# Usage:
#   .\scripts\run_migration.ps1 [-DryRun] [-Platform <name>] [-Verify]

param(
    [switch]$DryRun = $false,
    [string]$Platform = "",
    [switch]$Verify = $false
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

if ($Platform) {
    $args += "--platform"
    $args += $Platform
}

if ($Verify) {
    $args += "--verify"
}

# Run migration script
Write-Host "Running migration script..." -ForegroundColor Cyan
& .venv/Scripts/python.exe scripts/migrate_to_platform_containers.py @args
