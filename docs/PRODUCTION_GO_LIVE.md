# NAQAA Market — Production Go-Live Runbook

## Current verified safety state

**Real-money settlement must remain OFF.** The repository's application gate defaults `REAL_MONEY_ENABLED=0`. Do not enable live settlement until provider integration, compliance, persistent storage, and operational controls have been independently verified.

A connected SafePal wallet proves wallet connectivity/ownership only; it does not prove that a payment was received or settled.

## Required production infrastructure

1. Deploy the API to a stable HTTPS endpoint.
2. Use persistent storage. The production Render blueprint uses a disk mounted at `/var/data` and SQLite at `/var/data/naqaa_market.db`.
3. Keep a single service instance when using this SQLite persistent disk.
4. Configure `DATABASE_PATH=/var/data/naqaa_market.db` and `DATABASE_PERSISTENT=1`.
5. Configure a strong `NAQAA_ADMIN_KEY` as a host secret, never in source control.
6. Configure backups and test restoring them before any live financial use.
7. Verify the actual deployed API health and readiness endpoints; a repository blueprint does not prove that a service is deployed or healthy.

## SafePal / Reown integration status

Reown is the wallet connection layer. The repository's external-wallet ownership proof supports a public USDT address on BSC/BEP20. That flow is not, by itself, a merchant payment or payout integration.

The repository does not establish a verified SafePal merchant payment API contract or a verified payment-webhook signature scheme. Do not invent endpoint names, credentials, environment-variable names, or signature rules.

Before implementing live settlement:

1. Obtain the official SafePal merchant/payment API documentation and credentials from the provider.
2. Confirm the supported assets, networks, payment creation response, provider transaction identifier, and webhook authentication/signature rules.
3. Implement the documented API contract in a dedicated provider adapter.
4. Verify payment status server-side against the provider's authenticated event or reconciliation API. Never treat a browser redirect or a wallet connection as proof of payment.
5. Validate transaction ID, recipient, asset, network, amount, order reference, confirmations, replay protection, refunds, and reconciliation.
6. Add automated tests using documented provider fixtures or a provider sandbox before any live transaction.

Until those steps are completed and verified, provider settlement must remain disabled. Do not replace SafePal/Reown with CWallet.

## Application safety-gate variables

The current `app.py` gate checks these values before live financial operations can be enabled:

- `REAL_MONEY_ENABLED=1` (final activation switch; keep at `0` until approved)
- `PRODUCTION_APPROVED=1`
- `FINANCE_PRODUCTION_APPROVED=1`
- `KYC_KYB_PRODUCTION_APPROVED=1`
- `DATABASE_PERSISTENT=1`
- `DATABASE_PATH` is set
- `NAQAA_ADMIN_KEY` is set as a secret

Passing this application gate does **not** independently prove that the SafePal payment provider, compliance process, or production deployment is ready. Those must be verified separately. Never set approval flags merely to bypass a blocker.

Check the deployed readiness endpoint, if available:

`GET /api/v1/production/readiness`

The runbook also mentions `/api/v1/production/preflight`; confirm that route exists in the current application before relying on it.

## Operational checks before first live transaction

- Complete applicable legal, entity, KYC/KYB, privacy, and provider onboarding.
- Verify production storage persistence, backups, and restoration.
- Test that a successful payment is credited exactly once.
- Test wrong network, wrong asset, wrong amount, wrong recipient, unknown transaction ID, duplicate/replayed event, and invalid webhook signature.
- Verify refunds, disputes, escrow transitions, withdrawal controls, and ledger reconciliation.
- Compare the provider's transaction record with the NAQAA ledger independently.
- Conduct a small controlled production test only after all prior checks pass, with manual monitoring and a rollback plan.

## Accounting rule

NAQAA's ledger is the accounting source of truth for marketplace balances, commissions, and escrow state. External wallet connectivity must never directly create a money credit without verified transaction evidence and an idempotent server-side settlement path.

## Rollback

If provider verification, webhook authentication, reconciliation, or payout checks fail:

1. Set live settlement to OFF.
2. Stop new payment intents if needed.
3. Preserve provider events and audit logs.
4. Reconcile provider transactions against the NAQAA ledger.
5. Resolve outstanding escrow and dispute states before reopening live settlement.
