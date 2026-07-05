# Crush.lu iOS API Contract

Base host: `https://crush.lu`

The native app sends:

- User-Agent suffix: `CrushLUApp/1.0.0`
- Initial request header: `X-Crush-Client: ios-app`

## Public Config

`GET /api/mobile/ios/config/`

Returns app identity, feature flags, and canonical in-app URLs.

Important flags:

- `nativePushEnabled`: APNS credentials are configured.
- `nativeCommerceEnabled`: premium/payment CTAs may be shown in app.
- `universalLinksEnabled`: AASA is expected to be live.

## Native Auth Handoff

`GET /api/mobile/ios/auth/handoff/?redirect_uri=crushlu://auth`

Requires a logged-in browser session. Intended for `ASWebAuthenticationSession`.
Returns a redirect to:

`crushlu://auth?code=<one-time-code>&complete_url=<absolute-url>`

The app loads `complete_url` inside WKWebView.

`GET /api/mobile/ios/auth/complete/<code>/`

Consumes the one-time code, creates the normal Django session in WKWebView, marks
the session as `crush_ios_app`, then redirects to `/en/dashboard/?source=ios_app`.

## APNS Device Registration

`POST /api/mobile/ios/devices/register/`

Authenticated JSON body:

```json
{
  "deviceToken": "example_device_token_here",
  "deviceId": "stable-device-id",
  "environment": "sandbox",
  "bundleId": "lu.crush.app",
  "appVersion": "1.0.0",
  "appBuild": "1",
  "deviceName": "iPhone",
  "systemVersion": "18.0"
}
```

`POST /api/mobile/ios/devices/preferences/`

Authenticated JSON body:

```json
{
  "deviceId": "stable-device-id",
  "preferences": {
    "newMessages": true,
    "eventReminders": true,
    "newConnections": true,
    "profileUpdates": true
  }
}
```

`POST /api/mobile/ios/devices/unregister/`

Authenticated JSON body:

```json
{
  "deviceToken": "example_device_token_here"
}
```

`GET /api/mobile/ios/devices/`

Lists the signed-in user's registered iOS devices and preferences.

## Universal Links

`GET /.well-known/apple-app-site-association`

App ID:

`C5XDPB2G33.lu.crush.app`

Excluded paths:

- `/admin/*`
- `/crush-admin/*`
- `/api/*`
- `/static/*`
- `/media/*`
- `/sw-workbox.js`
- `/manifest.json`
