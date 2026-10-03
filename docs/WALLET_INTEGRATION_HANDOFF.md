# NAQAA Market — External Wallet / Reown Integration

## Current integration
- web/crypto-wallet.html exposes the External Wallet flow.
- Reown AppKit is used for the wallet connection.
- Allowed network: BNB Smart Chain / BSC, Chain ID 56 (BEP20).
- The only wallet value consumed by NAQAA is the public EVM address.
- No Seed Phrase, Private Key, Recovery Phrase, or wallet password is requested or stored.
- Real blockchain transfers remain disabled.

## Configuration
- Project ID: REOWN_PROJECT_ID
- App URL: REOWN_APP_URL
- Native redirect scheme: com.naqaaholding.market

The Reown Project ID is obtained from the Reown Dashboard and should be supplied through deployment configuration. Do not commit wallet secrets or private keys.

## Account linking
After a successful BSC connection, the frontend can link the public address through POST /api/v1/crypto/external-address with asset USDT and network BEP20. The backend validates the EVM address and requires verified email before linking.

## Production gate
Connecting a wallet does not authorize a withdrawal. Real-money settlement and blockchain transfers stay disabled until provider, compliance, persistent storage, webhook verification, reconciliation, and production approval gates are independently completed and tested.
