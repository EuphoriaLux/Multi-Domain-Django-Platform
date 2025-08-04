# 🚀 Azure Deployment Guide - Vins de Lux Multi-Domain Django App

## 📋 Overview

This guide covers the complete deployment process for your multi-domain Django application with the new Vins de Lux product catalog improvements to Azure App Service with PostgreSQL and Blob Storage.

### **Architecture Components:**
- **Azure App Service** (Python 3.11) with multi-domain support
- **Azure Database for PostgreSQL** (Flexible Server)
- **Azure Blob Storage** for media files (product images)
- **Azure Application Insights** for monitoring
- **Virtual Network** with private endpoints for security

---

## 🔧 Pre-Deployment Setup

### **1. Prerequisites**
```bash
# Required tools
- Azure CLI (az)
- Azure Developer CLI (azd)
- Python 3.11
- Git
```

### **2. Environment Variables Required**
The following variables are automatically configured by the Bicep templates:

```bash
# Database Configuration
AZURE_POSTGRESQL_CONNECTIONSTRING=dbname=... host=... user=... password=...

# Azure Storage Configuration (FIXED in latest Bicep)
AZURE_ACCOUNT_NAME=media{uniqueString}
AZURE_ACCOUNT_KEY={auto-generated}
AZURE_CONTAINER_NAME=media

# Multi-Domain Configuration
CUSTOM_DOMAINS=www.powerup.lu,powerup.lu,vinsdelux.com,www.vinsdelux.com
WEBSITE_HOSTNAME={auto-assigned-by-azure}

# Security
SECRET_KEY={auto-generated}
FLASK_DEBUG=False
```

---

## 🚀 Deployment Process

### **Step 1: Deploy Infrastructure**
```bash
# Initialize Azure Developer CLI (first time only)
azd init

# Provision Azure resources
azd provision

# This creates:
# - Resource Group
# - App Service Plan (B1 SKU)
# - App Service with Python 3.11
# - PostgreSQL Flexible Server
# - Storage Account with 'media' container
# - Virtual Network with private endpoints
# - Application Insights
```

### **Step 2: Deploy Application Code**
```bash
# Deploy your Django application
azd deploy

# This process:
# 1. Builds the Python application
# 2. Runs startup.sh which executes migrations
# 3. Starts Gunicorn server
```

### **Step 3: Database Migration Process**

#### **Automatic Migration (via startup.sh)**
Your `startup.sh` automatically runs during deployment:
```bash
# /home/site/wwwroot/startup.sh
python manage.py migrate
gunicorn --workers 2 --threads 4 --timeout 60 --access-logfile \
    '-' --error-logfile '-' --bind=0.0.0.0:8000 \
     --chdir=/home/site/wwwroot azureproject.wsgi
```

#### **Existing Migrations Applied:**
- `vinsdelux.0001_initial` - Base models (Producers, Coffrets, Adoption Plans, etc.)
- `vinsdelux.0002_alter_vdladoptionplan_coffrets_per_year_and_more`
- `vinsdelux.0003_remove_vdladoptionplan_created_at_and_more`
- `vinsdelux.0004_remove_vdlproductimage_is_feature`

---

## 📊 Data Population Process

### **Step 1: Access App Service SSH**
```bash
# Get SSH access to your deployed app
az webapp ssh --resource-group YOUR_RESOURCE_GROUP --name YOUR_APP_NAME

# Or use the Azure Portal SSH console
```

### **Step 2: Populate Sample Data**
```bash
# Run the data population command
python manage.py populate_data

# This creates:
# - 5 Wine Producers (Château Margaux, Domaine de la Romanée-Conti, etc.)
# - 5 Wine Coffrets with pricing and descriptions
# - 5 Adoption Plans linked to coffrets
# - Product images (if available in storage)
```

### **Step 3: Test Azure Storage Upload** (Optional)
```bash
# Test if Azure Blob Storage is working
python manage.py test_azure_upload

# This verifies:
# - Storage account credentials
# - Container access
# - Upload permissions
```

---

## 🖼️ Image Management Process

### **Azure Blob Storage Configuration**

#### **Storage Structure:**
```
media/                          # Container name
├── producers/
│   ├── logos/
│   │   ├── producer1.png
│   │   └── producer2.png
│   └── photos/
│       ├── producer1.png
│       └── producer2.png
├── products/
│   ├── main/
│   │   ├── winebottle1.png
│   │   └── winebottle2.png
│   └── gallery/
│       ├── winebottle1.png
│       ├── winebottle2.png
│       └── winebottle3.png
└── homepage/
    └── hero-section.png
```

#### **Image Upload Methods:**

**1. Admin Panel Upload (Recommended for Production)**
- Navigate to `https://vinsdelux.com/admin/`
- Go to **Vinsdelux > Producers** or **Coffrets**
- Use the image upload fields
- Images automatically save to Azure Blob Storage

**2. Management Command Upload**
- Place images in the expected directory structure
- Run `python manage.py populate_data`
- Images are uploaded during data creation

**3. Direct Azure Storage Upload**
```bash
# Using Azure CLI
az storage blob upload \
  --account-name YOUR_STORAGE_ACCOUNT \
  --container-name media \
  --name products/gallery/winebottle1.png \
  --file ./local/path/to/winebottle1.png
```

### **Image URL Structure**
- **Production URL**: `https://{storage-account}.blob.core.windows.net/media/{path}`
- **Example**: `https://media123abc.blob.core.windows.net/media/products/gallery/winebottle1.png`

---

## 🌐 Multi-Domain Routing

### **Domain Configuration**
Your app automatically routes different domains to different URL configurations:

```python
# Domain Mapping (configured in Bicep)
vinsdelux.com       → azureproject.urls_vinsdelux (Vins de Lux app)
www.vinsdelux.com   → azureproject.urls_vinsdelux (Vins de Lux app)
powerup.lu          → azureproject.urls_powerup (PowerUp app)
www.powerup.lu      → azureproject.urls_powerup (PowerUp app)
{app-service}.azurewebsites.net → azureproject.urls_default (Entreprinder app)
```

### **Vins de Lux URLs (New Product Catalog)**
```
https://vinsdelux.com/                     # Homepage
https://vinsdelux.com/coffrets/            # Product catalog
https://vinsdelux.com/coffrets/{slug}/     # Product detail
https://vinsdelux.com/producers/           # Producer list
https://vinsdelux.com/producers/{slug}/    # Producer detail
https://vinsdelux.com/adoption-plans/{slug}/ # Adoption plan detail
https://vinsdelux.com/admin/               # Admin panel
```

---

## ✅ Post-Deployment Verification

### **Step 1: Test All Domains**
```bash
# Test domain routing
curl -I https://vinsdelux.com/
curl -I https://vinsdelux.com/coffrets/
curl -I https://vinsdelux.com/producers/
curl -I https://powerup.lu/
```

### **Step 2: Verify Database Connection**
```bash
# SSH into App Service
python manage.py dbshell

# Check tables exist
\dt vinsdelux_*
\dt entreprinder_*

# Check data
SELECT COUNT(*) FROM vinsdelux_vdlproducer;
SELECT COUNT(*) FROM vinsdelux_vdlcoffret;
```

### **Step 3: Test Image Loading**
```bash
# Check if images load properly
curl -I https://{storage-account}.blob.core.windows.net/media/products/gallery/winebottle1.png
```

### **Step 4: Admin Panel Access**
- Navigate to `https://vinsdelux.com/admin/`
- Create a superuser if needed: `python manage.py createsuperuser`
- Verify all models are accessible:
  - Producers
  - Coffrets
  - Adoption Plans
  - Product Images

---

## 🔧 Troubleshooting

### **Common Issues & Solutions**

#### **1. Images Not Loading**
```bash
# Check storage account configuration
az storage account show --name YOUR_STORAGE_ACCOUNT --resource-group YOUR_RG

# Verify container exists
az storage container show --name media --account-name YOUR_STORAGE_ACCOUNT

# Test upload permissions
python manage.py test_azure_upload
```

#### **2. Database Connection Issues**
```bash
# Check connection string
az webapp config appsettings list --name YOUR_APP --resource-group YOUR_RG | grep POSTGRESQL

# Test database connectivity
python manage.py dbshell
```

#### **3. Domain Routing Issues**
```bash
# Verify custom domains are set
az webapp config appsettings list --name YOUR_APP --resource-group YOUR_RG | grep CUSTOM_DOMAINS

# Check ALLOWED_HOSTS in production.py
```

#### **4. Migration Issues**
```bash
# Check migration status
python manage.py showmigrations

# Apply specific migration
python manage.py migrate vinsdelux 0004

# Create new migration if models changed
python manage.py makemigrations vinsdelux
python manage.py migrate
```

---

## 📊 Monitoring & Logs

### **Application Insights**
- **URL**: Available in Azure Portal
- **Metrics**: Response times, error rates, dependency calls
- **Custom Events**: Track product views, purchases

### **App Service Logs**
```bash
# Stream live logs
az webapp log tail --name YOUR_APP --resource-group YOUR_RG

# Download logs
az webapp log download --name YOUR_APP --resource-group YOUR_RG
```

### **PostgreSQL Monitoring**
- **Metrics**: Available in Azure Portal
- **Query Performance**: Use pg_stat_statements
- **Connection Monitoring**: Track active connections

---

## 🔄 Continuous Deployment

### **GitHub Actions (Optional)**
Set up automated deployment:
```yaml
# .github/workflows/azure-deploy.yml
name: Deploy to Azure
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - run: azd deploy
```

### **Manual Deployment**
```bash
# Deploy code changes
git push origin main
azd deploy

# Deploy infrastructure changes
azd provision
azd deploy
```

---

## 📈 Scaling Considerations

### **App Service Scaling**
```bash
# Scale up to higher SKU
az appservice plan update --sku S1 --name YOUR_PLAN --resource-group YOUR_RG

# Scale out (multiple instances)
az webapp scale set --number-of-instances 2 --name YOUR_APP --resource-group YOUR_RG
```

### **Database Scaling**
```bash
# Scale PostgreSQL
az postgres flexible-server update --sku-name Standard_B2s --name YOUR_DB --resource-group YOUR_RG
```

### **Storage Optimization**
- Use Azure CDN for static assets
- Consider Premium Storage for high-performance needs
- Implement image compression for product images

---

## 🔐 Security Considerations

### **SSL/TLS**
- **Automatic HTTPS**: Enabled by default
- **Custom Domain SSL**: Configure in Azure Portal
- **HSTS**: Configured in Django settings

### **Network Security**
- **Private Endpoints**: Database not publicly accessible
- **VNet Integration**: App Service communicates via private network
- **Firewall Rules**: Configure if needed

### **Secrets Management**
- **Key Vault Integration**: Consider for production secrets
- **Environment Variables**: Used for configuration
- **No Secrets in Code**: All credentials via environment variables

---

## 📞 Support Resources

### **Azure Documentation**
- [App Service Python](https://docs.microsoft.com/en-us/azure/app-service/quickstart-python)
- [PostgreSQL Flexible Server](https://docs.microsoft.com/en-us/azure/postgresql/flexible-server/)
- [Blob Storage](https://docs.microsoft.com/en-us/azure/storage/blobs/)

### **Django Resources**
- [Django Azure Storage](https://django-storages.readthedocs.io/en/latest/backends/azure.html)
- [Django PostgreSQL](https://docs.djangoproject.com/en/4.2/ref/databases/#postgresql-notes)

---

**Last Updated**: January 2025  
**Version**: 2.0 (With Vins de Lux Product Catalog Improvements)