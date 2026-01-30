# Create platform-specific Azure Blob Storage containers
#
# This script creates the new container structure for the multi-domain platform.
# It sets the appropriate access levels (public/private) for each container.
#
# Prerequisites:
#   - Azure CLI installed (az cli)
#   - Logged in to Azure (az login)
#   - Storage account exists
#
# Usage:
#   .\scripts\create_platform_containers.ps1 [-StorageAccount <name>] [-DryRun]
#
# Environment Variables (alternative to parameters):
#   AZURE_ACCOUNT_NAME - Storage account name

param(
    [string]$StorageAccount = $env:AZURE_ACCOUNT_NAME,
    [switch]$DryRun = $false
)

# Validate storage account name
if ([string]::IsNullOrEmpty($StorageAccount)) {
    Write-Host "ERROR: Storage account name not provided" -ForegroundColor Red
    Write-Host "Usage: .\scripts\create_platform_containers.ps1 -StorageAccount <name>" -ForegroundColor Yellow
    Write-Host "   OR: Set AZURE_ACCOUNT_NAME environment variable" -ForegroundColor Yellow
    exit 1
}

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "Creating Platform-Specific Azure Blob Storage Containers" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""
Write-Host "Storage Account: $StorageAccount" -ForegroundColor Green

if ($DryRun) {
    Write-Host "DRY-RUN MODE: No changes will be made" -ForegroundColor Yellow
    Write-Host ""
}

# Container definitions (name, public_access)
$containers = @(
    @{Name="crush-lu-media"; Access="blob"; Description="Crush.lu public media (events, branding)"},
    @{Name="crush-lu-private"; Access="off"; Description="Crush.lu private user photos (SAS tokens)"},
    @{Name="vinsdelux-media"; Access="blob"; Description="VinsDelux public media (products, producers)"},
    @{Name="vinsdelux-private"; Access="off"; Description="VinsDelux private documents (future)"},
    @{Name="entreprinder-media"; Access="blob"; Description="Entreprinder public media (defaults, branding)"},
    @{Name="powerup-media"; Access="blob"; Description="PowerUP public media (delegations, branding)"},
    @{Name="powerup-finops"; Access="off"; Description="PowerUP FinOps cost exports (private)"},
    @{Name="shared-media"; Access="blob"; Description="Shared cross-platform assets"}
)

# Verify Azure CLI is installed
try {
    $azVersion = az version --output json | ConvertFrom-Json
    Write-Host "Azure CLI version: $($azVersion.'azure-cli')" -ForegroundColor Gray
    Write-Host ""
} catch {
    Write-Host "ERROR: Azure CLI not found. Please install from https://aka.ms/installazurecli" -ForegroundColor Red
    exit 1
}

# Verify logged in to Azure
$account = az account show 2>$null
if (-not $account) {
    Write-Host "ERROR: Not logged in to Azure. Run 'az login' first" -ForegroundColor Red
    exit 1
}

$accountInfo = $account | ConvertFrom-Json
Write-Host "Azure Subscription: $($accountInfo.name)" -ForegroundColor Gray
Write-Host "Tenant: $($accountInfo.tenantId)" -ForegroundColor Gray
Write-Host ""

# Create containers
$stats = @{
    Created = 0
    Exists = 0
    Failed = 0
}

foreach ($container in $containers) {
    $name = $container.Name
    $access = $container.Access
    $description = $container.Description

    Write-Host "Processing: $name" -ForegroundColor White
    Write-Host "  Description: $description" -ForegroundColor Gray
    Write-Host "  Access Level: $access" -ForegroundColor Gray

    if ($DryRun) {
        Write-Host "  [DRY-RUN] Would create container" -ForegroundColor Yellow
        $stats.Created++
        continue
    }

    # Check if container already exists
    $exists = az storage container exists `
        --account-name $StorageAccount `
        --name $name `
        --output json 2>$null | ConvertFrom-Json

    if ($exists.exists) {
        Write-Host "  Status: Already exists (skipped)" -ForegroundColor Yellow
        $stats.Exists++
    } else {
        # Create container
        try {
            $result = az storage container create `
                --account-name $StorageAccount `
                --name $name `
                --public-access $access `
                --output json 2>&1

            if ($LASTEXITCODE -eq 0) {
                Write-Host "  Status: Created successfully" -ForegroundColor Green
                $stats.Created++
            } else {
                Write-Host "  Status: Failed - $result" -ForegroundColor Red
                $stats.Failed++
            }
        } catch {
            Write-Host "  Status: Failed - $($_.Exception.Message)" -ForegroundColor Red
            $stats.Failed++
        }
    }

    Write-Host ""
}

# Print summary
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "Summary" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "Created:         $($stats.Created)" -ForegroundColor Green
Write-Host "Already Exists:  $($stats.Exists)" -ForegroundColor Yellow
Write-Host "Failed:          $($stats.Failed)" -ForegroundColor Red
Write-Host ""

if ($stats.Failed -gt 0) {
    Write-Host "ERROR: Some containers failed to create" -ForegroundColor Red
    exit 1
} else {
    Write-Host "SUCCESS: All containers ready" -ForegroundColor Green
}

# Optional: Set lifecycle management policies
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Cyan
Write-Host "1. Run migration script to copy blobs from 'media' container" -ForegroundColor White
Write-Host "   python scripts/migrate_to_platform_containers.py --dry-run" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Configure lifecycle management policies in Azure Portal" -ForegroundColor White
Write-Host "   Storage Account > Data management > Lifecycle management" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Update Django STORAGES configuration (already done)" -ForegroundColor White
Write-Host "   azureproject/settings.py, azureproject/production.py" -ForegroundColor Gray
