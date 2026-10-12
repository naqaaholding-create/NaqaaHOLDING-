# NAQAA Market — Production Go-Live Runbook

## Current state

The repository contains marketplace accounting and escrow-related code, an internal wallet, a SafePal/Reown external-wallet connection flow, idempotency controls and readiness endpoints.

**Real-money settlement must remain OFF.** The current API explicitly rejects live webhook processing with HTTP 501 because provider webhook verification is not configured. The preflight field `safe_pal_external_wallet` is currently a placeholder value, not evidence of a verified SafePal production integration.

## Required production infrastructure

1. Deploy the API as a production web service.
2. Use persistent storage. The included `render.production.yaml` specifies a persistent disk at `/var/data` and SQLite at `/var/data/naqaa_market.db`.
3. Keep exactly one service instance when using the SQLite persistent disk.
4. Configure HTTPS and a stable API domain.
5. Configure `DATABASE_PATH=/var/data/naqaa_market.db`.
6. Configure a strong `NAQAA_ADMIN_KEY` as a secret.
7. Keep backups and test restoration before accepting real funds.
8. Configure the production API URL in the static/mobile client. GitHub Pages alone does not host this Python API.

## SafePal / Reown requirements

Reown wallet connection and signing a wallet-ownership challenge are not the same as a payment processor integration. A connected wallet address alone does not prove that a subscription or listing fee was paid.

Before enabling any live payment or payout, establish the exact intended flow and verify the relevant SafePal/network capabilities. If using a provider/merchant API, obtain its official API and webhook documentation first. Implement and test:

- server-side payment-intent creation and a stable provider transaction/reference ID;
- verification of chain, token contract, amount, destination and transaction finality;
- authenticated provider events or independently verified on-chain transactions;
- duplicate-event idempotency and reconciliation;
- payout destination and amount validation, with audit records;
- failure, refund, timeout and dispute handling.

Never request or store a wallet seed phrase or private key. Never treat a browser redirect, screenshot, or client-supplied success flag as proof of payment.

## Finance safety gates

The API's live-finance gate checks these environment variables:

- `REAL_MONEY_ENABLED=1`
- `FINANCE_PRODUCTION_APPROVED=1`
- `PRODUCTION_APPROVED=1`
- `KYC_KYB_PRODUCTION_APPROVED=1`
- `DATABASE_PERSISTENT=1`
- `DATABASE_PATH` explicitly configured
- `NAQAA_ADMIN_KEY` configured

Passing these checks is not sufficient to launch: the provider verification, legal/compliance review, operational controls and live transaction tests must also be complete. Do not turn the flags on merely to make the readiness endpoint green.

Check readiness:

`GET /api/v1/production/preflight`

The current live webhook endpoint returns HTTP 501 when live mode is enabled because verified provider webhook handling has not been implemented. Treat that as a launch blocker.

## Operational checks before first live transaction

- Complete business/entity KYC/KYB and provider onboarding as applicable.
- Confirm supported assets, token contracts and networks.
- Verify payment amount, asset, network, recipient and order reference server-side.
- Confirm duplicate provider events cannot credit twice.
- Reject unknown or unverified transaction IDs.
- Confirm payout destinations and amounts are validated before payout.
- Confirm refunds and escrow transitions are atomic.
- Run reconciliation and confirm zero imbalance.
- Perform a small controlled production test transaction.
- Verify the external transaction and NAQAA ledger independently.
- Open live customer traffic only after all checks pass.

## Accounting rule

NAQAA's ledger should remain the source of truth for marketplace accounting, commissions and escrow state. The external wallet/payment flow must be reconciled against verifiable transactions; it must not create ledger credit solely from client input.

## Rollback

If provider verification, reconciliation or payout checks fail:

1. Set live settlement back to OFF.
2. Stop new payment intents if necessary.
3. Preserve provider event records and audit logs.
4. Reconcile provider transactions against the NAQAA ledger.
5. Resolve outstanding escrow/dispute states before reopening live settlement.
