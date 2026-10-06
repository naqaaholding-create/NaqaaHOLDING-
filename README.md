# NAQAA HOLDING — NAQAA Market

Production-oriented bilingual marketplace for NAQAA HOLDING.

## Current production safety state

**Real-money settlement is OFF by default.** The application contains a production safety gate so setting REAL_MONEY_ENABLED=1 alone cannot activate financial settlement.

The live-money gate requires all of the following to be explicitly configured:

- PRODUCTION_APPROVED=1
- KYC_KYB_PRODUCTION_APPROVED=1
- DATABASE_PERSISTENT=1
- NAQAA_ADMIN_KEY set as a secret
- SafePal external wallet connection configured through Reown
- SafePal is used only as the external wallet; NAQAA internal wallet remains the accounting source of truth
- Production webhook verification and operational monitoring
- Legal/compliance approval for the jurisdiction and services being offered

The endpoint /api/v1/production/readiness reports remaining configuration blockers without exposing secrets. It does not activate money.

## Financial architecture

- Double-entry marketplace ledger
- Buyer and seller wallets
- Commission snapshot at order acceptance
- Escrow state machine
- Refund/dispute controls
- Idempotency protections
- Reconciliation checks
- KYC/KYB approval gates
- Internal multi-asset crypto wallet
- SafePal external-wallet connection and ownership proof
- Provider-independent ledger and idempotency controls

## External wallet

**External Wallet** is the approved wallet feature name. Reown is the connection layer for BSC/BEP20 public wallet addresses and ownership proof. The app never requests or stores a seed phrase, private key, recovery phrase, or wallet password.

Real blockchain transfers and real-money settlement remain disabled until the production finance/provider gates are independently completed and verified.


## Deployment

### Sandbox

The supplied Render configuration keeps:

- REAL_MONEY_ENABLED=0
- SAFEPAL_EXTERNAL_WALLET=0
- DATABASE_PERSISTENT=0
- production approval flags at 0

This is intentional.

### Production requirements

The current Render service uses SQLite. A real-money deployment must use persistent production storage and must not rely on an ephemeral/free runtime. Before enabling money, move the database to a persistent production setup (or a properly mounted persistent volume) and set DATABASE_PERSISTENT=1.

Also configure secrets only through the hosting provider's secret/environment mechanism. Never commit API keys, webhook secrets, admin keys, seed phrases or private keys to Git.

### Go-live sequence

1. Confirm the legal entity, jurisdiction, terms, privacy policy, AML/KYC/KYB process and transaction/refund/dispute rules.
2. Complete the SafePal merchant/API onboarding and verify the exact Payment/Payout API contract.
3. Map the exact provider authentication and webhook signature verification into cwallet_provider.py.
4. Use persistent production storage and backups.
5. Set a strong NAQAA_ADMIN_KEY outside Git.
6. Run the full financial regression suite in CI and a provider sandbox/integration test.
7. Verify deposits, withdrawals, webhook replay protection, amount/network/asset mismatch handling, refunds, disputes and reconciliation.
8. Only after all checks pass, set the production approval flags and provider secrets in the hosting platform.
9. Enable REAL_MONEY_ENABLED=1 as the final controlled change.
10. Monitor the first transactions manually and keep withdrawal approval controls enabled.

## API

- Health: /health
- Status: /api/v1/status
- Production readiness: /api/v1/production/readiness
- Wallet: /wallet
- Crypto API: /api/v1/crypto/*
- SafePal API: /api/v1/cwallet/*

## Local run

pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port $PORT

Financial tests are run through GitHub Actions.

## Production readiness

- Live money is OFF by default.
- Production preflight: `/api/v1/production/preflight`
- Production deployment blueprint: `render.production.yaml`
- Go-live runbook: `docs/PRODUCTION_GO_LIVE.md`
- Never place provider API secrets in source control.
