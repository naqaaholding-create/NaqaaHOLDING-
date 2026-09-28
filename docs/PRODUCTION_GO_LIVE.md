# NAQAA Market — Production Go-Live Runbook

## Current state

The repository contains the marketplace ledger, escrow, crypto wallet, Cwallet integration layer, idempotency controls, audit logging, reconciliation and regression tests.

**Real-money settlement remains OFF by default.** Do not change the live flags until the provider contract, compliance/KYC/KYB, storage and operational controls have been verified.

## Required production infrastructure

1. Deploy the API as a paid Render web service.
2. Use persistent storage. The included `render.production.yaml` uses a persistent disk at `/var/data` and stores SQLite at `/var/data/naqaa_market.db`.
3. Keep exactly one service instance when using the SQLite persistent disk.
4. Configure HTTPS and a stable API domain.
5. Configure `DATABASE_PATH=/var/data/naqaa_market.db`.
6. Configure a strong `NAQAA_ADMIN_KEY` as a secret.
7. Keep backups and test restoration before accepting real funds.

## Cwallet production requirements

The current adapter deliberately does **not** assume undocumented Cwallet authentication or webhook signing details.

Before enabling live settlement, obtain the exact merchant/API contract from Cwallet and configure:

- `CWALLET_API_BASE_URL`
- `CWALLET_API_KEY`
- `CWALLET_API_SECRET`
- `CWALLET_WEBHOOK_SECRET`
- `CWALLET_PAYMENT_PATH`
- `CWALLET_PAYOUT_PATH`
- `CWALLET_ENV=production`

The payment response must be mapped to a stable provider payment ID and checkout/payment URL. Webhook processing must use the provider's documented signature/authentication method, not an assumed scheme.

## Finance gates

Live mode requires all of the following:

- `REAL_MONEY_ENABLED=1`
- `FINANCE_PRODUCTION_APPROVED=1`
- `CWALLET_LIVE_CONTRACT_VERIFIED=1`
- `CWALLET_ENABLED=1`
- `CWALLET_ENV=production`
- all Cwallet production configuration variables set
- `NAQAA_ADMIN_KEY` set
- persistent `DATABASE_PATH` set

Check readiness first:

`GET /api/v1/production/preflight`

If any blocker is returned, **do not activate real money**.

## Operational checks before first live transaction

- Complete business/entity KYC/KYB and provider onboarding.
- Confirm supported assets and networks in the Cwallet merchant account.
- Confirm payment amount, asset, network and order reference are matched server-side.
- Confirm duplicate provider events are idempotent.
- Confirm unknown provider transaction IDs are rejected.
- Confirm payout destination and amount are validated before payout.
- Confirm refunds and escrow transitions remain atomic.
- Run reconciliation and confirm zero imbalance.
- Perform a small controlled production test transaction.
- Verify the provider transaction and the NAQAA ledger independently.
- Only then open live customer traffic.

## Important architecture rule

Cwallet is the external payment/payout provider. NAQAA's double-entry ledger remains the source of truth for marketplace accounting, commissions and escrow state.

Never treat a browser redirect as proof of payment. Settlement must occur only from a verified provider event or a controlled provider reconciliation process.

## Rollback

If any provider, webhook, reconciliation or payout check fails:

1. Set live settlement back to OFF.
2. Stop new payment intents if necessary.
3. Preserve provider event records and audit logs.
4. Reconcile provider transactions against NAQAA ledger.
5. Resolve outstanding escrow/dispute states before reopening live settlement.


CI verification branch created for the production preflight regression suite.
