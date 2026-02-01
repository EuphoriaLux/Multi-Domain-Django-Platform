# Security Scan Report - 2026-02-01

## Executive Summary

Security scan completed for commit `465b6a5` with the following results:

| Category | Status | Action Required |
|----------|--------|-----------------|
| **Secrets Detection (GitLeaks)** | ⚠️ False Positives | ✅ Fixed - Updated allowlist |
| **SAST (Bandit)** | ✅ Passed | ⚙️ Review recommended |
| **Dependencies** | ⚠️ Vulnerabilities Found | 🔧 Update required |

---

## 1. Secrets Detection (GitLeaks)

### Findings
Two false positives detected in documentation and development scripts:

1. **`azure-functions/finops-daily-sync/README.md:180`**
   - Detection: `YOUR_SECRET_TOKEN` in curl example
   - Risk: **None** - Documentation placeholder

2. **Azurite Development Key**
   - Detection: Well-known Microsoft Azure Storage Emulator default key
   - Risk: **None** - [Official Microsoft documentation](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azurite)
   - Key: `Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==`
   - Used only for local development with Docker Azurite container

### Resolution
✅ **COMPLETED** - Updated `.gitleaks.toml` allowlist to suppress these false positives:
- Added regex patterns for documentation placeholders
- Added Azurite default key (public, not a secret)
- Scoped allowlist to documentation and development scripts only

**Next scan should pass cleanly.**

---

## 2. SAST Analysis (Bandit)

### Results
- **High Severity:** 0 issues ✅
- **Medium Severity:** 50 issues (informational)
- **Low Severity:** Not reported

### Issue Breakdown
All 50 medium-severity issues are related to Django's `mark_safe()` usage:
- **25x B703:** Potential XSS on `mark_safe` function
- **25x B308:** Use of `mark_safe()` may expose XSS vulnerabilities

### Affected Files
```
./arborist/templatetags/arborist_seo_tags.py
./azureproject/templatetags/analytics.py
./crush_lu/templatetags/seo_tags.py
./entreprinder/templatetags/vite_tags.py
./entreprinder/vibe/templatetags/vite_tags.py
./entreprinder/widgets.py
./vinsdelux/admin.py
```

### Assessment
**Risk Level: Low to Medium**

`mark_safe()` is used in:
1. **SEO template tags** - Generating structured data (JSON-LD), meta tags, canonical URLs
2. **Analytics tags** - Embedding Google Analytics and Application Insights scripts
3. **Vite asset tags** - Loading frontend build assets
4. **Admin interface** - Custom widgets and display helpers

**Mitigation:**
- Most usage is for **trusted, developer-controlled content** (JSON-LD schemas, script tags)
- Not directly rendering user-submitted data
- Django's template auto-escaping provides defense-in-depth

**Recommendation:**
- ⚙️ **Low Priority** - Review each `mark_safe()` usage to ensure no user input is passed
- Consider adding `# nosec B308` comments for verified-safe usages
- Add automated tests to validate XSS protection on user-facing forms

---

## 3. Dependency Vulnerabilities

### Critical Findings

| Package | Current | Vulnerable | Fix Version | Severity | Description |
|---------|---------|------------|-------------|----------|-------------|
| **pip** | 23.1.2 | ✓ | 25.3 | Medium | CVE-2025-8869: Tar extraction path traversal |
| **pip** | 23.1.2 | ✓ | 23.3 | Low | PYSEC-2023-228: Mercurial VCS command injection |
| **setuptools** | 65.5.0 | ✓ | 78.1.1 | **High** | PYSEC-2025-49: Path traversal → RCE |
| **setuptools** | 65.5.0 | ✓ | 70.0.0 | **High** | CVE-2024-6345: Remote code execution |
| **setuptools** | 65.5.0 | ✓ | 65.5.1 | Medium | PYSEC-2022-43012: ReDoS in package_index |

### Recommended Actions

#### Immediate (High Priority)
```bash
# Update build dependencies
pip install --upgrade pip setuptools wheel

# Verify versions
pip list | grep -E "pip|setuptools"
# Expected: pip>=25.3, setuptools>=78.1.1
```

#### Follow-up
1. Update `requirements.txt` to pin secure versions:
   ```
   pip>=25.3
   setuptools>=78.1.1
   wheel>=0.45.0
   ```

2. Run comprehensive dependency audit:
   ```bash
   pip-audit --fix --dry-run  # Preview fixes
   pip-audit --fix            # Apply fixes
   ```

3. Consider adding to CI pipeline:
   ```yaml
   - name: Security Audit
     run: |
       pip install pip-audit
       pip-audit --strict
   ```

---

## 4. Next Steps

### Immediate Actions (Completed)
- [x] Update `.gitleaks.toml` to suppress false positives
- [x] Run dependency vulnerability audit
- [x] Document Bandit findings

### Recommended Actions
- [ ] **Update pip and setuptools** (see commands above)
- [ ] **Run full dependency update:**
  ```bash
  pip-audit --fix
  pip list --outdated
  ```
- [ ] **Review `mark_safe()` usage** in affected files
- [ ] **Re-run security scan** to verify all issues resolved:
  ```bash
  gh workflow run security-scan.yml
  ```

### Long-term Improvements
1. Add `pip-audit` to CI/CD pipeline
2. Enable Dependabot for automated dependency updates
3. Add `# nosec` comments with justifications for legitimate `mark_safe()` usage
4. Consider pre-commit hooks for secrets detection

---

## Summary

**Overall Security Posture:** Good ✅

- No actual secrets leaked (false positives resolved)
- No high-severity code issues
- Dependency vulnerabilities are in build tools (low runtime risk)
- All issues have clear remediation paths

**Priority:** Update pip and setuptools to resolve known CVEs, then re-run scan.
