# Crush.lu Private Photo Storage Setup

## Overview

Crush.lu profile photos use a separate **private Azure Blob Storage container** with time-limited SAS (Shared Access Signature) tokens for security. This prevents unauthorized access and photo scraping while your public media files remain accessible via CDN.

## Architecture

### Two Storage Containers

1. **Public CDN Container** (`media` or your `AZURE_CONTAINER_NAME`)
   - Access: Public (anonymous read)
   - Content: Wine images, vineyard photos, general assets
   - URLs: Direct public URLs

2. **Private Photos Container** (`crush-profiles-private`)
   - Access: Private (no anonymous access)
   - Content: Crush.lu user profile photos only
   - URLs: Time-limited SAS tokens (1-hour expiration)

## Azure Portal Setup

### Step 1: Create Private Container

1. Go to Azure Portal → Your Storage Account
2. Navigate to **Containers** under Data storage
3. Click **+ Container**
4. Settings:
   - **Name**: `crush-profiles-private`
   - **Public access level**: **Private (no anonymous access)**
5. Click **Create**

### Step 2: Verify Configuration

Your existing environment variables are already sufficient:
```bash
AZURE_ACCOUNT_NAME=<your-storage-account>
AZURE_ACCOUNT_KEY=<your-storage-key>
AZURE_CONTAINER_NAME=media  # Your public container (unchanged)
```

No additional environment variables needed! The private container is hardcoded in the storage backend.

## How It Works

### In Development (Local)
- Photos stored in local `media/crush_profiles/` directory
- Direct file access, no SAS tokens
- Automatic fallback when `AZURE_ACCOUNT_NAME` is not set

### In Production (Azure)
- Photos automatically routed to `crush-profiles-private` container
- Each photo URL includes a SAS token valid for 1 hour
- Tokens regenerated on each request for security

### Code Implementation

**Model Field** ([crush_lu/models.py:106](crush_lu/models.py#L106)):
```python
photo_1 = models.ImageField(
    upload_to='crush_profiles/',
    storage=crush_photo_storage  # Uses CrushProfilePhotoStorage in production
)
```

**Storage Backend** ([crush_lu/storage.py](crush_lu/storage.py)):
```python
class CrushProfilePhotoStorage(PrivateAzureStorage):
    azure_container = 'crush-profiles-private'  # Separate private container
    expiration_secs = 1800  # 30 minutes for profile photos
```

## Security Features

1. **Private Container**: No public access, even with direct URL
2. **SAS Tokens**: Time-limited read-only access
3. **UUID Filenames**: Prevents enumeration attacks
   - Format: `{uuid}_{original_name}`
   - Example: `a3f2d8b9_profile_photo.jpg`
4. **Short Expiration**: Tokens expire in 30 minutes
5. **No Caching**: Tokens regenerated on each request

## Testing

### Test in Development
```bash
# Upload a profile photo through Crush.lu profile creation
# Photo will be saved to: media/crush_profiles/
```

### Test in Production
```bash
# After deploying to Azure:
# 1. Upload a profile photo
# 2. Inspect the image URL in browser - should contain "?sv=" (SAS token)
# 3. Copy the URL and wait 31 minutes
# 4. Try accessing the URL - should fail (token expired)
```

## Migration

After setting up the container, create and run the migration:

```bash
python manage.py makemigrations crush_lu
python manage.py migrate crush_lu
```

**Note**: Existing photos in the public container will NOT be automatically moved. They will remain accessible at their current URLs. Only new uploads will use the private container.

## Troubleshooting

### Issue: Photos not uploading
- **Check**: Container `crush-profiles-private` exists in Azure Portal
- **Check**: `AZURE_ACCOUNT_KEY` has write permissions

### Issue: 404 errors on photo URLs
- **Check**: Container access level is **Private** (not Blob or Container)
- **Check**: `AZURE_ACCOUNT_NAME` and `AZURE_ACCOUNT_KEY` are correct

### Issue: "Invalid SAS token" errors
- **Check**: System clock is synchronized (SAS tokens are time-sensitive)
- **Check**: Azure Storage Account key hasn't been regenerated

### Issue: Photos work locally but not in production
- **Check**: Environment variable `AZURE_ACCOUNT_NAME` is set in Azure App Service
- **Check**: Container name spelling: `crush-profiles-private` (exact match)

## Migration Guide for Existing Photos

If you have existing Crush.lu photos in the public container and want to move them:

```bash
# Using Azure CLI
az storage blob copy start-batch \
  --account-name <your-account> \
  --account-key <your-key> \
  --source-container media \
  --destination-container crush-profiles-private \
  --pattern "crush_profiles/*"
```

Or use Azure Storage Explorer (GUI tool) for a manual copy.

## Environment Variables Summary

```bash
# Required (already configured)
AZURE_ACCOUNT_NAME=<your-storage-account>
AZURE_ACCOUNT_KEY=<your-storage-key>

# Public container (unchanged)
AZURE_CONTAINER_NAME=media

# Private container (hardcoded in code, no env var needed)
# Container name: crush-profiles-private
```

## Additional Notes

- **Performance**: SAS token generation is fast (< 1ms)
- **Cost**: Same storage cost as public container
- **Bandwidth**: No additional egress charges for SAS tokens
- **Compatibility**: Works with all Azure Storage tiers (Hot, Cool, Archive)
