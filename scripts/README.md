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

## SEO / Analytics / Google Integrations

- `gsc_report.py` - Google Search Console performance report (queries, pages, countries)
- `ga4_report.py` - GA4 traffic acquisition report (sources, landing pages, geography, devices)
- `seo_funnel_report.py` - Combined GSC search impressions + GA4 conversion funnel
- `google_index_ping.py` - Instant Googlebot indexing notifications (`URL_UPDATED`, `URL_DELETED`)
- `gbp_discover.py` - Google Business Profile accounts & locations discovery tool

## Usage

All scripts should be run from the project root:

```bash
.venv/Scripts/python.exe scripts/script_name.py
```
