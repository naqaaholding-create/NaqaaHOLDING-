# Android release signing — NAQAA

This checklist prepares Android signing for the manually triggered workflow:
`.github/workflows/mobile-android-release.yml`.

**Signing an APK does not make the app production-ready and does not enable real-money collection.** Keep payment collection disabled until the backend, persistent database, and on-chain payment verification have been independently tested.

## 1. Create and protect the signing keystore

Use a trusted computer with Java's `keytool` available. In a private folder (not inside the repository), run:

```sh
keytool -genkeypair -v -keystore naqaa-release.jks -alias naqaa-release -keyalg RSA -keysize 2048 -validity 10000
```

Choose strong, unique passwords and store them securely. Keep at least one secure backup of `naqaa-release.jks`. **If this keystore is lost, future updates may not be installable as updates to the same app.** Do not upload the keystore to GitHub, commit it, or send it in chat. Do not regenerate a replacement after publishing.

## 2. Prepare the Base64 keystore value locally

Convert the keystore file to one Base64 string on your own computer. For PowerShell:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes(".\naqaa-release.jks")) | Set-Clipboard
```

Paste that value directly into the GitHub secret named below. Do not paste it into an issue, pull request, chat, source file, or log. Clear the clipboard afterward.

## 3. Add GitHub Actions repository secrets

Open the repository's **Settings → Secrets and variables → Actions → New repository secret**:
https://github.com/naqaaholding-create/NaqaaHOLDING-/settings/secrets/actions

Create these four secrets (names must match exactly):

- `ANDROID_KEYSTORE_BASE64` — the Base64 value from step 2.
- `ANDROID_KEYSTORE_PASSWORD` — keystore password.
- `ANDROID_KEY_ALIAS` — the alias chosen in step 1 (example: `naqaa-release`).
- `ANDROID_KEY_PASSWORD` — password for that key.

Never put secret values in the repository or share them in ChatGPT. If your account does not show repository Settings or cannot create Actions secrets, stop and ask the repository owner/admin to add them.

## 4. Run a release candidate only after review

1. Review and merge the signed-release workflow pull request after its checks and workflow configuration are verified.
2. Open **Actions → NAQAA Market Android Release**:
   https://github.com/naqaaholding-create/NaqaaHOLDING-/actions/workflows/android-signed-release.yml
3. Type `RELEASE` in the confirmation field and run the workflow only when you intend to build a production-signed release candidate. The workflow now fails closed if any of the four permanent signing secrets is missing; it never substitutes a temporary test key.
4. Confirm the run succeeds, the APK signature verification passes, and both APK/AAB artifacts are present.
5. Download and install the APK on a test phone. Verify registration, login/logout, market navigation, ads, employee flow, and wallet connection. Record the result before considering distribution.

The workflow's artifacts expire after 7 days. Store release files and the keystore backup securely.

## 5. Release gate — not yet proven by signing

Before enabling real-money collection, separately verify all of the following:

- A production API is deployed and reachable over HTTPS.
- A persistent production database is configured, backed up, and tested.
- Server-side verification confirms the expected token, chain, recipient, amount, and transaction finality; duplicate/replayed transactions are rejected.
- A real test transaction has been independently confirmed on the relevant blockchain explorer.
- Admin controls, payment reconciliation, and failure/refund handling have been tested.

Until those checks have evidence, keep live collection disabled. SafePal/Reown wallet connection by itself is not proof that a payment was received.
