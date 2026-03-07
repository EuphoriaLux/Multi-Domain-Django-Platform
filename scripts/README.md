# Scripts Directory

Utility scripts for development, translation, deployment, and maintenance tasks.

## FinOps / Deployment

- `deploy-finops-function.sh` - Azure Function deployment for daily cost sync
- `sync_costs.sh` - Manual cost sync trigger
- `upload_cost_exports_to_local.py` - Upload cost exports for local dev
- `verify-timer-triggers.ps1` - Verify Azure Function timer triggers

## Translation

- `translate_po.py` - Main translation tool (reusable for new strings)
- `translate_french_crush.py` - French translation automation
- `split_locale.py` - Split locale files by app

## Local Development

- `setup_azurite.py` - Azure Blob Storage emulator setup (used by `setup_local_dev`)

## Security / Monitoring

- `check_codeql_alerts.sh` - Check CodeQL security alerts
- `dismiss_codeql_alerts.sh` - Dismiss reviewed CodeQL alerts

## SEO / Analytics

- `gsc_report.py` - Google Search Console performance report

## Usage

All scripts should be run from the project root:

```bash
.venv/Scripts/python.exe scripts/script_name.py
```
