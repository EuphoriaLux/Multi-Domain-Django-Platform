# 🖼️ Image Management Guide - Vins de Lux Azure Deployment

## 📋 Overview

This guide explains how images are handled in your Azure-deployed Vins de Lux application, including upload processes, storage structure, and troubleshooting.

---

## 🔧 Azure Storage Configuration

### **Storage Account Setup**
Your Bicep template automatically creates:
```bicep
// Storage Account with unique name
storageAccountName = 'media{uniqueString(resourceGroup().id)}'
mediaContainerName = 'media'

// Container with private access
properties: {
  publicAccess: 'None'  // Images accessed via Django auth
}
```

### **Environment Variables (Auto-configured)**
```bash
AZURE_ACCOUNT_NAME=media{unique-string}      # Storage account name
AZURE_ACCOUNT_KEY={auto-generated-key}       # Storage account key
AZURE_CONTAINER_NAME=media                   # Container name
```

### **Django Storage Configuration**
```python
# In production.py - STORAGES configuration
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.azure_storage.AzureStorage",
        "OPTIONS": {
            "account_name": os.getenv('AZURE_ACCOUNT_NAME'),
            "account_key": os.getenv('AZURE_ACCOUNT_KEY'),
            "azure_container": os.getenv('AZURE_CONTAINER_NAME'),
            "overwrite_files": True,
        },
    },
}

# Media URL construction
MEDIA_URL = f'https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/{AZURE_CONTAINER_NAME}/'
```

---

## 📁 Directory Structure

### **Expected Storage Structure**
```
Azure Blob Container: 'media'
├── producers/
│   ├── logos/              # Producer logo images
│   │   ├── producer1.png
│   │   ├── producer2.png
│   │   ├── producer3.png
│   │   ├── producer4.png
│   │   └── producer5.png
│   └── photos/             # Producer photography
│       ├── producer1.png
│       ├── producer2.png
│       ├── producer3.png
│       ├── producer4.png
│       └── producer5.png
│
├── products/
│   ├── main/               # Main product images
│   │   ├── winebottle1.png
│   │   ├── winebottle2.png
│   │   ├── winebottle3.png
│   │   ├── winebottle4.png
│   │   ├── winebottle5.png
│   │   ├── winebottle6.png
│   │   ├── winebottle7.png
│   │   └── winebottle8.png
│   └── gallery/            # Product gallery images
│       ├── winebottle1.png
│       ├── winebottle2.png
│       ├── winebottle3.png
│       ├── winebottle4.png
│       ├── winebottle5.png
│       ├── winebottle6.png
│       ├── winebottle7.png
│       └── winebottle8.png
│
├── categories/             # Category images (future use)
│   └── {category-images}
│
├── homepage/               # Homepage assets
│   ├── hero-section.png
│   └── {other-homepage-assets}
│
└── blog/                   # Blog post images (future use)
    └── {blog-images}
```

---

## 📤 Image Upload Methods

### **Method 1: Django Admin Panel (Recommended)**

**For Producers:**
1. Navigate to `https://vinsdelux.com/admin/vinsdelux/vdlproducer/`
2. Create or edit a producer
3. Upload images using the form fields:
   - **Logo**: Upload via `logo` field → saves to `producers/logos/`
   - **Producer Photo**: Upload via `producer_photo` field → saves to `producers/photos/`
4. Images automatically upload to Azure Blob Storage

**For Coffrets:**
1. Navigate to `https://vinsdelux.com/admin/vinsdelux/vdlcoffret/`
2. Create or edit a coffret
3. Add images via the **Product Images** inline:
   - Upload image files
   - Set alt text for SEO
   - Images save to `products/gallery/` or `products/main/`

**For Adoption Plans:**
1. Navigate to `https://vinsdelux.com/admin/vinsdelux/vdladoptionplan/`
2. Create or edit an adoption plan
3. Add images via the **Product Images** inline
4. Images automatically link to the adoption plan

### **Method 2: Management Command (Bulk Upload)**

**Prerequisites:**
```bash
# Ensure images exist in your local media directory
media/
├── producers/logos/producer1.png → producer5.png
├── products/gallery/winebottle1.png → winebottle8.png
└── homepage/producer1.png (for producer photos)
```

**Command:**
```bash
# SSH into Azure App Service
az webapp ssh --name YOUR_APP --resource-group YOUR_RG

# Run data population (includes image upload)
python manage.py populate_data
```

**What the command does:**
```python
# From populate_data.py
# 1. Creates producers with logo and photo uploads
with open(logo_path, 'rb') as logo_file:
    producer.logo.save(os.path.basename(logo_path), File(logo_file), save=False)

# 2. Creates coffrets with image uploads
VdlProductImage.objects.create(
    content_type=content_type,
    object_id=coffret.id,
    image=File(image_file, name=os.path.basename(image_path)),
    alt_text=coffret_name
)
```

### **Method 3: Direct Azure Storage Upload**

**Using Azure CLI:**
```bash
# Upload individual images
az storage blob upload \
  --account-name YOUR_STORAGE_ACCOUNT \
  --container-name media \
  --name producers/logos/new-producer.png \
  --file ./local/path/to/new-producer.png \
  --auth-mode key

# Upload multiple images
az storage blob upload-batch \
  --source ./local/images/ \
  --destination media/products/gallery/ \
  --account-name YOUR_STORAGE_ACCOUNT
```

**Using Azure Storage Explorer:**
1. Download Azure Storage Explorer
2. Connect using your storage account key
3. Navigate to the `media` container
4. Drag and drop files into appropriate folders

---

## 🔄 Image Processing Workflow

### **Upload Flow:**
```
User uploads image via Admin Panel
         ↓
Django receives file
         ↓
django-storages processes file
         ↓
File uploaded to Azure Blob Storage
         ↓
Database stores file path reference
         ↓
Template renders image URL
```

### **URL Generation:**
```python
# Model method (in models.py)
@property
def main_image(self):
    return self.images.first().image if self.images.exists() else None

# Template usage (in templates)
{% if coffret.main_image %}
    <img src="{{ coffret.main_image.url }}" alt="{{ coffret.name }}">
{% endif %}

# Generated URL format:
# https://media{unique}.blob.core.windows.net/media/products/gallery/winebottle1.png
```

---

## 🛠️ Troubleshooting

### **Issue 1: Images Not Uploading**

**Symptoms:**
- Upload forms show errors
- Images don't appear in storage
- Admin panel upload fails

**Diagnosis:**
```bash
# Check storage account configuration
python manage.py test_azure_upload

# Verify environment variables
python manage.py shell
>>> import os
>>> print(os.getenv('AZURE_ACCOUNT_NAME'))
>>> print(os.getenv('AZURE_CONTAINER_NAME'))
```

**Solutions:**
```bash
# Re-deploy with fixed Bicep template
azd provision  # Updates environment variables
azd deploy     # Applies changes

# Manually set environment variables if needed
az webapp config appsettings set \
  --name YOUR_APP \
  --resource-group YOUR_RG \
  --settings AZURE_ACCOUNT_NAME=your_storage_account
```

### **Issue 2: Images Not Loading on Website**

**Symptoms:**
- Broken image links on product pages
- 404 errors for image URLs
- Images upload but don't display

**Diagnosis:**
```bash
# Check if images exist in storage
az storage blob list \
  --container-name media \
  --account-name YOUR_STORAGE_ACCOUNT \
  --output table

# Test image URL directly
curl -I https://YOUR_STORAGE.blob.core.windows.net/media/products/gallery/winebottle1.png
```

**Solutions:**
```python
# Check MEDIA_URL in production.py
MEDIA_URL = f'https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/{AZURE_CONTAINER_NAME}/'

# Verify image field usage in templates
{% if product.main_image %}
    <img src="{{ product.main_image.url }}" alt="{{ product.name }}">
{% endif %}
```

### **Issue 3: Permission Issues**

**Symptoms:**
- Access denied errors
- Authentication failures
- Storage connection errors

**Solutions:**
```bash
# Check storage account access policies
az storage container show-permission \
  --name media \
  --account-name YOUR_STORAGE_ACCOUNT

# Verify storage account key
az storage account keys list \
  --name YOUR_STORAGE_ACCOUNT \
  --resource-group YOUR_RG
```

---

## 📊 Image Optimization Best Practices

### **File Formats:**
- **Products**: PNG or JPG (max 2MB)
- **Logos**: PNG with transparency (max 500KB)
- **Hero Images**: JPG (max 5MB)

### **Recommended Sizes:**
```python
# Product images
PRODUCT_IMAGE_SIZE = (800, 600)    # Main product display
PRODUCT_THUMB_SIZE = (300, 225)    # Thumbnail/grid view

# Producer logos
LOGO_SIZE = (200, 200)             # Square format preferred

# Producer photos
PRODUCER_PHOTO_SIZE = (600, 400)   # Landscape format
```

### **Performance Optimization:**
```python
# Consider adding image processing
from PIL import Image
from django.core.files.uploadedfile import InMemoryUploadedFile

def optimize_image(image_field):
    img = Image.open(image_field)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img.thumbnail((800, 600), Image.Resampling.LANCZOS)
    # Save optimized version
```

### **CDN Integration (Future Enhancement):**
```python
# Consider Azure CDN for better performance
AZURE_CDN_ENDPOINT = 'https://your-cdn.azureedge.net'
MEDIA_URL = f'{AZURE_CDN_ENDPOINT}/media/'
```

---

## 🔐 Security Considerations

### **Access Control:**
- **Container Access**: Private (no direct public access)
- **Authentication**: Via Django/Azure Storage keys
- **File Validation**: Implement file type/size validation

### **File Upload Security:**
```python
# Add to Django settings for security
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB

# Validate file types
ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp']
```

### **Content Validation:**
```python
# Example validator
def validate_image_file(file):
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise ValidationError('Unsupported file type')
    if file.size > 5 * 1024 * 1024:  # 5MB
        raise ValidationError('File too large')
```

---

## 📈 Monitoring & Analytics

### **Storage Metrics:**
- **Blob Count**: Track number of uploaded images
- **Storage Usage**: Monitor container size
- **Access Patterns**: Analyze frequently accessed images

### **Application Insights:**
```python
# Track image upload events
from applicationinsights import TelemetryClient
tc = TelemetryClient('your-instrumentation-key')

def track_image_upload(image_type, file_size):
    tc.track_event('ImageUpload', {
        'type': image_type,
        'size': file_size
    })
```

---

## 🎯 Quick Reference Commands

```bash
# Test storage connectivity
python manage.py test_azure_upload

# Populate sample data with images
python manage.py populate_data

# Check storage account details
az storage account show --name YOUR_STORAGE --resource-group YOUR_RG

# List all images in container
az storage blob list --container-name media --account-name YOUR_STORAGE

# Upload single image
az storage blob upload --file image.png --name products/gallery/image.png \
  --container-name media --account-name YOUR_STORAGE

# Download all images (backup)
az storage blob download-batch --source media --destination ./backup/ \
  --account-name YOUR_STORAGE
```

---

**Last Updated**: January 2025  
**Related Documentation**: [DEPLOYMENT.md](./DEPLOYMENT.md)