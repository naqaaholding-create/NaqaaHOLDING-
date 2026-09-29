# NAQAA Market — Mobile Release Checklist

## الحالة الحالية
- Android/iOS: Capacitor 7
- App ID: com.naqaaholding.market
- Web directory: static/
- Real-money settlement: OFF
- Debug Android workflow: enabled
- iOS simulator workflow: enabled

## Android QA
1. Build debug APK.
2. Install on a physical Android device.
3. Test account creation and login.
4. Test market navigation and responsive layout.
5. Test wallet demo flows.
6. Confirm no real-money operation is possible.
7. Test app restart and local session behavior.
8. Record crashes and API errors.

## iOS QA
1. Build simulator artifact on macOS.
2. Test navigation and forms.
3. Test safe-area behavior on iPhone screens.
4. Test keyboard/input fields.
5. Confirm no real-money operation is possible.
6. Prepare a signed archive only after Apple Developer credentials are available.

## Store preparation
### Google Play
- Google Play Console developer account
- Final app name and description
- App icon and screenshots
- Privacy Policy URL
- Data Safety declarations
- Content rating
- Target SDK verification
- Signed Android App Bundle (AAB)
- Release signing key kept outside Git

### Apple App Store
- Apple Developer Program account
- Bundle ID: com.naqaaholding.market
- App icon and screenshots
- Privacy Policy URL
- App Privacy declarations
- Signed archive
- App Store metadata

## Financial production gate
Do not enable real-money settlement until legal/compliance approval, KYC/KYB controls, persistent production database, provider contract verification, webhook verification, reconciliation and security testing are complete.
