# FinOps Hub: Subscription ID Tracking Feature

## Overview

This feature adds subscription ID tracking to the FinOps Hub import system, with a prominent call-to-action (CTA) for exports that completed with 0 records. This helps resolve the common issue where exports fail to import data due to missing subscription IDs.

## What's New

### 1. Enhanced Data Model

**New Fields in `CostExport` model:**
- `subscription_id` (CharField, 100): Azure subscription GUID
- `needs_subscription_id` (BooleanField): Flag for incomplete imports
- `import_status` choices: Added 'superseded' status for replaced exports

**New Model Methods:**
- `is_incomplete()`: Check if export completed with 0 records and no subscription ID
- `get_incomplete_exports()`: Class method to query all incomplete exports

### 2. Improved Import Dashboard

**Location:** `/finops/import/`

**New Features:**
- **Prominent Warning Alert**: Shows at the top when incomplete exports exist
- **Quick Action Table**: Lists all incomplete imports with "Add ID" buttons
- **Clean UI**: Incomplete exports separated from regular import history
- **Visual Indicators**: Bootstrap warning styling with exclamation icon

**Screenshot Flow:**
```
┌─────────────────────────────────────────────────────────┐
│ ⚠ Action Required: Complete Import Setup               │
│                                                         │
│ 2 exports completed with 0 records.                    │
│ Quick fix: Add the Azure subscription ID below.        │
│                                                         │
│ ┌─────────────────────────────────────────────────┐   │
│ │ Subscription Name │ Period    │ Action          │   │
│ ├──────────────────┼───────────┼─────────────────┤   │
│ │ Pay as you go    │ 2025-10-01│ [Add ID] button │   │
│ │ PartnerLed       │ 2025-10-01│ [Add ID] button │   │
│ └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 3. Subscription ID Entry Form

**Location:** `/finops/import/<export_id>/update-subscription/`

**Features:**
- Clean, simple form with one field: Azure Subscription ID
- GUID pattern validation (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
- Helpful sidebar with instructions
- Export details card showing context
- Next steps guidance

**User Flow:**
1. User clicks "Add ID" from import dashboard
2. Form shows export details and help text
3. User finds subscription ID in Azure Portal
4. User enters GUID and submits
5. Success message redirects to import dashboard
6. User can re-run import with `--force` flag

### 4. Auto-Extract Subscription IDs

**Enhancement:** Import command now automatically extracts subscription IDs from CSV data

**Logic:**
```python
# During CSV parsing:
if not subscription_id_found and parsed.get('sub_account_id'):
    subscription_id_found = parsed['sub_account_id']

# After import:
if subscription_id_found:
    cost_export.subscription_id = subscription_id_found
    cost_export.save()
```

**Benefits:**
- Reduces manual data entry
- Future imports for same subscription auto-populate
- Still allows manual override via form

### 5. Enhanced Admin Interface

**Django Admin Updates:**
- Added `subscription_id` to list display
- Added `needs_subscription_id` filter
- Included fields in fieldsets
- Search by subscription ID enabled

## Deployment Instructions

### Step 1: Apply Migrations (CRITICAL - Run on Azure)

```bash
# SSH into Azure App Service
ssh your-app-service

# Activate virtual environment
source antenv/bin/activate

# Apply migrations
python manage.py migrate finops_hub

# Expected output:
# Running migrations:
#   Applying finops_hub.0003_add_subscription_id_tracking... OK
```

### Step 2: Verify Current State

```bash
# Check for incomplete imports
python manage.py shell -c "
from finops_hub.models import CostExport
incomplete = CostExport.get_incomplete_exports()
print(f'Incomplete exports: {incomplete.count()}')
for exp in incomplete:
    print(f'  - {exp.subscription_name}: {exp.billing_period_start}')
"
```

### Step 3: Use the New Feature

#### Option A: Via Web Interface (Recommended)

1. Navigate to `/finops/import/` in your browser
2. You'll see the warning alert with incomplete exports
3. Click "Add ID" for each incomplete export
4. Enter the Azure subscription GUID from Azure Portal
5. Save and return to dashboard

#### Option B: Via Shell (Advanced)

```bash
python manage.py shell -c "
from finops_hub.models import CostExport

# Update specific export
export = CostExport.objects.get(subscription_name='Pay as you go - Tom Privat')
export.subscription_id = 'YOUR-SUBSCRIPTION-GUID-HERE'
export.needs_subscription_id = False
export.save()
print(f'Updated: {export.subscription_name}')
"
```

### Step 4: Re-Import Data

```bash
# Force re-import with new subscription IDs
python manage.py import_cost_data --force

# Expected output:
# Found 2 cost export file(s)
# 2 unprocessed export(s) to import
# [1/2] Processing: partnerled/...
#   → Extracted subscription ID: abc123...
#   ✓ Imported 1543 records (0 duplicates skipped)
# [2/2] Processing: Pay as you go...
#   → Extracted subscription ID: def456...
#   ✓ Imported 892 records (0 duplicates skipped)
```

### Step 5: Verify Dashboard

1. Navigate to `/finops/` to see the main dashboard
2. Check `/finops/subscriptions/` for subscription breakdown
3. Both subscriptions should now show cost data

## Technical Details

### Files Modified

1. **Models** ([finops_hub/models.py](finops_hub/models.py)):
   - Added `subscription_id`, `needs_subscription_id` fields
   - Added `is_incomplete()` and `get_incomplete_exports()` methods

2. **Forms** ([finops_hub/forms.py](finops_hub/forms.py)):
   - NEW: `SubscriptionIDForm` with GUID validation

3. **Views** ([finops_hub/views.py](finops_hub/views.py)):
   - Updated `trigger_import()` to pass incomplete exports
   - NEW: `update_subscription_id()` view for form handling

4. **URLs** ([finops_hub/urls.py](finops_hub/urls.py)):
   - Added route: `/import/<int:export_id>/update-subscription/`

5. **Templates**:
   - Updated [import.html](finops_hub/templates/finops_hub/import.html): Added CTA section
   - NEW: [update_subscription_id.html](finops_hub/templates/finops_hub/update_subscription_id.html): Subscription ID form

6. **Management Command** ([import_cost_data.py](finops_hub/management/commands/import_cost_data.py)):
   - Auto-extracts subscription IDs from CSV data
   - Flags exports with 0 records as needing subscription ID
   - Enhanced logging and warnings

7. **Admin** ([finops_hub/admin.py](finops_hub/admin.py)):
   - Added new fields to list display and filters
   - Updated fieldsets

8. **Migration** ([0003_add_subscription_id_tracking.py](finops_hub/migrations/0003_add_subscription_id_tracking.py)):
   - Adds `subscription_id` and `needs_subscription_id` fields
   - Updates `import_status` choices

## How to Find Azure Subscription IDs

### Method 1: Azure Portal (Easiest)
1. Go to https://portal.azure.com
2. Search "Subscriptions" in top search bar
3. Click on your subscription
4. Copy the "Subscription ID" field (GUID format)

### Method 2: Azure CLI
```bash
az account list --query "[].{Name:name, ID:id}" -o table
```

### Method 3: PowerShell
```powershell
Get-AzSubscription | Select-Object Name, Id
```

## Validation and Testing

### Test Scenarios

1. **New Import with 0 Records:**
   ```bash
   # Simulate by importing with wrong subscription filter
   python manage.py import_cost_data --subscription "NonExistent"
   # Should create export with needs_subscription_id=True
   ```

2. **Add Subscription ID:**
   - Visit `/finops/import/`
   - Verify warning alert appears
   - Click "Add ID" and submit form
   - Verify success message

3. **Re-Import:**
   ```bash
   python manage.py import_cost_data --force
   # Should now import records successfully
   ```

4. **Auto-Extract:**
   - Delete a CostExport record
   - Re-import: `python manage.py import_cost_data --force`
   - Verify subscription_id is auto-populated from CSV data

### Expected Behavior

| Scenario | Result |
|----------|--------|
| Import with valid data | `subscription_id` extracted, `needs_subscription_id=False` |
| Import with 0 records | `needs_subscription_id=True`, shows in CTA |
| User adds subscription ID | Flag cleared, export ready for re-import |
| Re-import after adding ID | Data imports successfully |

## Troubleshooting

### Issue: Migration Fails

**Error:** `django.db.utils.OperationalError: no such column: finops_hub_costexport.subscription_id`

**Solution:**
```bash
# Check migration status
python manage.py showmigrations finops_hub

# If 0003 is not applied:
python manage.py migrate finops_hub 0003
```

### Issue: Incomplete Exports Not Showing

**Check:**
```bash
python manage.py shell -c "
from finops_hub.models import CostExport
exports = CostExport.objects.filter(records_imported=0)
print(f'Zero-record exports: {exports.count()}')
incomplete = CostExport.get_incomplete_exports()
print(f'Flagged as incomplete: {incomplete.count()}')
"
```

**Fix:** Manually flag existing zero-record exports:
```bash
python manage.py shell -c "
from finops_hub.models import CostExport
updated = CostExport.objects.filter(
    import_status='completed',
    records_imported=0,
    subscription_id__isnull=True
).update(needs_subscription_id=True)
print(f'Flagged {updated} exports as incomplete')
"
```

### Issue: Form Not Saving

**Check permissions:** User must be authenticated
**Check URL:** Ensure export ID is correct
**Check logs:** Look for form validation errors

## Future Enhancements

1. **Bulk Update:** Allow updating multiple subscriptions at once
2. **API Endpoint:** Programmatic subscription ID updates
3. **Webhook Integration:** Auto-trigger re-import after ID submission
4. **Email Notifications:** Alert admins about incomplete imports
5. **Subscription ID Library:** Pre-populate common subscriptions

## Support

If you encounter issues:

1. Check Django logs: `tail -f /var/log/django.log`
2. Check migration status: `python manage.py showmigrations`
3. Test in Django shell: `python manage.py shell`
4. Review admin interface: `/admin/finops_hub/costexport/`

## Summary

This feature provides a clean, user-friendly workflow to:
- **Identify** exports that failed to import data (0 records)
- **Collect** missing subscription IDs through a simple form
- **Re-import** data successfully with correct metadata
- **Auto-extract** subscription IDs from future imports

The implementation follows Django best practices with:
- Database migrations for schema changes
- Form validation and user feedback
- Admin interface integration
- Management command enhancements
- Clean, accessible UI with Bootstrap 5
