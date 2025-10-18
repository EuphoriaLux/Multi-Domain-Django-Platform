# 🚨 CRITICAL SECURITY ALERT - Exposed API Keys

## Issue Discovered
API keys have been committed to the `.env` file in the git repository. This is a **CRITICAL security vulnerability**.

**Exposed Keys**:
- `ANTHROPIC_API_KEY=sk-ant-api03-XXXX...REDACTED...XXXX` (found in .env file)
- `GEMINI_API_KEY=AIzaSyBqN9Busr...REDACTED...SSiw` (found in .env file)

**Risk**: These keys were publicly accessible in your git history and could be used by unauthorized parties.

---

## IMMEDIATE ACTIONS REQUIRED

### Step 1: Revoke Compromised Keys (DO THIS NOW)

#### Revoke Anthropic API Key
1. Go to https://console.anthropic.com/settings/keys
2. Find the key starting with `sk-ant-api03-...`
3. Click "Revoke" or "Delete"
4. Confirm revocation

#### Revoke Gemini API Key
1. Go to https://aistudio.google.com/app/apikey
2. Find the key starting with `AIzaSyBqN9Busr...`
3. Click "Delete API key"
4. Confirm deletion

---

### Step 2: Remove .env from Git History

The `.env` file containing these keys has been committed to git. You need to remove it from history:

**Option A: Using BFG Repo-Cleaner (Recommended)**
```bash
# Download BFG: https://rpo.github.io/bfg-repo-cleaner/
java -jar bfg.jar --delete-files .env
git reflog expire --expire=now --all && git gc --prune=now --aggressive
```

**Option B: Using git-filter-repo**
```bash
# Install: pip install git-filter-repo
git-filter-repo --path .env --invert-paths
```

**Option C: Using git filter-branch (Legacy)**
```bash
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .env" \
  --prune-empty --tag-name-filter cat -- --all
```

After removing from history:
```bash
git push origin --force --all
git push origin --force --tags
```

**⚠️ WARNING**: Force pushing rewrites history. Coordinate with team members.

---

### Step 3: Generate New API Keys

#### Create New Anthropic API Key
1. Go to https://console.anthropic.com/settings/keys
2. Click "Create Key"
3. Name it: `Crush.lu Production - $(date +%Y-%m-%d)`
4. Copy the new key
5. Update environment variables (see Step 4)

#### Create New Gemini API Key
1. Go to https://aistudio.google.com/app/apikey
2. Click "Create API Key"
3. Select your project
4. Copy the new key
5. Update environment variables (see Step 4)

---

### Step 4: Update Environment Variables

**For Azure App Service (Production)**:
1. Go to Azure Portal
2. Navigate to your App Service
3. Go to Configuration → Application settings
4. Update:
   - `ANTHROPIC_API_KEY` = [new key]
   - `GEMINI_API_KEY` = [new key]
5. Click "Save"
6. Restart the app service

**For Local Development**:
1. Update your local `.env` file with new keys
2. **NEVER commit .env to git**
3. Verify `.env` is in `.gitignore`

---

### Step 5: Verify .gitignore

Ensure `.env` is properly ignored:

```bash
# Check if .env is in .gitignore
grep -r "\.env" .gitignore

# If not present, add it:
echo ".env" >> .gitignore
echo "*.env" >> .gitignore
echo ".env.*" >> .gitignore

# Commit .gitignore changes
git add .gitignore
git commit -m "Ensure .env files are ignored"
```

---

## Prevention Strategies

### 1. Use Environment Variables Properly

**Development** (`.env` file):
```bash
# .env (NEVER commit this file)
ANTHROPIC_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here
```

**Production** (Azure App Service):
- Use Azure App Service Configuration → Application settings
- These are automatically loaded as environment variables
- Never store secrets in code or config files

### 2. Use Azure Key Vault (Optional - Advanced)

For enhanced security, consider Azure Key Vault:
```python
# azureproject/settings.py
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

credential = DefaultAzureCredential()
client = SecretClient(vault_url="https://your-vault.vault.azure.net/", credential=credential)

ANTHROPIC_API_KEY = client.get_secret("ANTHROPIC-API-KEY").value
```

### 3. Pre-commit Hooks

Install pre-commit hooks to prevent committing secrets:

```bash
# Install pre-commit
pip install pre-commit

# Create .pre-commit-config.yaml
cat > .pre-commit-config.yaml <<EOF
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.4.0
    hooks:
      - id: detect-private-key
      - id: check-added-large-files

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.4.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
EOF

# Install hooks
pre-commit install
```

### 4. Use GitHub Secret Scanning

GitHub will automatically scan for leaked secrets (as it did here):
- Enable push protection (Settings → Code security)
- Review secret scanning alerts regularly
- Set up notifications for secret detection

### 5. Environment Variable Checklist

Always verify before committing:
- [ ] `.env` is in `.gitignore`
- [ ] No API keys in code files
- [ ] No API keys in config files
- [ ] No API keys in documentation
- [ ] Azure App Service settings configured
- [ ] Local `.env` file exists (not committed)

---

## Monitoring

After remediation:
1. Monitor API key usage in provider dashboards
2. Check for unexpected API calls
3. Review access logs for suspicious activity
4. Set up billing alerts (if API usage is metered)

---

## Timeline

**Immediate** (within 1 hour):
- [x] Revoke old keys
- [x] Generate new keys
- [x] Update production environment variables

**Within 24 hours**:
- [ ] Remove .env from git history
- [ ] Force push cleaned history
- [ ] Verify .gitignore configuration
- [ ] Install pre-commit hooks

**Within 1 week**:
- [ ] Review all API usage for anomalies
- [ ] Consider implementing Azure Key Vault
- [ ] Update team documentation on secret management

---

## Additional Resources

- [GitHub Secret Scanning Documentation](https://docs.github.com/en/code-security/secret-scanning)
- [Azure Key Vault Quick Start](https://learn.microsoft.com/en-us/azure/key-vault/general/quick-create-python)
- [Anthropic API Key Best Practices](https://docs.anthropic.com/claude/reference/api-key-security)
- [Google Cloud API Key Best Practices](https://cloud.google.com/docs/authentication/api-keys)

---

## Questions?

If you need help with any of these steps:
1. Check Azure documentation
2. Review provider-specific security guides
3. Contact your security team if in an organization
4. Consider hiring a security consultant for audit

**Remember**: Treating API keys as secrets is critical. Never commit them to version control.
