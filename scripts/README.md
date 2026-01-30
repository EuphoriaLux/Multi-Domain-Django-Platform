# Scripts Directory

Utility scripts for development, translation, deployment, and maintenance tasks.

## 🚀 FinOps Hub Deployment Scripts

### Quick Deploy

```bash
# Deploy Azure Function for daily cost sync
./scripts/deploy-finops-function.sh --generate-token
```

### Deployment Scripts

- `deploy-finops-function.sh` - Azure Function deployment (Bash)

**Documentation:**
- See `AZURE_CLI_DEPLOYMENT.md` for complete deployment guide
- See `.github/workflows/deploy-finops-sync-function.yml` for CI/CD automation

## Translation Scripts

- `translate_po.py` - Main translation tool (107KB)
- `translate_french_crush.py` - French translation automation
- `translate_all_remaining_de.py` - Batch German translation
- `final_translations_de.py` - Final German translation pass
- `translate_remaining_de.py` - Remaining German translations
- `verify_translations_de.py` - Verification script
- `fix_fuzzy_de.py` - Fix fuzzy translation entries (uses polib)
- `fix_fuzzy_german.py` - Alternative fuzzy fixer (line-by-line processing)
- `show_untranslated.py` - Show untranslated strings
- `split_locale.py` - Split locale files

## Storage Scripts

- `setup_azurite.py` - Azure Blob emulator setup
- `migrate_blob_structure.py` - Storage migration utility

## Analysis Scripts

- `analyze_fuzzy.py` - Extract fuzzy entries from .po files
- `batch_fix_fuzzy.py` - Batch fix fuzzy translation entries
- `view_mobile_phone_field.py` - Phone field visualization for debugging

## Screenshot Scripts

- `capture_admin.py` - Admin panel screenshot utility

## Usage

All scripts should be run from the project root:

```bash
# Activate virtual environment first
.venv/Scripts/Activate.ps1

# Run script
python scripts/script_name.py
```
