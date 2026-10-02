# NAQAA Market — Developer Handoff

## Handoff status
This repository is the current source of truth for the NAQAA Market web application and API.

## Included
- Bilingual Arabic/English welcome and marketplace UI.
- Account registration/login and email OTP verification.
- Buyer/seller roles.
- Listing creation with image upload and preview.
- NAQAA Giving page.
- Internal crypto wallet APIs.
- External wallet public-address linking.
- USDT on BNB Smart Chain public balance/transaction checks.
- Logout/session handling.
- FastAPI API and static frontend served from the same application.
- Render sandbox deployment configuration.

## External wallet security
The current external-wallet feature stores a public wallet address only. It must never request or store a Seed phrase, Private key, Recovery phrase, or Wallet password.
The current UI uses SafePal as a label/provider name; it is not a privileged SafePal account connection or WalletConnect session.

## Financial status
Real-money settlement is OFF by default. Do not enable live money for a store release until the provider contract, KYC/KYB, persistent storage, legal/compliance requirements, webhook verification, reconciliation, refund/dispute controls, and production secrets are independently completed and tested.

## Email verification
The backend uses Resend when RESEND_API_KEY and RESEND_FROM_EMAIL are configured. Without those secrets, registration verification must remain unavailable rather than accepting arbitrary codes.

## Deployment
For sandbox testing, use render.yaml.
For a production-grade hosted backend, use render.production.yaml with persistent storage and production secrets.

## Mobile store packaging
The web/PWA source is the functional source. A store developer should package it into native Android/iOS applications using a maintained WebView/PWA wrapper or migrate the UI into a native shell while keeping the API contract.

Before Google Play/App Store submission, the developer must add:
- App icons and splash assets from approved NAQAA brand files.
- Android application ID and signing key.
- iOS bundle identifier and Apple signing/provisioning.
- Privacy policy URL, terms, support/contact information.
- Store screenshots and descriptions.
- Production HTTPS API URL.
- Production email provider configuration.
- Store-specific compliance declarations.

## Important
Do not publish the sandbox build as a live financial product. The current code intentionally blocks live-money settlement.