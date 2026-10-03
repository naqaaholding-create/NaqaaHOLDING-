# NAQAA Market — SafePal Wallet Integration Handoff

## Implemented
- Added **Connect SafePal / Browser Wallet** to `static/wallet.html`.
- Uses the browser's EIP-1193 wallet provider (`window.ethereum`) when available.
- Requests the public account address only.
- Requires **BNB Smart Chain / Chain ID 56** and attempts a network switch when supported.
- Fills the existing public-address linking flow with:
  - provider: SafePal
  - network: BEP20
  - asset: USDT
- No seed phrase, private key, wallet password, or signing secret is requested or stored.

## Developer next step
For mobile in-app SafePal/WalletConnect QR/deep-link support, configure a WalletConnect/Reown project ID as a server/build secret and add the maintained WalletConnect integration appropriate to the Capacitor/WebView build. Do not hard-code API secrets or private keys in the repository.

Required production checks:
1. Verify SafePal/WalletConnect connection on Android and iOS.
2. Verify returned address is a valid BSC address.
3. Verify the selected chain is 56 before linking.
4. Link only the public address through `/api/v1/crypto/external-address`.
5. Keep withdrawals disabled until provider, compliance, and production finance gates are approved.
6. Never request or store seed phrase/private key.

## Current branch
`release/naqaa-publishable-v1`

## Commit
`6673561cae8ba57a1be84ad2717e525590ff9fbb`
