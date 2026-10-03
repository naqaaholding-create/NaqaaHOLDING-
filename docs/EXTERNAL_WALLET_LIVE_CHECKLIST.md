# External Wallet — Live Acceptance Checklist

## Scope
External Wallet uses Reown for connection and only stores the public wallet address.

- Provider: Reown
- Network: BSC / BEP20
- Chain ID: 56
- Asset: USDT
- Seed phrase: never requested
- Private key: never requested
- Real external transfers: disabled until production approval gates are complete

## Required configuration
1. Set `REOWN_PROJECT_ID` in the deployment environment.
2. Set `REOWN_APP_URL` to the deployed application URL.
3. Keep all real-money production gates disabled until the live acceptance test is complete.

## Live test
1. Open the wallet page while logged in with a verified email.
2. Confirm the page reports that Reown is configured.
3. Press **Connect Wallet**.
4. Connect a compatible wallet.
5. Select **BSC / BEP20**.
6. Confirm the page displays the public address and BSC status.
7. Confirm the address is linked to the authenticated account.
8. Disconnect the wallet.
9. Confirm the UI returns to the disconnected state.
10. Reconnect the same wallet.
11. Confirm the address links idempotently and no duplicate address record is created.
12. Switch to a non-BSC network and confirm the UI refuses to link the address.
13. Confirm no seed phrase or private key is requested at any point.
14. Confirm real external USDT transfers remain unavailable.

## Production security gate
Before enabling any external payout/settlement:
- Require a wallet-ownership proof (wallet signature/nonce challenge), not only a supplied address. **Implemented:** `/external-wallet/challenge` + `/external-wallet/verify` with one-time 10-minute challenges.
- Verify the signature server-side and bind the verified address to the authenticated account. **Implemented:** EVM `personal_sign` recovery with `eth-account`, account binding, address matching, expiry and replay protection.
- Verify provider/webhook and live-contract configuration.
- Enable production finance gates only after independent verification.
- Re-run the full acceptance test against production infrastructure.

## Implementation status

The ownership-proof backend and Reown signing flow are implemented. Real browser/wallet acceptance remains the final live step.

## Current limitation
A repository test can validate API behavior and configuration, but the Connect/BSC/Disconnect/Reconnect flow requires a real browser, a real Reown Project ID, and a compatible BSC wallet.