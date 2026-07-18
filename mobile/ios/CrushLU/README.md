# Crush.lu iOS App

Native iOS shell for the Crush.lu App Store build.

- App name: Crush.lu
- Bundle ID: lu.crush.app
- Team ID: C5XDPB2G33
- Version: 1.0.2
- Build: 2
- Device family: iPhone only for v1
- Minimum iOS: 16.0

## Environments

- Release builds (TestFlight and App Store) target `https://crush.lu` (production).
- Debug builds (local Xcode runs) target `https://test.crush.lu` (staging).

## Build Locally

This scaffold uses XcodeGen so the generated `.xcodeproj` does not have to be
kept in git.

```bash
brew install xcodegen
cd mobile/ios/CrushLU
xcodegen generate
open CrushLU.xcodeproj
```

Archive from Xcode after selecting the Apple Developer team. The
`aps-environment` entitlement is set to `production` for App Store builds; for
local development-signed builds Xcode manages the push environment.

## Backend Contract

The shell expects the Django backend to expose:

- `/.well-known/apple-app-site-association`
- `/api/mobile/ios/config/`
- `/api/mobile/ios/auth/handoff/`
- `/api/mobile/ios/auth/complete/<code>/`
- `/api/mobile/ios/devices/register/`
- `/api/mobile/ios/devices/unregister/`
- `/api/mobile/ios/devices/preferences/`

The app injects the `CrushLUApp/1.0.2` user-agent suffix and sends
`X-Crush-Client: ios-app` on initial web requests.

## Review Notes

Premium membership CTAs are suppressed in the native iOS app unless
`IOS_NATIVE_COMMERCE_ENABLED=true`. v1 should stay free of in-app digital
purchases until StoreKit policy is explicitly implemented.
