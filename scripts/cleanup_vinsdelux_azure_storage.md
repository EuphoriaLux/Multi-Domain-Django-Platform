# VinsDelux Azure Blob Storage Cleanup Guide

## Overview
After simplifying VinsDelux to a portfolio-only site, we need to clean up unused images from Azure Blob Storage.

**Storage Account:** `mediabjnukuybtvjdy`
**Container:** `media`
**Subscription:** `64c21818-0806-461a-919c-1c02b989a2d1`

---

## Step 1: Upload WebP Journey Images

Upload the newly converted WebP journey images to Azure Blob Storage:

```powershell
# Navigate to project root
cd C:\Users\User\Github-Local\Multi-Domain-Django-Platform

# Upload each WebP file
az storage blob upload `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/journey/step_01.webp" `
  --file "vinsdelux\static\vinsdelux\images\journey\step_01.webp" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"

az storage blob upload `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/journey/step_02.webp" `
  --file "vinsdelux\static\vinsdelux\images\journey\step_02.webp" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"

az storage blob upload `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/journey/step_03.webp" `
  --file "vinsdelux\static\vinsdelux\images\journey\step_03.webp" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"

az storage blob upload `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/journey/step_04.webp" `
  --file "vinsdelux\static\vinsdelux\images\journey\step_04.webp" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"

az storage blob upload `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/journey/step_05.webp" `
  --file "vinsdelux\static\vinsdelux\images\journey\step_05.webp" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"
```

**Or upload all at once:**
```powershell
az storage blob upload-batch `
  --account-name mediabjnukuybtvjdy `
  --destination media `
  --destination-path "vinsdelux/journey" `
  --source "vinsdelux\static\vinsdelux\images\journey" `
  --pattern "*.webp" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"
```

---

## Step 2: Archive Unused Vineyard Images (Optional - Save Before Delete)

Before deleting, you may want to download the vineyard images as a backup:

```powershell
# Create backup directory
mkdir backup\vinsdelux_vineyard_images

# Download all vineyard images
az storage blob download-batch `
  --account-name mediabjnukuybtvjdy `
  --source media `
  --source-path "vinsdelux/vineyard-defaults" `
  --destination "backup\vinsdelux_vineyard_images" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"
```

---

## Step 3: Delete Unused Vineyard Images

**⚠️ WARNING:** This permanently deletes the vineyard default images. Make sure you have a backup if needed.

### Delete individual files:
```powershell
az storage blob delete `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/vineyard-defaults/vineyard_01.jpg" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"

az storage blob delete `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/vineyard-defaults/vineyard_02.jpg" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"

az storage blob delete `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/vineyard-defaults/vineyard_03.jpg" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"

az storage blob delete `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/vineyard-defaults/vineyard_04.jpg" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"

az storage blob delete `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --name "vinsdelux/vineyard-defaults/vineyard_05.jpg" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"
```

### Or delete all at once using Azure Portal:
1. Go to https://portal.azure.com
2. Navigate to Storage Account: `mediabjnukuybtvjdy`
3. Click "Containers" → "media"
4. Browse to folder: `vinsdelux/vineyard-defaults/`
5. Select all vineyard_*.jpg files
6. Click "Delete" button

---

## Step 4: Verify Cleanup

Check remaining VinsDelux blobs:

```powershell
# List journey images (should include new WebP files)
az storage blob list `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --prefix "vinsdelux/journey/" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1" `
  --output table

# Verify vineyard-defaults is empty (should return nothing)
az storage blob list `
  --account-name mediabjnukuybtvjdy `
  --container-name media `
  --prefix "vinsdelux/vineyard-defaults/" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1" `
  --output table
```

---

## Step 5: Test Production Site

After cleanup, test the production site to ensure images load correctly:

1. Visit: https://django-app-ajfffwjb5ie3s-app-service.azurewebsites.net
2. Check browser console for any 404 errors
3. Verify journey images display properly
4. Check that WebP images load (with PNG fallback for older browsers)

---

## Summary

### Before Cleanup:
- Journey PNG images: 5 files × ~1.3 MB = **6.44 MB**
- Vineyard JPG images: 5 files × ~1.3 MB = **6.44 MB**
- **Total: 12.89 MB**

### After Cleanup:
- Journey PNG images: 5 files × ~1.3 MB = **6.44 MB** (keep as fallback)
- Journey WebP images: 5 files × ~75 KB = **0.37 MB** (new!)
- Vineyard JPG images: **0 MB** (deleted)
- **Total: 6.81 MB**

### Savings:
- Removed unused vineyard images: **-6.44 MB**
- Added optimized WebP: **+0.37 MB**
- **Net savings: ~6 MB (47% reduction)**

### User Experience:
- Modern browsers load tiny WebP files (0.37 MB total)
- Old browsers fall back to PNG (6.44 MB)
- Page load dramatically faster for 95%+ of users

---

## Rollback (If Needed)

If something goes wrong, restore from backup:

```powershell
# Re-upload vineyard images from backup
az storage blob upload-batch `
  --account-name mediabjnukuybtvjdy `
  --destination media `
  --destination-path "vinsdelux/vineyard-defaults" `
  --source "backup\vinsdelux_vineyard_images" `
  --subscription "64c21818-0806-461a-919c-1c02b989a2d1"
```

---

## Notes

- The production site already supports WebP with PNG fallback (see index.html template)
- No code changes needed - the `<picture>` element handles browser compatibility
- PNG files are kept as fallback for older browsers
- Vineyard images were only used by the plot selector (now removed)
