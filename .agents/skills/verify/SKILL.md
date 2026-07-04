---
name: verify
description: How to run and drive this Django multi-domain app locally to verify crush.lu changes end-to-end (runserver, test account, browser flows).
---

# Verifying crush.lu changes end-to-end

## Build & launch

```powershell
.venv/Scripts/Activate.ps1          # ALWAYS first (see AGENTS.md)
npm run build:css                   # if any tailwind-src/**.css changed
$env:CRUSH_CONNECT_LAUNCHED='true'  # Crush Connect is feature-flagged, default off
python manage.py runserver 8000 --noreload
```

Browse via `http://crush.localhost:8000` (NOT crush.lu — HSTS). Other sites:
`portal.localhost`, `api.localhost`, … (see `DEV_DOMAIN_MAPPINGS` in
`azureproject/domains.py`).

## Test account (idempotent)

A ready onboarded Crush Connect member: **verifyghost@example.com / verify-pass-123**.
If missing, recreate by mirroring `_make_user` from
`crush_lu/tests/test_crush_connect.py`: user + verified allauth `EmailAddress`
+ approved `CrushProfile` + assigned `CrushCoach` (premium) + `luxid`
`SocialAccount` + `UserDataConsent(crushlu_consent_given=True)` +
`CrushConnectMembership(onboarded_at=now, onboarding_step=8)`.

To see the **onboarding wizard** instead of the profile editor, set
`membership.onboarded_at = None; membership.onboarding_step = <n>` (restore
after). Onboarded members are redirected away from the wizard.

## Gotchas

- `manage.py shell` via piped stdin breaks on PowerShell's UTF-8 BOM — use
  `python manage.py shell -c "..."` from the **Bash** tool instead.
- Login page is `/accounts/login/` (allauth lives OUTSIDE i18n_patterns; the
  `/de/accounts/...` form 404s).
- Decline the cookie banner once per fresh session.
- The "Crush.lu App installieren" banner appears/disappears between loads and
  shifts the layout ~50px — take a FRESH screenshot before every
  coordinate-based click, or clicks land on the wrong element (a mis-click on
  a range slider silently changes its value).
- The Codex-in-chrome extension blocks `document.styleSheets[..].cssRules`
  enumeration; probe computed styles instead
  (`getComputedStyle(el)` on an injected element works fine).
- CSS layer trap: `@tailwindcss/forms` emits `.form-select`/`.form-input`
  rules inside `@layer utilities`; anything in `@layer components` loses to
  them regardless of selector specificity. Component overrides for
  plugin-styled form controls must be UNLAYERED (bottom of
  `tailwind-src/crush_lu/tailwind-input.css`, see "Form select (unlayered)").

## Flows worth driving

- Crush Connect profile editor: `/de/crush-connect/profile/?section=<key>`
  (keys in `crush_lu/onboarding_connect.py` CONNECT_STEPS: intention,
  lifestyle, languages, life, family, ideal_match, questions). Save →
  redirects to index which shows per-section value summaries.
- Onboarding wizard: `/de/crush-connect/onboarding/<step>/` (pointer-gated).
- Theme toggle: header sun/moon buttons (`html.dark` class-based dark mode) —
  check both themes; brand "white" is `#f0ecf5`, not `#fff`.
