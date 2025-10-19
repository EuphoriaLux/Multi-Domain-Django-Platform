# Azure Deployment Speed Optimization Guide

## Current Performance
- **Current deployment time**: ~5 minutes
- **Target deployment time**: ~3 minutes
- **Bottleneck**: Azure-side build and container restart

## ✅ Already Optimized

Your deployment is **already highly optimized**:

1. ✅ Minimal GitHub Actions workflow (no Python setup, no pip install)
2. ✅ Azure zip deployment with Oryx build
3. ✅ OIDC federated identity (no secrets)
4. ✅ Path filtering for docs/readme changes
5. ✅ Gunicorn preload and multi-worker setup
6. ✅ Efficient startup script with conditional data loading

## 🚀 Additional Optimization Strategies

### Strategy 1: Enable Dependency Caching (Recommended)

Azure App Service can cache pip dependencies between deployments:

```bash
az webapp config appsettings set \
  --name 'django-app-ajfffwjb5ie3s-app-service' \
  --resource-group 'django-app-ajfffwjb5ie3s' \
  --settings \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    ENABLE_ORYX_BUILD=true \
    ORYX_DISABLE_PIP_CACHE=false

# Expected improvement: -30 to -60 seconds
```

### Strategy 2: Optimize Requirements.txt

Split requirements into production and development:

**requirements-prod.txt** (minimal production dependencies):
```
Django>=5.1,<5.2
psycopg2-binary>=2.9
gunicorn>=23.0
whitenoise>=6.7
django-allauth>=0.63
djangorestframework>=3.15
# ... only production essentials
```

**requirements-dev.txt** (development only):
```
-r requirements-prod.txt
flake8
pytest
selenium
# ... dev/test dependencies
```

Update `startup.sh` or Bicep to use `requirements-prod.txt`:
```bash
pip install -r requirements-prod.txt
```

**Expected improvement**: -15 to -30 seconds

### Strategy 3: Pre-build Static Files (Advanced)

Instead of collecting static files on Azure, pre-collect locally and commit:

```bash
# In .github/workflows/deploy-azure-app-service-optimized.yml
- name: Collect static files
  run: |
    python -m pip install -r requirements.txt
    python manage.py collectstatic --noinput --clear

# Then deploy with static files included
```

**Expected improvement**: -20 to -40 seconds
**Trade-off**: Larger repository size, slower git operations

### Strategy 4: Use Deployment Slots with Swap

Set up a staging slot for zero-downtime deployments:

```bash
# Create staging slot
az webapp deployment slot create \
  --name django-app-ajfffwjb5ie3s-app-service \
  --resource-group django-app-ajfffwjb5ie3s \
  --slot staging

# Deploy to staging, then swap (instant)
az webapp deployment slot swap \
  --name django-app-ajfffwjb5ie3s-app-service \
  --resource-group django-app-ajfffwjb5ie3s \
  --slot staging \
  --target-slot production
```

**Expected improvement**: Instant user-facing deployment (build happens in background)
**Cost**: Additional App Service slot (~50% of main app cost)

### Strategy 5: Optimize Database Migrations

Only run migrations when models actually changed:

**Option A**: Use GitHub Actions to detect migration changes:
```yaml
- name: Check for new migrations
  id: check-migrations
  run: |
    if git diff --name-only HEAD~1 HEAD | grep -q "migrations/"; then
      echo "has_migrations=true" >> $GITHUB_OUTPUT
    else
      echo "has_migrations=false" >> $GITHUB_OUTPUT
    fi

- name: Run migrations
  if: steps.check-migrations.outputs.has_migrations == 'true'
  run: |
    az webapp ssh --name django-app-ajfffwjb5ie3s-app-service \
      --resource-group django-app-ajfffwjb5ie3s \
      --command "python manage.py migrate --no-input"
```

**Expected improvement**: -10 to -20 seconds (when no migrations)

### Strategy 6: Increase App Service Plan Tier

Your current tier affects build speed:

```bash
# Check current tier
az appservice plan show \
  --name your-plan-name \
  --resource-group django-app-ajfffwjb5ie3s \
  --query "sku.tier"

# Upgrade to faster tier (if on Basic)
az appservice plan update \
  --name your-plan-name \
  --resource-group django-app-ajfffwjb5ie3s \
  --sku S1  # Standard tier (faster CPU)
```

**Expected improvement**: -30 to -60 seconds
**Cost**: ~$70/month for S1 vs ~$55/month for B1

### Strategy 7: Use Azure Container Registry (Advanced)

Build Docker container locally/in CI, push to ACR, deploy pre-built image:

**Benefits**:
- Full control over build process
- Instant deployment (pre-built image)
- Consistent environments
- Can use Docker layer caching

**Expected improvement**: -60 to -120 seconds
**Complexity**: Medium to High

## 📊 Recommended Quick Wins

For immediate improvement with minimal effort:

1. **Enable dependency caching** (Strategy 1) - 5 minutes setup, -30-60s deployment
2. **Split requirements.txt** (Strategy 2) - 15 minutes setup, -15-30s deployment
3. **Optimize migrations check** (Strategy 5) - 10 minutes setup, -10-20s when no migrations

**Combined expected improvement**: ~2-3 minutes faster (**target: ~3min total**)

## 🎯 Implementation Priority

### High Priority (Do Now)
- [ ] Enable Oryx build caching (Strategy 1)
- [ ] Review and optimize requirements.txt (Strategy 2)

### Medium Priority (Next Sprint)
- [ ] Set up staging slot for zero-downtime (Strategy 4)
- [ ] Optimize migration detection (Strategy 5)

### Low Priority (Future Optimization)
- [ ] Consider containerization with ACR (Strategy 7)
- [ ] Evaluate App Service tier upgrade (Strategy 6)

## 📝 Monitoring Deployment Performance

Track deployment times in GitHub Actions:

```yaml
- name: Record deployment time
  run: |
    echo "⏱️ Deployment completed in: ${{ steps.deploy-to-webapp.outputs.deployment-time }}"
```

Monitor Azure build logs:
```bash
az webapp log tail \
  --name django-app-ajfffwjb5ie3s-app-service \
  --resource-group django-app-ajfffwjb5ie3s
```

## 🔍 Current Deployment Breakdown

Typical 5-minute deployment:
- GitHub Actions checkout: ~10s
- Azure login: ~5s
- Zip upload: ~15s
- **Azure Oryx build**: ~2-3 min (main bottleneck)
  - Extract zip: ~10s
  - Install dependencies: ~90-120s
  - Collect static files: ~20-30s
  - Run migrations: ~10-20s
- **Container restart**: ~60-90s (second bottleneck)
- Health check stabilization: ~20s

## 🎉 Expected Results After Optimization

With recommended quick wins implemented:

- GitHub Actions checkout: ~10s
- Azure login: ~5s
- Zip upload: ~15s
- **Azure Oryx build**: ~60-90s (cached deps, prod-only requirements)
- **Container restart**: ~60s
- Health check: ~10s

**Total: ~2.5-3 minutes** ✅

---

*Last updated: 2025-10-19*
*Your deployment is already well-optimized. These strategies provide incremental improvements.*
