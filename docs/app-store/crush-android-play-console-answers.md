# Crush.lu Android Play Console Answers

Use this as the working copy for Play Console fields that are not part of the `.aab` itself.

## App Access

Crush.lu has public pages, but reviewer-visible member features require login.

Suggested Play Console entry:

- Access type: Some or all functionality is restricted.
- Instructions: Use the demo account below, then open the app and select Login / Join.
- Username/email: create a stable review account, for example `play-review@crush.lu`.
- Password: create a unique password in the password manager.
- Account state: approved profile, no real private messages, no real user photos, no live payment method.
- Notes: Login may open a browser window and return to the app through `crushlu://auth`.

Do not put real customer data in the review account.

## Review Notes

Copy from `mobile/android/CrushLU/fastlane/metadata/android/en-US/review_notes.txt`.

## Data Safety Draft

This draft should be checked against the live production privacy policy before final submission.

### Data Collection

Crush.lu collects these user-provided or app-generated categories:

- Personal info: name, email address, phone number, date of birth, city/region, gender, looking-for preferences, language preferences.
- Photos and videos: profile photos uploaded by the user.
- App activity: profile completion state, event registrations, connections, notification preferences, account settings, moderation/report actions.
- User-generated content: profile bio, interests, profile answers, messages or connection-related content where enabled.
- Device or other IDs: account user ID, session identifiers, push/web notification subscription identifiers, native app session markers.
- Approximate location: user-provided city/region in Luxembourg, not native GPS collection in the Android wrapper.
- Diagnostics: server logs and operational diagnostics may include request metadata needed to run and secure the service.

The Android wrapper itself requests only `INTERNET` and `ACCESS_NETWORK_STATE`; data entry happens in the Crush.lu web experience.

### Purpose

- App functionality: account login, profile creation, event registration, matching/connection flows, notifications, privacy controls.
- Safety, security, and compliance: verification, fraud prevention, moderation, reports, account deletion, consent records.
- Communications: account emails, event/service notifications, support.
- Analytics and service improvement: aggregated usage and operational diagnostics.

### Sharing

Disclose data sharing only for service providers that process data for Crush.lu, such as hosting, authentication, email/SMS/notification delivery, storage, monitoring, and support operations. Do not mark data as sold.

### Security Practices

- Data is encrypted in transit with HTTPS.
- Users can request account/profile deletion at `https://crush.lu/en/account/gdpr/`.
- Public deletion instructions are available at `https://crush.lu/en/data-deletion/`.

## Content Rating

Expected direction for the questionnaire:

- App category: Dating or Social.
- Target audience: adults only, 18+.
- User interaction: users can create profiles and may communicate/connect through the platform.
- User-generated content: profiles, photos, answers, messages/connections where enabled.
- Moderation/reporting: report/block and account safety controls are available in the product.
- Purchases: no Android in-app purchases for v1 while `ANDROID_NATIVE_COMMERCE_ENABLED=false`.
- Ads: no ads unless this changes in production.

The final rating is assigned by the Play Console/IARC questionnaire; answer from the live product state at submission time.

## Store Listing Assets

- App icon: `mobile/android/CrushLU/fastlane/metadata/android/en-US/images/icon.png` — 512x512 PNG.
- Feature graphic: `mobile/android/CrushLU/fastlane/metadata/android/en-US/images/featureGraphic.png` — 1024x500 PNG.
- Phone screenshots:
  - `mobile/android/CrushLU/fastlane/metadata/android/en-US/images/phoneScreenshots/01-home.png` — 1080x1920 PNG.
  - `mobile/android/CrushLU/fastlane/metadata/android/en-US/images/phoneScreenshots/02-privacy.png` — 1080x1920 PNG.

## Production Setup Before Upload

- Set `ANDROID_NATIVE_COMMERCE_ENABLED=false` until Play Billing is implemented.
- After creating the app in Play Console, copy the Play app signing SHA-256 certificate fingerprint into `ANDROID_APP_SHA256_CERT_FINGERPRINTS`.
- Verify `https://crush.lu/.well-known/assetlinks.json` includes the `android_app` target for `lu.crush.app`.
- Build and upload the signed `app-release.aab`.
