# NAQAA Market v6.1

Bilingual-ready NAQAA Market sandbox web application.

- FastAPI backend
- Real login + buyer/seller registration
- PBKDF2 password hashing
- Session tokens
- Public marketplace landing page
- SQLite for sandbox/demo
- `REAL_MONEY_ENABLED=0`
- `STRIPE_LIVE_ENABLED=0`
- No payment/KYC provider is live in this build

## Render
Build: `pip install -r requirements.txt`
Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`
Health: `/health`
