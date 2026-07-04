# Crush.lu Android Play Store Submission Checklist

## App Identity

- App name: Crush.lu
- Package name: `lu.crush.app`
- Version: `1.0.0`
- Version code: `1`
- Category: Dating or Social
- Native project: `mobile/android/CrushLU/`

## Required Public URLs

- App Links: `https://crush.lu/.well-known/assetlinks.json`
- Privacy: `https://crush.lu/en/privacy-policy/`
- Terms: `https://crush.lu/en/terms-of-service/`
- Support: `https://crush.lu/en/support/`
- Account deletion/data controls: `https://crush.lu/en/account/gdpr/`
- Data deletion public instructions: `https://crush.lu/en/data-deletion/`

## Play Console Metadata

- Short description: Privacy-first dating and real events in Luxembourg.
- Full description: Use `mobile/android/CrushLU/fastlane/metadata/android/en-US/full_description.txt`.
- Feature graphic: `mobile/android/CrushLU/fastlane/metadata/android/en-US/images/featureGraphic.png`
- App icon: `mobile/android/CrushLU/fastlane/metadata/android/en-US/images/icon.png`
- Phone screenshots: `mobile/android/CrushLU/fastlane/metadata/android/en-US/images/phoneScreenshots/`
- Demo account: create a stable review user with an approved profile and no real private messages.
- Review notes: hybrid Android WebView app, browser auth handoff for Google/LuxID login, no Play Billing in v1, premium CTAs suppressed while `ANDROID_NATIVE_COMMERCE_ENABLED=false`.
- App access, data safety, and content rating draft answers: `docs/app-store/crush-android-play-console-answers.md`.

## Production Environment

- `ANDROID_APP_PACKAGE=lu.crush.app`
- `ANDROID_APP_NAME=Crush.lu`
- `ANDROID_APP_VERSION=1.0.0`
- `ANDROID_APP_BUILD=1`
- `ANDROID_NATIVE_COMMERCE_ENABLED=false`
- `ANDROID_AUTH_REDIRECT_URIS=crushlu://auth`
- `ANDROID_APP_SHA256_CERT_FINGERPRINTS=<Play app signing SHA-256 certificate fingerprint>`
- `ANDROID_PLAY_STORE_URL=<store URL after the app exists>`

## Play Account Steps

1. Create the app in Play Console.
2. Choose package name `lu.crush.app`.
3. Create or upload the Play App Signing upload key.
4. Copy the Play app signing SHA-256 fingerprint into `ANDROID_APP_SHA256_CERT_FINGERPRINTS` in production.
5. Verify `https://crush.lu/.well-known/assetlinks.json` includes the Android app target.
6. Upload the release `.aab`.
7. Complete Data safety, app access, content rating, target audience, and ads declarations.
8. Upload screenshots captured from a review-safe account.
9. Submit to internal testing before production review.

## Test Plan

- `python manage.py check`
- `pytest crush_lu/tests/test_android_app_readiness.py`
- Build `app-release.aab`.
- Install a generated universal APK locally if needed.
- Login through the browser handoff and return to the app.
- Confirm WebView session persists after app restart.
- Confirm file upload works for profile photos.
- Confirm external links open outside the app.
- Confirm privacy, terms, support, account deletion, report/block, and notification settings are reachable.
- Confirm premium/payment CTAs are suppressed while `ANDROID_NATIVE_COMMERCE_ENABLED=false`.
