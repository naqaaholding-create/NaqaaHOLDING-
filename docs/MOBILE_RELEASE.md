# NAQAA Market — Mobile Release / Store Submission

## حالة المشروع الآن

- Capacitor 7 mobile app.
- Android application ID: `com.naqaaholding.market`.
- iOS bundle ID: `com.naqaaholding.market`.
- Real-money settlement remains **OFF**.
- QA Android workflow remains available for debug/test artifacts.
- A dedicated signed Google Play AAB workflow has been added: `.github/workflows/mobile-android-store-release.yml`.
- A dedicated signed iOS App Store archive workflow has been added: `.github/workflows/mobile-ios-store-release.yml`.

## Google Play — current technical requirement

As of August 31, 2026, new apps and updates submitted to Google Play must target Android 16 / API 36 or higher. The release workflows therefore patch the generated Capacitor Android project to compile/target API 36 before building. Verify the generated project and dependencies again immediately before submission because Google can change requirements.

## Android release secrets

Configure these GitHub Actions secrets before running the store-release workflow:

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

The workflow fails instead of silently producing an unsigned store build.

Recommended distribution model: Google Play App Signing, with the company-controlled keystore retained as the upload key.

## Apple App Store — current technical requirement

Apple currently requires App Store submissions to be built with Xcode 26 or later using the iOS 26 SDK or later. The signed iOS workflow checks the installed Xcode major version before building.

The current workflow creates an App Store archive and IPA artifact; it does not upload the IPA to Apple automatically.

## iOS release secrets

Configure:

- `IOS_DISTRIBUTION_P12_BASE64`
- `IOS_DISTRIBUTION_P12_PASSWORD`
- `IOS_APPSTORE_PROFILE_BASE64`
- `IOS_TEMP_KEYCHAIN_PASSWORD`

The provisioning profile must match `com.naqaaholding.market` and the distribution certificate must be valid for App Store distribution.

## Store content still requiring company input/approval

Before submission, the company owner must provide/approve:

1. Final app icon and store screenshots.
2. Final legal entity name and support contact.
3. Final privacy policy and terms of use (the current privacy page is explicitly a draft).
4. Google Play Data Safety answers.
5. Google Play content/age rating.
6. Apple App Privacy answers and age-rating answers.
7. Final store description in Arabic and English.
8. Support URL and privacy-policy URL.
9. Final production HTTPS API URL and verified backend deployment.
10. Google Play Console / Apple Developer ownership and app records.

## Financial safety

Do not enable real-money settlement merely to publish the app. The repository intentionally keeps live settlement behind production approval gates. A live-money launch additionally requires the applicable legal/compliance approvals, KYC/KYB controls, persistent storage, provider contract verification, webhook verification, reconciliation, and security testing.

## QA before store submission

- Install the release build on physical Android hardware.
- Test registration, email verification, login/logout.
- Test market browsing, listing creation, images and account pages.
- Test responsive layout and keyboard behavior.
- Test wallet screens without enabling real-money settlement.
- Test session restart and API error handling.
- Verify privacy/terms links.
- Verify no debug/demo wording appears in the store-facing flow.
- Record and fix crashes before submission.

## Versioning

Current mobile store release workflow defaults to:

- Android `versionCode: 2`
- Android/iOS marketing version: `1.0.1`

Increase the Android versionCode for every Google Play upload.
