# Azure PostgreSQL SSH Tunnel Script
# This script creates a tunnel through your App Service to access PostgreSQL
# Keep this window open while using the database in VS Code

# Deactivate any virtual environment to use system Azure CLI
if ($env:VIRTUAL_ENV) {
    Write-Host "Deactivating virtual environment..." -ForegroundColor Yellow
    deactivate 2>$null
    # Remove venv from PATH
    $env:PATH = ($env:PATH -split ';' | Where-Object { $_ -notlike "*\.venv*" }) -join ';'
}

$resourceGroup = "django-app-rg"
$appName = "django-app-ajfffwjb5ie3s-app-service"
$postgresHost = "django-app-ajfffwjb5ie3s-postgres-server.postgres.database.azure.com"
$localPort = 5432
$sshPort = 2222

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Azure PostgreSQL Tunnel Connection" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This script will:" -ForegroundColor Yellow
Write-Host "1. Create a remote connection to your App Service"
Write-Host "2. Forward PostgreSQL traffic through the tunnel"
Write-Host ""
Write-Host "VS Code Connection Settings:" -ForegroundColor Green
Write-Host "  Server:   127.0.0.1"
Write-Host "  Port:     $localPort"
Write-Host "  Database: (your database name)"
Write-Host "  User:     (your username)"
Write-Host ""
Write-Host "Press Ctrl+C to close the tunnel when done."
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if logged into Azure
Write-Host "Checking Azure login status..." -ForegroundColor Yellow
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "Not logged in. Running 'az login'..." -ForegroundColor Red
    az login
}
Write-Host "Logged in as: $($account.user.name)" -ForegroundColor Green
Write-Host ""

# Start the remote connection in background
Write-Host "Starting App Service remote connection..." -ForegroundColor Yellow
$tunnelJob = Start-Job -ScriptBlock {
    param($rg, $app, $port)
    az webapp create-remote-connection --resource-group $rg --name $app --port $port 2>&1
} -ArgumentList $resourceGroup, $appName, $sshPort

# Wait for tunnel to be ready
Write-Host "Waiting for tunnel to be ready..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# Check if tunnel is ready
$tunnelOutput = Receive-Job -Job $tunnelJob -Keep
if ($tunnelOutput -match "SSH is available") {
    Write-Host "Tunnel is ready!" -ForegroundColor Green
} else {
    Write-Host "Tunnel output: $tunnelOutput" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Starting SSH port forwarding..." -ForegroundColor Yellow
Write-Host "Password is: Docker!" -ForegroundColor Magenta
Write-Host ""

# Run SSH with port forwarding (this will prompt for password)
ssh -L ${localPort}:${postgresHost}:5432 root@127.0.0.1 -m hmac-sha1 -p $sshPort -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null

# Cleanup
Write-Host ""
Write-Host "Cleaning up..." -ForegroundColor Yellow
Stop-Job -Job $tunnelJob
Remove-Job -Job $tunnelJob
Write-Host "Tunnel closed." -ForegroundColor Green
