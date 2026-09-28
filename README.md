# NAQAA HOLDING — NAQAA Market v6.2

Bilingual-ready marketplace for NAQAA HOLDING.

## Included
- FastAPI backend
- Buyer and seller registration/login
- Wallet for every account
- Wallet-only marketplace payments
- Deposit and withdrawal requests
- Wallet transaction ledger
- Listings and offers
- Electronic-contract hash records
- Duplicate webhook protection
- Mobile-first NAQAA visual identity
- `/robots.txt` and `/sitemap.xml`

## Money model
- Card checkout: disabled
- Stripe live: disabled
- Real-money movement: disabled until legal/provider approval, KYC/KYB and verified production webhooks are completed
- Deposit/withdrawal requests remain pending in the current sandbox build
- Wallet-to-wallet payment logic is internal ledger logic and must not be presented as live money until production controls are enabled

## Run
`pip install -r requirements.txt`
`uvicorn app:app --host 0.0.0.0 --port $PORT`

Health: `/health`
Status: `/api/v1/status`


Financial regression CI enabled.
