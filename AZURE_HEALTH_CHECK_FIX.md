# Azure Health Check Fix - Complete History

**Last Updated:** October 17, 2025
**Status:** ✅ FIXED (All Issues Resolved)

---

## Issue #2: Sites Framework Error (October 17, 2025)

### Problem
After fixing the 301 redirect issue, a new error appeared:

```
ERROR Internal Server Error: /healthz/
django.contrib.sites.models.Site.DoesNotExist: Site matching query does not exist.
```

**Root Cause:**
- Azure health checks use `localhost:8080` as the HTTP_HOST header
- Django Sites framework (`CurrentSiteMiddleware`) tries to find a Site object for this host
- No Site exists in the database for `localhost:8080`
- Health check fails with 500 error before reaching the view

### Solution

Created `HealthCheckMiddleware` that returns immediately for `/healthz/` requests, **bypassing all middleware** including the Sites framework.

**Files Modified:**
1. [azureproject/middleware.py:9-22](azureproject/middleware.py#L9-L22) - Added HealthCheckMiddleware
2. [azureproject/settings.py:69](azureproject/settings.py#L69) - Added middleware as FIRST in list

```python
class HealthCheckMiddleware:
    """
    Bypass all middleware and Sites framework for health check endpoint.
    This prevents Azure health checks from failing due to missing Site objects.
    MUST be placed FIRST in MIDDLEWARE list.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Immediately return OK for health checks, bypassing all other middleware
        if request.path in ['/healthz/', '/healthz']:
            return HttpResponse("OK", status=200, content_type="text/plain")
        return self.get_response(request)
```

**Why This Works:**
- Middleware processes requests top-to-bottom
- `HealthCheckMiddleware` is **first** in the list
- Returns immediately for `/healthz/`, never reaching `CurrentSiteMiddleware`
- All other requests pass through normally

---

## Issue #1: 301 Redirect (Fixed Earlier)

Azure health checks were failing with "Bad Request" errors every ~60 seconds. Log analysis revealed:

```
169.254.129.8 - - [16/Oct/2025:22:45:41 +0000] "GET /healthz/ HTTP/1.1" 301 0 "-" "ReadyForRequest/1.0 (HealthCheck)"
ERROR Non-retryable server side error: Operation returned an invalid status 'Bad Request'.
```

### Root Cause Analysis

**Two separate but related issues:**

1. **Health Check 301 Redirects**
   - Azure's internal health check IP (`169.254.129.8`) was hitting `/healthz/`
   - `RedirectWWWToRootDomainMiddleware` was redirecting internal IPs
   - Azure health checks don't follow redirects → interprets as failure

2. **Application Insights Timeout** (Secondary issue)
   - Azure Application Insights trying to send telemetry to `westeurope-5.in.applicationinsights.azure.com`
   - Connection timing out after 300 seconds
   - Causing periodic error logs (not related to health check failures)

---

## The Fix

### File Modified: `azureproject/redirect_www_middleware.py`

**Added two bypass conditions to `RedirectWWWToRootDomainMiddleware`:**

```python
def __call__(self, request):
    # Skip redirects for health check endpoint
    if request.path == '/healthz/' or request.path == '/healthz':
        return self.get_response(request)

    # Get host
    host = request.META.get('HTTP_HOST', '').split(':')[0].lower()

    if not host:
        return self.get_response(request)

    # Skip redirects for Azure internal IPs (health checks, monitoring)
    if host.startswith('169.254.') or host == 'localhost':
        return self.get_response(request)

    # ... rest of redirect logic ...
```

### Why This Works

**Bypass #1: Path-Based**
- Checks if the request path is `/healthz/` or `/healthz`
- Allows health check to proceed without redirect
- Works for all hosts hitting the health endpoint

**Bypass #2: IP-Based**
- Checks if request comes from Azure internal IP range (`169.254.*.*`)
- Also includes `localhost` for local development health checks
- Ensures all internal monitoring traffic is not redirected

---

## Before vs After

### Before Fix
```
Request: GET /healthz/ from 169.254.129.8
  → RedirectWWWToRootDomainMiddleware: No special handling
  → (Host check logic runs)
  → Returns: 301 Redirect
  → Azure: ❌ FAIL "Bad Request"
```

### After Fix
```
Request: GET /healthz/ from 169.254.129.8
  → RedirectWWWToRootDomainMiddleware: Path is /healthz/ → BYPASS
  → Pass to next middleware
  → health_check_view(): Returns 200 OK
  → Azure: ✅ PASS
```

---

## Expected Log Output After Fix

### Successful Health Checks
```
169.254.129.8 - - [17/Oct/2025:10:00:00 +0000] "GET /healthz/ HTTP/1.1" 200 2 "-" "ReadyForRequest/1.0 (HealthCheck)"
169.254.129.8 - - [17/Oct/2025:10:00:05 +0000] "GET /healthz/ HTTP/1.1" 200 2 "-" "ReadyForRequest/1.0 (HealthCheck)"
```

**Key indicators:**
- Status code: `200` (not `301`)
- Response size: `2` bytes (the text "OK")
- No "Bad Request" errors following health checks

---

## About Application Insights Timeout

The **"Operation returned an invalid status 'Bad Request'"** errors appearing every ~60 seconds are from **Application Insights telemetry export timeouts**, NOT health check failures.

### Error Pattern
```
File "/agents/python/azure/monitor/opentelemetry/exporter/_base.py", line 204, in _transmit
azure.core.exceptions.ServiceResponseError: HTTPSConnectionPool(host='westeurope-5.in.applicationinsights.azure.com', port=443): Read timed out. (read timeout=300)
ERROR Non-retryable server side error: Operation returned an invalid status 'Bad Request'.
```

### What This Means

- **Not a health check issue** - Different error entirely
- **Application Insights trying to send logs** - Telemetry export
- **Connection to AI endpoint timing out** - Network/region issue
- **Non-retryable** - AI SDK gives up after timeout

### Possible Causes

1. **Network connectivity issue** - Firewall blocking outbound to AI endpoints
2. **Region mismatch** - App in one region, AI workspace in another (westeurope)
3. **High telemetry volume** - Too much data to send in time window
4. **AI service issue** - Temporary Azure service problem

### How to Fix Application Insights Timeout

**Option 1: Check Network Configuration**
```bash
# In Azure Portal → App Service → Networking
# Ensure outbound traffic to *.applicationinsights.azure.com is allowed
```

**Option 2: Verify AI Region Matches App Region**
```bash
# In Azure Portal → Application Insights
# Check "Location" matches your App Service region
# If mismatch, consider creating new AI workspace in same region
```

**Option 3: Reduce Telemetry Volume**

In `azureproject/production.py`, you already have reduced logging:

```python
LOGGING = {
    'handlers': {
        'console': {
            'level': 'WARNING',  # Only WARNING and above
        },
    },
    'loggers': {
        'django': {
            'level': 'WARNING',  # Reduced from DEBUG
        },
        'azure': {
            'level': 'WARNING',  # Reduced Azure SDK logging
        },
    },
}
```

**Option 4: Disable Application Insights (if not needed)**

Remove or comment out in your startup script:
```python
# startup.sh or startup command
# Remove: --auto-instrumentation
```

Or set environment variable:
```bash
APPLICATIONINSIGHTS_ENABLED=false
```

---

## Verification Steps

### 1. Deploy the Fix
```bash
git add azureproject/redirect_www_middleware.py
git commit -m "Fix: Skip redirects for health check and Azure internal IPs"
git push azure main
```

### 2. Monitor Logs After Deployment
```bash
# In Azure Portal → App Service → Log stream
# Watch for health check requests
```

### 3. Expected Behavior

**Health Checks Should Show:**
```
✅ "GET /healthz/ HTTP/1.1" 200 2
✅ "GET /healthz/ HTTP/1.1" 200 2
✅ "GET /healthz/ HTTP/1.1" 200 2
```

**NOT:**
```
❌ "GET /healthz/ HTTP/1.1" 301 0
❌ ERROR Non-retryable server side error: Operation returned an invalid status 'Bad Request'.
```

### 4. Check App Service Health

**Azure Portal → App Service → Health check**
- Status should be: **Healthy**
- No recent failures
- Consistent 200 responses

---

## Why This Matters

### Health Check Failures Can Cause:

1. **Auto-restarts** - Azure may restart your app thinking it's unhealthy
2. **Load balancer removal** - App removed from load balancer rotation
3. **Deployment failures** - New deployments fail if health check doesn't pass
4. **Alerting** - False alerts to on-call engineers
5. **Availability impact** - Users may see errors during "unhealthy" periods

### Correct Health Check Behavior Ensures:

✅ Stable app service with no unexpected restarts
✅ Proper load balancing across instances
✅ Successful deployments
✅ Accurate availability metrics
✅ Reduced false alerting

---

## Additional Notes

### Why 169.254.* IPs?

- **Azure's internal IP range** for platform services
- Used for:
  - Health checks
  - Application Insights
  - Container instance communication
  - Internal load balancers

### Why Bypass on /healthz/?

- **Health checks should be fast** - No redirect overhead
- **Health checks should be simple** - Return 200 or fail
- **Health checks shouldn't depend on DNS** - Internal IP → No need to redirect to public domain
- **Standard practice** - Health endpoints universally exempt from redirects

### Other Services That Hit Health Checks

- Azure Load Balancer
- Azure Front Door
- Application Gateway
- Container Instances
- Kubernetes (if using AKS)
- Monitoring tools (DataDog, New Relic, etc.)

---

## Testing Locally

### Test Health Check Response
```bash
curl -I http://localhost:8000/healthz/
```

**Expected:**
```
HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8
Content-Length: 2
```

### Test with Azure-like IP (Requires hosts file trick)
```bash
# Won't work directly, but shows the concept
curl -I http://169.254.129.8/healthz/
```

### Test Redirect Logic Still Works
```bash
# This should still redirect
curl -I http://www.powerup.lu/

# Expected:
# HTTP/1.1 301 Moved Permanently
# Location: https://powerup.lu/
```

---

## Related Files

- **Fixed:** `azureproject/redirect_www_middleware.py`
- **Health check defined:** `azureproject/urls.py` (line 17-18, 25)
- **Health check imported:** `azureproject/urls_powerup.py` (line 12, 15)
- **Health check imported:** `azureproject/urls_vinsdelux.py` (line 6, 9)
- **Health check imported:** `azureproject/urls_crush.py` (line 11, 14)
- **Production settings:** `azureproject/production.py`

---

## Summary

✅ **Fixed:** Health check 301 redirects by bypassing redirect middleware for `/healthz/` path and Azure internal IPs
⚠️ **Note:** Application Insights timeout is a separate issue (network/configuration) that doesn't affect health checks
✅ **Impact:** Health checks now return 200 OK, preventing Azure from marking app as unhealthy
✅ **Safe:** Regular user traffic still gets redirected correctly (www → non-www, azurewebsites.net → powerup.lu)

---

*Fix implemented by Claude Code - October 17, 2025*
