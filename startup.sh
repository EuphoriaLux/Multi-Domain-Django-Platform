#!/bin/bash
# startup.sh is used by infra/resources.bicep to automate database migrations and start the application
set -e  # Exit on error

echo "🚀 Starting deployment..."
echo "📍 Working directory: $(pwd)"
echo "🐍 System Python: $(python --version) at $(which python)"

# Extract pre-built virtual environment if not already present
if [ ! -d "/antenv" ] && [ -f "antenv.tar.gz" ]; then
    echo "📦 Extracting pre-built virtual environment..."
    mkdir -p /antenv
    tar xzf antenv.tar.gz -C /antenv
    echo "✅ Virtual environment extracted to /antenv"
fi

# Fix nested extraction: Oryx may extract antenv.tar.gz into /antenv/antenv/
# (happens when tar contains antenv/ prefix and Oryx uses tar -C /antenv)
if [ -d "/antenv/antenv/bin" ] && [ ! -d "/antenv/bin" ]; then
    echo "🔧 Fixing nested venv extraction (Oryx created /antenv/antenv/)..."
    mv /antenv/antenv/* /antenv/
    rmdir /antenv/antenv 2>/dev/null || true
    echo "✅ Nested venv structure fixed"
fi

# Fix broken python symlinks — venv was built on GitHub Actions (different Python path)
# The symlinks point to /opt/hostedtoolcache/... but Azure has /opt/python/...
if [ -d "/antenv/bin" ]; then
    SYSTEM_PYTHON="$(which python3 2>/dev/null || which python)"
    echo "🔍 Debug: venv bin contents:"
    ls -la /antenv/bin/python* 2>&1 || true
    echo "🔍 Debug: system python is $SYSTEM_PYTHON"
    # If python symlink is broken, relink to system python
    if [ -L "/antenv/bin/python" ] && [ ! -e "/antenv/bin/python" ]; then
        echo "🔧 Fixing broken python symlink in venv..."
        rm -f /antenv/bin/python /antenv/bin/python3 /antenv/bin/python3.12
        ln -s "$SYSTEM_PYTHON" /antenv/bin/python
        ln -s "$SYSTEM_PYTHON" /antenv/bin/python3
        ln -s "$SYSTEM_PYTHON" /antenv/bin/python3.12
        echo "✅ Python symlinks fixed → $SYSTEM_PYTHON"
    fi
    # Fix shebang lines in venv scripts (gunicorn, uvicorn, etc.)
    # They contain the GitHub Actions workspace path which doesn't exist on Azure
    echo "🔧 Fixing shebang lines in venv scripts..."
    for script in /antenv/bin/*; do
        if [ -f "$script" ] && head -1 "$script" | grep -q "^#!.*antenv.*python"; then
            sed -i "1s|^#!.*|#!/antenv/bin/python|" "$script"
        fi
    done
    echo "✅ Shebang lines fixed"
fi

# Use explicit venv python path — source activate can fail silently in Oryx wrappers
if [ -x "/antenv/bin/python" ]; then
    PYTHON="/antenv/bin/python"
    export PATH="/antenv/bin:$PATH"
    export VIRTUAL_ENV="/antenv"
    echo "🔧 Using virtual environment: $PYTHON ($($PYTHON --version))"
elif [ -x "antenv/bin/python" ]; then
    PYTHON="$(pwd)/antenv/bin/python"
    export PATH="$(pwd)/antenv/bin:$PATH"
    export VIRTUAL_ENV="$(pwd)/antenv"
    echo "🔧 Using virtual environment: $PYTHON ($($PYTHON --version))"
else
    PYTHON="python"
    echo "⚠️ No virtual environment found, using system python"
fi

# Ensure antenv is activated for SSH sessions (persists across redeployments)
grep -qxF 'source /antenv/bin/activate' /home/.bashrc 2>/dev/null || echo 'source /antenv/bin/activate' >> /home/.bashrc

# Note: collectstatic is handled during CI/CD build (GitHub Actions workflow)
# The antenv virtual environment and static files are pre-built and included in the deployment zip
# Do NOT run collectstatic here — running it twice causes manifest conflicts

# Run migrations with no-input for faster execution
$PYTHON manage.py migrate --no-input

# Create cache table for database-backed caching (rate limiting)
# This is idempotent - safe to run on every deployment
echo "📦 Creating cache table if needed..."
$PYTHON manage.py createcachetable 2>&1 || echo "Cache table already exists or creation skipped"

# Only deploy media/data on initial deployment or when explicitly needed
# Set INITIAL_DEPLOYMENT=true in Azure portal only for first deployment
if [ "$INITIAL_DEPLOYMENT" = "true" ]; then
    echo "📦 Initial deployment - setting up media and data..."
    $PYTHON manage.py deploy_media_and_data --force-refresh
elif [ "$DEPLOY_MEDIA_AND_DATA" = "true" ]; then
    echo "🚀 Deploying complete media and data setup..."
    $PYTHON manage.py deploy_media_and_data --force-refresh
fi

# Legacy options (kept for backward compatibility)
if [ "$SYNC_MEDIA_TO_AZURE" = "true" ]; then
    echo "📸 Syncing local media files to Azure Blob Storage..."
    $PYTHON manage.py sync_media_to_azure
fi

if [ "$POPULATE_SAMPLE_DATA" = "true" ]; then
    echo "🍷 Auto-populating sample data with images..."
    $PYTHON manage.py populate_with_images --force-refresh
fi

echo "✅ Migrations complete. Starting Gunicorn..."

# Gunicorn + Uvicorn ASGI settings
# OPTIMIZATION: Increased workers from 2 to 4 for P0v3 plan (2 vCPU, 8GB RAM)
# Formula: (2 * CPU_CORES) + 1 = (2 * 2) + 1 = 5 workers (using 4 for safety)
# Using UvicornWorker for ASGI support (HTTP + WebSocket via Django Channels)
# Access logs sent to stderr for Azure Log Stream visibility (minimal format)
# Application Insights also captures requests via OpenTelemetry for full telemetry
# Using AsyncioUvicornWorker to force asyncio event loop — uvloop's
# run_in_executor breaks asgiref's CurrentThreadExecutor (django/channels#1959)
gunicorn --workers 4 --timeout 120 \
    -k azureproject.worker.AsyncioUvicornWorker \
    --access-logfile '-' --access-logformat '%(h)s %(m)s %(U)s %(s)s %(D)sms' \
    --error-logfile '-' --bind=0.0.0.0:8000 \
    azureproject.asgi:application
