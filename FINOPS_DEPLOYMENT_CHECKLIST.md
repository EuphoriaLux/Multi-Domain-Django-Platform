# FinOps Subscription ID Feature - Deployment Checklist

## Pre-Deployment (Local)

- [x] Model fields added (`subscription_id`, `needs_subscription_id`)
- [x] Model methods created (`is_incomplete()`, `get_incomplete_exports()`)
- [x] Form created (`SubscriptionIDForm`)
- [x] Views updated (`trigger_import`, `update_subscription_id`)
- [x] URLs configured
- [x] Templates created/updated
- [x] Import command enhanced
- [x] Admin interface updated
- [x] Migration file created (`0003_add_subscription_id_tracking.py`)
- [x] Documentation written

## Azure Deployment

### Step 1: Push Code to Git
```bash
git status
git add finops_hub/
git commit -m "Add subscription ID tracking feature for FinOps Hub

- Add subscription_id and needs_subscription_id fields to CostExport
- Create CTA in import dashboard for incomplete exports
- Add form to collect missing subscription IDs
- Auto-extract subscription IDs from CSV data
- Update admin interface with new fields
- Enhance import command with better logging"

git push origin main  # or your branch name
```

### Step 2: SSH into Azure
```bash
# Connect to Azure App Service
ssh <your-app-service-name>

# Navigate to project directory
cd /home/site/wwwroot
```

### Step 3: Pull Latest Code
```bash
# Pull from Git
git pull origin main

# OR if using Azure deployment
# Code may auto-deploy via GitHub Actions
# Wait for deployment to complete
```

### Step 4: Activate Virtual Environment
```bash
source antenv/bin/activate
```

### Step 5: Apply Migrations (CRITICAL)
```bash
python manage.py migrate finops_hub

# Expected output:
# Operations to perform:
#   Apply all migrations: finops_hub
# Running migrations:
#   Applying finops_hub.0003_add_subscription_id_tracking... OK
```

### Step 6: Verify Migration
```bash
python manage.py showmigrations finops_hub

# Should show:
# finops_hub
#  [X] 0001_initial
#  [X] 0002_add_record_hash_deduplication
#  [X] 0003_add_subscription_id_tracking  ← NEW
```

### Step 7: Check for Incomplete Imports
```bash
python manage.py shell -c "
from finops_hub.models import CostExport
incomplete = CostExport.get_incomplete_exports()
print(f'\nIncomplete exports: {incomplete.count()}\n')
for exp in incomplete:
    print(f'  - {exp.subscription_name}')
    print(f'    Period: {exp.billing_period_start} to {exp.billing_period_end}')
    print(f'    Records: {exp.records_imported}')
    print()
"
```

### Step 8: Flag Existing Zero-Record Imports (If Needed)
```bash
# If incomplete exports exist but aren't flagged:
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

### Step 9: Test Web Interface
1. Open browser: `https://<your-app>.azurewebsites.net/finops/import/`
2. Check for warning alert with incomplete exports
3. Click "Add ID" button
4. Verify form loads correctly
5. **Don't submit yet** - wait until you have subscription IDs ready

### Step 10: Collect Subscription IDs
```bash
# Option A: Azure CLI (if available on your machine)
az account list --query "[].{Name:name, ID:id}" -o table

# Option B: Azure Portal
# 1. Go to https://portal.azure.com
# 2. Search "Subscriptions"
# 3. Copy IDs for:
#    - PartnerLed-power_up
#    - Pay as you go - Tom Privat (or your subscription name)
```

### Step 11: Add Subscription IDs
Via web interface:
1. Go to `/finops/import/`
2. For each incomplete export, click "Add ID"
3. Paste the Azure subscription GUID
4. Click "Save Subscription ID"

OR via shell:
```bash
python manage.py shell -c "
from finops_hub.models import CostExport

# Update each export (replace with actual GUIDs)
exports = [
    ('PartnerLed-power_up', 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'),
    ('Pay as you go - Tom Privat', 'yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy'),
]

for sub_name, sub_id in exports:
    export = CostExport.objects.filter(
        subscription_name=sub_name,
        records_imported=0
    ).first()
    if export:
        export.subscription_id = sub_id
        export.needs_subscription_id = False
        export.save()
        print(f'✓ Updated: {sub_name}')
    else:
        print(f'✗ Not found: {sub_name}')
"
```

### Step 12: Re-Import Data
```bash
python manage.py import_cost_data --force

# Monitor output:
# - Should detect 2 export files
# - Should show "→ Extracted subscription ID: ..."
# - Should import records successfully
# - Should NOT show "0 unprocessed exports"
```

### Step 13: Verify Dashboard Data
```bash
# Check cost records by subscription
python manage.py shell -c "
from finops_hub.models import CostRecord
from django.db.models import Count, Sum

subs = CostRecord.objects.values('sub_account_name').annotate(
    total_records=Count('id'),
    total_cost=Sum('billed_cost')
).order_by('-total_records')

print('\nCost Records by Subscription:\n')
for sub in subs:
    print(f\"  {sub['sub_account_name']}\")
    print(f\"    Records: {sub['total_records']}\")
    print(f\"    Total Cost: {sub['total_cost']}\")
    print()
"
```

### Step 14: Refresh Aggregations
```bash
# This ensures dashboard shows latest data
python manage.py shell -c "
from finops_hub.utils.aggregation import CostAggregator
result = CostAggregator.refresh_all(days_back=60, currency='EUR')
print(f'Daily aggregations: {result[\"daily_aggregations\"]}')
print(f'Monthly aggregations: {result[\"monthly_aggregations\"]}')
print(f'Period: {result[\"period\"]}')
"
```

### Step 15: Final Verification
1. **Dashboard**: Visit `/finops/` - should show costs for all subscriptions
2. **Subscriptions**: Visit `/finops/subscriptions/` - should list all subscriptions
3. **Import Page**: Visit `/finops/import/` - warning should be gone
4. **Admin**: Visit `/admin/finops_hub/costexport/` - verify subscription IDs are populated

## Post-Deployment

### Collect Baseline Metrics
```bash
# Total exports
python manage.py shell -c "from finops_hub.models import CostExport; print(f'Total exports: {CostExport.objects.count()}')"

# Total cost records
python manage.py shell -c "from finops_hub.models import CostRecord; print(f'Total records: {CostRecord.objects.count()}')"

# Date range
python manage.py shell -c "
from finops_hub.models import CostRecord
from django.db.models import Min, Max
from django.db.models.functions import TruncDate
result = CostRecord.objects.aggregate(
    min=Min(TruncDate('charge_period_start')),
    max=Max(TruncDate('charge_period_start'))
)
print(f'Date range: {result[\"min\"]} to {result[\"max\"]}')
"
```

### Set Up Monitoring
- [ ] Bookmark `/finops/import/` for daily checks
- [ ] Schedule weekly re-imports: `python manage.py import_cost_data`
- [ ] Set up email alerts for failed imports (future enhancement)

### Documentation
- [ ] Share `/finops/faq/` with team
- [ ] Document subscription IDs in team wiki
- [ ] Create runbook for monthly imports

## Rollback Plan (If Needed)

If something goes wrong:

```bash
# Rollback migration
python manage.py migrate finops_hub 0002

# Revert code
git revert HEAD
git push origin main

# Clear cache
python manage.py shell -c "from django.core.cache import cache; cache.clear()"

# Restart app service
az webapp restart --name <your-app-name> --resource-group <your-rg>
```

## Success Criteria

- [x] Migration applied without errors
- [ ] Incomplete exports flagged (if any exist)
- [ ] Subscription IDs collected and saved
- [ ] Re-import completes successfully
- [ ] Dashboard shows data for all subscriptions
- [ ] No errors in application logs
- [ ] Import page shows no warnings
- [ ] Team can access and use the feature

## Timeline

- **Code Push**: 5 minutes
- **Azure Deployment**: 5-10 minutes (auto)
- **Migration**: 1 minute
- **Subscription ID Collection**: 5-10 minutes
- **Re-Import**: 5-15 minutes (depending on data volume)
- **Verification**: 5 minutes

**Total Estimated Time**: 30-45 minutes

## Support Contacts

- **Technical Issues**: Check Django logs at `/var/log/django.log`
- **Azure Issues**: Azure Portal > App Service > Logs
- **Database Issues**: Check migrations with `showmigrations`

## Notes

- **Backup**: Azure handles automatic backups
- **Downtime**: Zero downtime deployment
- **Data Loss Risk**: None - only adding fields
- **User Impact**: Positive - resolves import issues
