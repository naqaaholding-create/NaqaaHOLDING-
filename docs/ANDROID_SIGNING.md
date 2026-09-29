# NAQAA Market Android signing

## Signing model

The repository does not contain a keystore or private signing key.

The Release workflow can sign the AAB when these GitHub Actions secrets are configured:

- ANDROID_KEYSTORE_BASE64
- ANDROID_KEYSTORE_PASSWORD
- ANDROID_KEY_ALIAS
- ANDROID_KEY_PASSWORD

The keystore should be generated and retained by the company owner. Never commit the keystore, passwords, or private key to Git.

## Recommended Google Play setup

Use Google Play App Signing. The local/company key is the upload key; Google manages the app-signing key after enrollment.

## Generate an upload keystore

On a trusted computer with Java installed:

    keytool -genkeypair -v -keystore naqaa-market-upload.jks -alias naqaa-market-upload -keyalg RSA -keysize 4096 -validity 10000

Keep the .jks file and both passwords in a secure password manager.

For GitHub Actions, encode the keystore as Base64 locally and add the resulting value as the GitHub secret ANDROID_KEYSTORE_BASE64. Add the three password/alias values as the other secrets.

Do not paste any of these secrets into ChatGPT.

## Package identity

Application ID:

    com.naqaaholding.market

App name:

    NAQAA Market

Initial release:

    versionCode 1
    versionName 1.0.0
