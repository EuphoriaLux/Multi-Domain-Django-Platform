#!/bin/bash
#
# Deploy FinOps Daily Sync Function App to Azure
#
# Usage:
#   ./deploy-finops-function.sh
#   ./deploy-finops-function.sh --generate-token
#   ./deploy-finops-function.sh --resource-group my-rg --location eastus

set -e  # Exit on error

# Default configuration
RESOURCE_GROUP="django-app-rg"
FUNCTION_APP_NAME="finops-daily-sync"
LOCATION="westeurope"
STORAGE_ACCOUNT="finopssyncstorage"
DJANGO_WEBHOOK_URL="https://power-up.lu/finops/api/sync/"
GENERATE_TOKEN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --resource-group)
            RESOURCE_GROUP="$2"
            shift 2
            ;;
        --function-app-name)
            FUNCTION_APP_NAME="$2"
            shift 2
            ;;
        --location)
            LOCATION="$2"
            shift 2
            ;;
        --storage-account)
            STORAGE_ACCOUNT="$2"
            shift 2
            ;;
        --webhook-url)
            DJANGO_WEBHOOK_URL="$2"
            shift 2
            ;;
        --generate-token)
            GENERATE_TOKEN=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

echo -e "${MAGENTA}"
echo "========================================"
echo "  FinOps Function App Deployment"
echo "========================================"
echo -e "${NC}"

# Check Azure CLI
echo -e "${CYAN}ℹ Checking prerequisites...${NC}"
if ! command -v az &> /dev/null; then
    echo -e "${RED}✗ Azure CLI not found${NC}"
    echo "Please install: https://aka.ms/installazurecli"
    exit 1
fi
AZ_VERSION=$(az version --query '"azure-cli"' -o tsv)
echo -e "${GREEN}✓ Azure CLI version $AZ_VERSION detected${NC}"

# Check if logged in
echo -e "${CYAN}ℹ Checking Azure authentication...${NC}"
if ! az account show &> /dev/null; then
    echo -e "${YELLOW}⚠ Not logged in to Azure${NC}"
    echo -e "${CYAN}ℹ Logging in...${NC}"
    az login
fi

ACCOUNT_NAME=$(az account show --query "user.name" -o tsv)
SUBSCRIPTION_NAME=$(az account show --query "name" -o tsv)
SUBSCRIPTION_ID=$(az account show --query "id" -o tsv)
echo -e "${GREEN}✓ Logged in as: $ACCOUNT_NAME${NC}"
echo -e "${GREEN}✓ Subscription: $SUBSCRIPTION_NAME ($SUBSCRIPTION_ID)${NC}"

# Generate or validate sync token
if [ "$GENERATE_TOKEN" = true ]; then
    echo -e "${CYAN}ℹ Generating new SECRET_SYNC_TOKEN...${NC}"
    SECRET_SYNC_TOKEN=$(openssl rand -base64 32)
    echo -e "${GREEN}✓ Generated new token${NC}"
    echo -e "${YELLOW}⚠ IMPORTANT: Save this token and add it to your Django App Service!${NC}"
    echo -e "\n${YELLOW}SECRET_SYNC_TOKEN=$SECRET_SYNC_TOKEN${NC}\n"
else
    if [ -z "$SECRET_SYNC_TOKEN" ]; then
        echo -e "${RED}✗ SECRET_SYNC_TOKEN not set in environment${NC}"
        echo "Either set environment variable or run with --generate-token"
        echo "Example: export SECRET_SYNC_TOKEN='your-token-here'"
        exit 1
    fi
    echo -e "${GREEN}✓ Using SECRET_SYNC_TOKEN from environment${NC}"
fi

# Check if resource group exists
echo -e "${CYAN}ℹ Checking resource group '$RESOURCE_GROUP'...${NC}"
if ! az group exists --name "$RESOURCE_GROUP" | grep -q "true"; then
    echo -e "${YELLOW}⚠ Resource group does not exist, creating...${NC}"
    az group create --name "$RESOURCE_GROUP" --location "$LOCATION" > /dev/null
    echo -e "${GREEN}✓ Resource group created${NC}"
else
    echo -e "${GREEN}✓ Resource group exists${NC}"
fi

# Check if storage account exists
echo -e "${CYAN}ℹ Checking storage account '$STORAGE_ACCOUNT'...${NC}"
STORAGE_AVAILABLE=$(az storage account check-name --name "$STORAGE_ACCOUNT" --query "nameAvailable" -o tsv)
if [ "$STORAGE_AVAILABLE" = "true" ]; then
    echo -e "${CYAN}ℹ Creating storage account...${NC}"
    az storage account create \
        --name "$STORAGE_ACCOUNT" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --sku Standard_LRS \
        --kind StorageV2 > /dev/null
    echo -e "${GREEN}✓ Storage account created${NC}"
else
    echo -e "${GREEN}✓ Storage account exists${NC}"
fi

# Get Application Insights connection string
echo -e "${CYAN}ℹ Getting Application Insights connection string...${NC}"
APP_SERVICE_NAME=$(az webapp list --resource-group "$RESOURCE_GROUP" --query "[?contains(name, 'django-app')].name" -o tsv | head -n1)
if [ -n "$APP_SERVICE_NAME" ]; then
    APP_INSIGHTS_CONN=$(az webapp config appsettings list \
        --name "$APP_SERVICE_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --query "[?name=='APPLICATIONINSIGHTS_CONNECTION_STRING'].value" -o tsv)

    if [ -n "$APP_INSIGHTS_CONN" ]; then
        echo -e "${GREEN}✓ Retrieved Application Insights connection string${NC}"
    else
        echo -e "${YELLOW}⚠ Could not retrieve Application Insights connection string${NC}"
        APP_INSIGHTS_CONN=""
    fi
else
    echo -e "${YELLOW}⚠ Main app service not found, skipping Application Insights config${NC}"
    APP_INSIGHTS_CONN=""
fi

# Get existing App Service Plan (shared with Django web app)
echo -e "${CYAN}ℹ Getting existing App Service Plan...${NC}"
PLAN_NAME=$(az appservice plan list \
    --resource-group "$RESOURCE_GROUP" \
    --query "[?kind=='linux'].name" -o tsv | head -n1)

if [ -n "$PLAN_NAME" ]; then
    echo -e "${GREEN}✓ Found existing App Service Plan: $PLAN_NAME${NC}"
else
    echo -e "${RED}✗ No Linux App Service Plan found in resource group $RESOURCE_GROUP${NC}"
    echo -e "${CYAN}ℹ Please ensure the web app's App Service Plan exists first${NC}"
    exit 1
fi

# Check if Function App exists
echo -e "${CYAN}ℹ Checking Function App '$FUNCTION_APP_NAME'...${NC}"
if ! az functionapp show --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" &> /dev/null; then
    echo -e "${CYAN}ℹ Creating Function App on Premium plan...${NC}"
    az functionapp create \
        --name "$FUNCTION_APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --storage-account "$STORAGE_ACCOUNT" \
        --runtime python \
        --runtime-version 3.11 \
        --functions-version 4 \
        --os-type Linux \
        --plan "$PLAN_NAME" > /dev/null
    echo -e "${GREEN}✓ Function App created${NC}"
else
    echo -e "${GREEN}✓ Function App exists${NC}"
fi

# Configure Function App settings
echo -e "${CYAN}ℹ Configuring Function App settings...${NC}"
SETTINGS=(
    "DJANGO_WEBHOOK_URL=$DJANGO_WEBHOOK_URL"
    "SECRET_SYNC_TOKEN=$SECRET_SYNC_TOKEN"
    "FINOPS_SYNC_ENABLED=true"
    "FUNCTIONS_WORKER_RUNTIME=python"
    "FUNCTIONS_EXTENSION_VERSION=~4"
)

if [ -n "$APP_INSIGHTS_CONN" ]; then
    SETTINGS+=("APPLICATIONINSIGHTS_CONNECTION_STRING=$APP_INSIGHTS_CONN")
fi

az functionapp config appsettings set \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --settings "${SETTINGS[@]}" > /dev/null

echo -e "${GREEN}✓ Function App settings configured${NC}"

# Deploy function code
echo -e "${CYAN}ℹ Deploying function code...${NC}"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
FUNCTION_PATH="$SCRIPT_DIR/../azure-functions/finops-daily-sync"

if [ ! -d "$FUNCTION_PATH" ]; then
    echo -e "${RED}✗ Function code not found at: $FUNCTION_PATH${NC}"
    exit 1
fi

cd "$FUNCTION_PATH"

# Install dependencies
echo -e "${CYAN}ℹ Installing Python dependencies...${NC}"
pip install -r requirements.txt --target .python_packages/lib/site-packages > /dev/null 2>&1

# Create deployment package
echo -e "${CYAN}ℹ Creating deployment package...${NC}"
TEMP_ZIP="/tmp/finops-function-$(date +%Y%m%d%H%M%S).zip"
zip -r "$TEMP_ZIP" . -x "*.git*" "*.vscode*" "__pycache__/*" "*.pyc" > /dev/null

# Deploy to Azure
echo -e "${CYAN}ℹ Uploading to Azure...${NC}"
az functionapp deployment source config-zip \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --src "$TEMP_ZIP" > /dev/null

# Cleanup
rm "$TEMP_ZIP"
rm -rf .python_packages

echo -e "${GREEN}✓ Function code deployed${NC}"

cd - > /dev/null

# Wait for deployment
echo -e "${CYAN}ℹ Waiting for deployment to complete...${NC}"
sleep 10

# Verify deployment
echo -e "${CYAN}ℹ Verifying deployment...${NC}"
FUNCTION_COUNT=$(az functionapp function list \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "length(@)" -o tsv)

if [ "$FUNCTION_COUNT" -gt 0 ]; then
    FUNCTION_NAME=$(az functionapp function list \
        --name "$FUNCTION_APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --query "[0].name" -o tsv)
    echo -e "${GREEN}✓ Function deployed successfully: $FUNCTION_NAME${NC}"
else
    echo -e "${YELLOW}⚠ Function not found - may still be deploying${NC}"
fi

# Display summary
echo -e "\n${GREEN}========================================"
echo "  Deployment Complete!"
echo "========================================${NC}\n"

echo -e "${CYAN}Function App Details:${NC}"
echo "  Name: $FUNCTION_APP_NAME"
echo "  Resource Group: $RESOURCE_GROUP"
echo "  Location: $LOCATION"
echo "  Runtime: Python 3.11"
echo "  Schedule: Daily at 3:00 AM UTC"

echo -e "\n${CYAN}Configuration:${NC}"
echo "  Webhook URL: $DJANGO_WEBHOOK_URL"
echo "  Sync Enabled: true"

if [ "$GENERATE_TOKEN" = true ]; then
    echo -e "\n${YELLOW}⚠ IMPORTANT: Add this token to your Django App Service!${NC}"
    echo -e "\n${YELLOW}Run this command:${NC}"
    echo "  az webapp config appsettings set \\"
    echo "    --name YOUR_APP_SERVICE_NAME \\"
    echo "    --resource-group $RESOURCE_GROUP \\"
    echo -e "    --settings SECRET_SYNC_TOKEN=$SECRET_SYNC_TOKEN\n"
fi

echo -e "${CYAN}Next Steps:${NC}"
echo "  1. Verify function in Azure Portal"
echo "  2. Test manual trigger"
echo "  3. Check logs: az webapp log tail --name $FUNCTION_APP_NAME --resource-group $RESOURCE_GROUP"
echo "  4. Monitor first scheduled run (3:00 AM UTC)"
echo ""

# Offer to get publish profile
read -p "Do you want to get the Function App publish profile for GitHub Actions? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${CYAN}ℹ Getting publish profile...${NC}"
    az functionapp deployment list-publishing-profiles \
        --name "$FUNCTION_APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --xml > finops-publish-profile.xml
    echo -e "${GREEN}✓ Publish profile saved to: finops-publish-profile.xml${NC}"
    echo -e "${CYAN}ℹ Add this to GitHub Secrets as: AZURE_FUNCTIONAPP_PUBLISH_PROFILE_FINOPS_SYNC${NC}"
fi

echo -e "\n${GREEN}✓ Deployment script completed successfully!${NC}\n"
