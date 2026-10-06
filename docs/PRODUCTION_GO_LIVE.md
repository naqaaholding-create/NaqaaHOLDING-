# NAQAA Market — Production Go-Live Runbook

## Current state

The repository contains the marketplace ledger, internal wallet, External Wallet/Reown connection, idempotency controls, audit logging, reconciliation and regression tests.

**Real-money settlement remains OFF by default.** Do not change the live flags until the payment/provider contract, compliance/KYC/KYB, persistent storage and operational controls have been verified.

## Required production infrastructure

1. Deploy the API as a paid Render web service.
2. Use persistent storage. The production blueprint uses a persistent disk at `/var/data` and stores SQLite at `/var/data/naqaa_market.db`.
3. Keep exactly one service instance when using the SQLite persistent disk.
4. Configure HTTPS and a stable API domain.
5. Configure `DATABASE_PATH=/var/data/naqaa_market.db`.
6. Configure a strong `NAQAA_ADMIN_KEY` as a secret.
7. Keep backups and test restoration before accepting real funds.

## External Wallet / Reown requirements

External Wallet uses Reown only as the connection layer for the public EVM address.

- Network: BSC / BEP20 (Chain ID 56).
- Asset: USDT.
- Only the public wallet address is consumed by NAQAA.
- No seed phrase, private key, recovery phrase or wallet password is requested or stored.
- Wallet ownership is proved with a one-time signature challenge.
- Connecting a wallet does not authorize a withdrawal or blockchain transfer.
- Real external transfers remain disabled until production approval gates are complete.

Required deployment configuration:
- `REOWN_PROJECT_ID`
- `REOWN_APP_URL`

## Finance/payment gates

Live money requires all relevant production gates to be explicitly approved and configured, including:

- `REAL_MONEY_ENABLED=1`
- `FINANCE_PRODUCTION_APPROVED=1`
- `PRODUCTION_APPROVED=1`
- `KYC_KYB_PRODUCTION_APPROVED=1`
- persistent `DATABASE_PATH`
- `NAQAA_ADMIN_KEY`
- a verified production payment/provider contract and webhook/reconciliation process

Check readiness first:

`GET /api/v1/production/preflight`

If any blocker is returned, **do not activate real money**.

## Operational checks before first live transaction

- Complete business/entity KYC/KYB and provider onboarding.
- Confirm supported assets, networks and payment methods in the production provider account.
- Confirm payment amount, asset, network and order reference are matched server-side.
- Confirm duplicate provider events are idempotent.
- Confirm unknown provider transaction IDs are rejected.
- Confirm payout destination and amount are validated before payout.
- Confirm refunds and escrow transitions remain atomic.
- Run reconciliation and confirm zero imbalance.
- Perform a small controlled production test transaction.
- Verify the provider transaction and the NAQAA ledger independently.
- Only then open live customer traffic.

## Architecture rule

The NAQAA double-entry ledger remains the source of truth for marketplace accounting, commissions and escrow state.

Never treat a browser redirect as proof of payment. Settlement must occur only from a verified provider event or a controlled provider reconciliation process.

## Rollback

If any provider, webhook, reconciliation or payout check fails:

1. Set live settlement back to OFF.
2. Stop new payment intents if necessary.
3. Preserve provider event records and audit logs.
4. Reconcile provider transactions against the provider and NAQAA ledger.
5. Resolve outstanding escrow/dispute states before reopening live settlement.
