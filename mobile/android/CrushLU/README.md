# Crush.lu Android

Native Android WebView shell for the Crush.lu Play Store submission.

## App Identity

- App name: Crush.lu
- Package name: `lu.crush.app`
- Version: `1.0.0`
- Version code: `1`
- Category: Dating or Social
- Start URL: `https://crush.lu/en/dashboard/?source=android_app`

## Build

1. Install Android Studio with Android SDK 35 or newer.
2. Open `mobile/android/CrushLU`.
3. Copy `gradle.properties.example` to `gradle.properties` and fill in the Play upload key values.
4. Build the Play upload bundle from Android Studio: Build > Generate Signed App Bundle / APK > Android App Bundle.

You can also run `./gradlew bundleRelease` from this directory. On Windows PowerShell, use `.\gradlew.bat bundleRelease`. The Play upload file will be created at `app/build/outputs/bundle/release/app-release.aab`.

## Signing & Release CI

Signed production builds are produced by the `android-release.yml` workflow
(triggered by a `v*` tag or a manual `workflow_dispatch`). It is the only path
that uploads to the Play Store. Do **not** commit signing secrets — they live in
GitHub Actions secrets and are injected at run time.

The workflow expects these repository secrets:

| Secret | Purpose |
|--------|---------|
| `ANDROID_KEYSTORE_BASE64` | The Play upload keystore (`crush-lu-upload.jks`), base64-encoded. Decoded into `app/keystores/` at build time. |
| `ANDROID_KEYSTORE_PASSWORD` | Keystore password. |
| `ANDROID_KEY_ALIAS` | Signing key alias (e.g. `crush-lu-upload`). |
| `ANDROID_KEY_PASSWORD` | Signing key password. |
| `ANDROID_SERVICE_ACCOUNT_JSON` | Google Play service account JSON (only required for the Play upload step). |

The build is driven by gradle properties passed on the command line, never read
from a committed `gradle.properties`:

- `CRUSH_ENV` — `production` (default) or `staging`. The local-dev `local`
  variant (`http://10.0.2.2:8000`, package `lu.crush.app.local`) is for emulator
  use only and is never built by CI.
- `CRUSH_VERSION_CODE` — a monotonic value computed at run time
  (seconds since 2024-01-01) under a serialized concurrency group, so Play
  always sees increasing codes. The literal fallback of `4` in
  `app/build.gradle.kts` is local-only and never uploaded.

Releases run one at a time (concurrency group `android-release`,
`cancel-in-progress: false`) and upload to the `internal` track. Release notes
ship from `distribution/whatsnew/whatsnew-en-US` — edit before each release.

## Backend Environment

Set these in production before submitting:

- `ANDROID_APP_PACKAGE=lu.crush.app`
- `ANDROID_APP_NAME=Crush.lu`
- `ANDROID_APP_VERSION=1.0.0`
- `ANDROID_APP_BUILD=1`
- `ANDROID_NATIVE_COMMERCE_ENABLED=false`
- `ANDROID_AUTH_REDIRECT_URIS=crushlu://auth`
- `ANDROID_APP_SHA256_CERT_FINGERPRINTS=<SHA-256 fingerprint from the Play app signing certificate>`
- `ANDROID_PLAY_STORE_URL=<store URL after the app exists>`

`ANDROID_APP_SHA256_CERT_FINGERPRINTS` is required for Android App Links verification at `https://crush.lu/.well-known/assetlinks.json`.

## Verification

- Open app cold.
- Login with Google/LuxID through the browser handoff.
- Confirm return to dashboard in the app.
- Upload a profile photo.
- Open privacy policy, terms, support, notification settings, report/block, and account deletion.
- Confirm premium subscription CTAs are hidden while `ANDROID_NATIVE_COMMERCE_ENABLED=false`.
- Confirm `https://crush.lu/...` links open directly in the app after Play signing fingerprint is configured.

## Play Console Materials

- Store listing metadata: `fastlane/metadata/android/en-US/`
- Review notes: `fastlane/metadata/android/en-US/review_notes.txt`
- App access/data safety/content rating draft: `docs/app-store/crush-android-play-console-answers.md`
