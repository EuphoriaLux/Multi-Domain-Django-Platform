#!/bin/bash
# Azure WebJob script to sync daily costs
# Place this in the wwwroot/App_Data/jobs/triggered/sync-costs/ directory

set -e

echo "Starting daily cost sync at $(date)"

# Activate virtual environment if needed
cd /home/site/wwwroot
source antenv/bin/activate

# Run the sync command
python manage.py sync_daily_costs

echo "Completed daily cost sync at $(date)"
