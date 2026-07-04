# Crush.lu iOS App Store Submission Checklist

Branch: `feature/crush-ios-app-store-readiness`

## App Identity

- App name: Crush.lu
- Bundle ID: `lu.crush.app`
- Apple Team ID: `C5XDPB2G33`
- Version: `1.0.0`
- Build: `1`
- Category: Social Networking or Lifestyle
- Devices: iPhone only for v1
- Native project: `mobile/ios/CrushLU/`

## Backend Environment

Required environment variables:

- `IOS_APP_BUNDLE_ID=lu.crush.app`
- `IOS_APP_TEAM_ID=C5XDPB2G33`
- `IOS_APP_NAME=Crush.lu`
- `IOS_APP_VERSION=1.0.0`
- `IOS_APP_BUILD=1`
- `IOS_NATIVE_COMMERCE_ENABLED=false` for v1
- `IOS_APNS_KEY_ID`
- `IOS_APNS_TEAM_ID`
- `IOS_APNS_BUNDLE_ID=lu.crush.app`
- `IOS_APNS_PRIVATE_KEY` or `IOS_APNS_PRIVATE_KEY_BASE64`
- `IOS_APNS_USE_SANDBOX=false` for production

Required public URLs:

- Universal Links: `https://crush.lu/.well-known/apple-app-site-association`
- Privacy: `https://crush.lu/en/privacy-policy/`
- Terms: `https://crush.lu/en/terms-of-service/`
- Support: `https://crush.lu/en/support/`
- Account deletion/data controls: `https://crush.lu/en/account/gdpr/`
- Data deletion public instructions: `https://crush.lu/en/data-deletion/`

## App Store Connect Metadata

Prepare before upload:

- Subtitle: Privacy-first dating in Luxembourg
- Promotional text: Meet people at real events, with safer introductions and coach-supported connections.
- Description: Explain event-based dating, verified profiles, coach-supported introductions, privacy controls, and safety tools.
- Keywords: dating, Luxembourg, events, singles, matchmaking, social
- Support URL: `https://crush.lu/en/support/`
- Privacy Policy URL: `https://crush.lu/en/privacy-policy/`
- Marketing URL: optional, `https://crush.lu/`
- Copyright: Crush.lu
- Demo account: create a stable review user with an approved profile and no real private messages.
- Review notes: mention hybrid WKWebView app, native APNS registration, no IAP in v1, premium CTAs suppressed in iOS app mode.

## Privacy And Safety

Verify in the iOS app before review:

- Privacy policy opens in-app.
- Terms open in-app.
- Account deletion/data management opens in-app.
- Report and block actions are reachable from a member connection/detail surface.
- Push notification permission is requested only through native iOS permission UI.
- Notification preferences are accessible from account/profile settings.
- No premium payment flow or digital purchase CTA is active in the iOS app while `IOS_NATIVE_COMMERCE_ENABLED=false`.

Privacy nutrition labels to prepare in App Store Connect:

- Contact Info: email address, phone number if collected.
- User Content: profile photos, bio/profile answers, messages.
- Identifiers: user ID, device/APNS token.
- Usage Data: app interactions and device/activity records.
- Diagnostics: only if crash/analytics tools are added later.

## Screenshots

Create iPhone screenshots from a production-like TestFlight build:

- 6.9-inch display set
- 6.7-inch or 6.5-inch display set if required by App Store Connect
- Login/onboarding or dashboard
- Events list/detail
- Crush Connect or connections
- Privacy/account controls
- Notification settings or safety/report flow

Keep screenshots free of real user personal data.

## Build And Release Flow

1. Generate Xcode project:
   `cd mobile/ios/CrushLU && xcodegen generate`
2. Open `CrushLU.xcodeproj`.
3. Select team `C5XDPB2G33`.
4. Confirm bundle ID `lu.crush.app`.
5. For release archive, set `aps-environment` to `production`.
6. Build on a real iPhone.
7. Archive and upload to App Store Connect.
8. Run internal TestFlight.
9. Add external TestFlight if needed.
10. Submit for review with live backend and review notes.

## Test Plan

Backend:

- `python manage.py check`
- `pytest crush_lu/tests/test_ios_app_readiness.py`
- Verify AASA JSON has `C5XDPB2G33.lu.crush.app`.
- Verify native auth handoff and one-time completion.
- Verify APNS registration and preference APIs.
- Verify notification fan-out calls APNS devices.

iOS:

- Launch from cold start.
- Login through native auth handoff.
- Confirm WKWebView session persists after app restart.
- Open Universal Link into app.
- Register APNS token after permission grant.
- Receive push and navigate to payload URL.
- Open external links outside the webview.
- Show offline banner when network is unavailable.
- Confirm premium/payment CTAs are suppressed.
- Confirm privacy, account deletion, report/block, and notification settings are reachable.

## Deferred Work

- Android app wrapper and Play Store flow.
- Full SPA/API rebuild on `feature/crush-mobile-api-spa-foundation`.
- StoreKit/IAP for premium membership.
- App Store screenshot automation.
- Native account-settings screens.
