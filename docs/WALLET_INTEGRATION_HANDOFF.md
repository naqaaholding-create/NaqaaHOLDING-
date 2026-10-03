# NAQAA Market — SafePal / Reown Mobile Wallet Integration

## Current web integration
- `static/wallet.html` supports EIP-1193 when SafePal Extension or an injected provider is available.
- The no-login test page is `static/safepal-test.html`.
- No seed phrase or private key is ever requested.

## Mobile integration
SafePal supports DApp access on mobile and recommends mobile-friendly Web3 integration, Android/iOS testing, and deep links.

For the Capacitor Android/iOS app, NAQAA will use a maintained WalletConnect/Reown integration with:
- Project ID: `REOWN_PROJECT_ID`
- App URL: `REOWN_APP_URL`
- Native redirect scheme: `com.naqaaholding.market`
- Allowed chain: BNB Smart Chain, Chain ID 56
- Result used by NAQAA: public EVM address only

Reown documents that the Project ID is obtained from Reown Cloud, recommends an allowlist of web origins/application IDs, and recommends environment configuration rather than committing project credentials to the repository.

## What is needed from the owner
1. Create/sign in to Reown Cloud: https://cloud.reown.com/
2. Create a project for NAQAA Market.
3. Add the production web origin and the mobile application/bundle identifiers to the Project ID allowlist.
4. Send only the Project ID to the deployment configuration. Never send a wallet seed phrase/private key.
5. We then configure Android/iOS WalletConnect/Reown connection and test SafePal on both platforms.

## Security boundary
- No wallet seed/private key is stored or transmitted to NAQAA.
- Connecting a wallet does not authorize a withdrawal.
- Real-money settlement remains disabled.
- Withdrawals remain disabled until provider, finance, compliance, production storage, webhook, and reconciliation gates are approved.
