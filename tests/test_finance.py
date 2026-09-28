import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DB = Path(__file__).parent / "test_runtime.db"
if DB.exists():
    DB.unlink()

os.environ["DATABASE_PATH"] = str(DB)
os.environ["NAQAA_ADMIN_KEY"] = "ci-admin-key"
os.environ["REAL_MONEY_ENABLED"] = "0"

from app import app, db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    if DB.exists():
        DB.unlink()


def register_login(client, role, email):
    payload = {
        "role": role,
        "name": role.title(),
        "email": email,
        "password": "StrongPass123!",
        "account_type": "individual",
        "identity_type": "passport",
        "identity_number": "P12345678",
        "identity_country": "US",
    }
    r = client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass123!"})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["user"]["id"], data["token"]


def test_fresh_database_has_marketplace_finance_columns(client):
    c = db()
    cols = {row[1] for row in c.execute("PRAGMA table_info(orders)").fetchall()}
    c.close()
    required = {
        "buyer_fee_cents", "seller_fee_cents", "buyer_total_cents",
        "seller_net_cents", "payment_reference", "paid_at",
        "commission_rule_id", "buyer_rate_bps", "seller_rate_bps",
    }
    assert required <= cols


def test_deposit_is_pending_and_does_not_increase_balance(client):
    account_id, token = register_login(client, "buyer", "deposit@example.com")
    headers = {"Idempotency-Key": "deposit-1000"}
    r = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        headers=headers,
        json={"account_id": account_id, "amount": 1000, "currency": "USD"},
    )
    assert r.status_code == 200, r.text
    request_id = r.json()["request_id"]

    w = client.get(f"/api/v1/wallet/{account_id}", params={"token": token})
    assert w.status_code == 200
    assert w.json()["balance"] == 0.0
    assert w.json()["pending_deposit"] == 1000.0

    duplicate = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        headers=headers,
        json={"account_id": account_id, "amount": 1000, "currency": "USD"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["request_id"] == request_id


def test_approval_then_withdrawal_limits_and_hold_release(client):
    account_id, token = register_login(client, "buyer", "withdraw@example.com")

    dep = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        headers={"Idempotency-Key": "dep-withdraw"},
        json={"account_id": account_id, "amount": 1000, "currency": "USD"},
    )
    request_id = dep.json()["request_id"]

    approved = client.post(
        f"/api/v1/admin/wallet-requests/{request_id}/approve",
        headers={"X-Admin-Key": "ci-admin-key"},
    )
    assert approved.status_code == 200, approved.text

    too_much = client.post(
        "/api/v1/wallet/withdraw-request",
        params={"token": token},
        headers={"Idempotency-Key": "withdraw-too-much"},
        json={"account_id": account_id, "amount": 10000, "currency": "USD"},
    )
    assert too_much.status_code == 400

    wd = client.post(
        "/api/v1/wallet/withdraw-request",
        params={"token": token},
        headers={"Idempotency-Key": "withdraw-600"},
        json={"account_id": account_id, "amount": 600, "currency": "USD"},
    )
    assert wd.status_code == 200, wd.text
    wid = wd.json()["request_id"]

    w = client.get(f"/api/v1/wallet/{account_id}", params={"token": token}).json()
    assert w["balance"] == 1000.0
    assert w["held"] == 600.0
    assert w["available"] == 400.0

    rejected = client.post(
        f"/api/v1/admin/wallet-requests/{wid}/reject",
        headers={"X-Admin-Key": "ci-admin-key"},
    )
    assert rejected.status_code == 200, rejected.text

    w = client.get(f"/api/v1/wallet/{account_id}", params={"token": token}).json()
    assert w["balance"] == 1000.0
    assert w["held"] == 0.0
    assert w["available"] == 1000.0


def test_wallet_payment_is_double_entry_and_idempotent(client):
    buyer_id, buyer_token = register_login(client, "buyer", "paybuyer@example.com")
    seller_id, seller_token = register_login(client, "seller", "payseller@example.com")

    dep = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "dep-pay"},
        json={"account_id": buyer_id, "amount": 1000, "currency": "USD"},
    )
    assert dep.status_code == 200
    rid = dep.json()["request_id"]
    assert client.post(
        f"/api/v1/admin/wallet-requests/{rid}/approve",
        headers={"X-Admin-Key": "ci-admin-key"},
    ).status_code == 200

    headers = {"Idempotency-Key": "pay-100"}
    pay = client.post(
        "/api/v1/wallet/pay",
        params={"token": buyer_token},
        headers=headers,
        json={
            "from_account_id": buyer_id,
            "to_account_id": seller_id,
            "amount": 100,
            "currency": "USD",
            "description": "test payment",
        },
    )
    assert pay.status_code == 200, pay.text
    ref = pay.json()["reference"]

    duplicate = client.post(
        "/api/v1/wallet/pay",
        params={"token": buyer_token},
        headers=headers,
        json={
            "from_account_id": buyer_id,
            "to_account_id": seller_id,
            "amount": 100,
            "currency": "USD",
            "description": "test payment",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["reference"] == ref

    buyer_wallet = client.get(f"/api/v1/wallet/{buyer_id}", params={"token": buyer_token}).json()
    seller_wallet = client.get(f"/api/v1/wallet/{seller_id}", params={"token": seller_token}).json()
    assert buyer_wallet["balance"] == 900.0
    assert seller_wallet["balance"] == 100.0

    journal = client.get(f"/api/v1/finance/journal/{ref}").json()
    assert journal.get("total_debit_cents", journal.get("debit_cents")) == journal.get("total_credit_cents", journal.get("credit_cents")) == 10000

    recon = client.get("/api/v1/finance/reconciliation").json()
    assert recon["ok"] is True


def test_financial_endpoints_require_idempotency_and_admin_key(client):
    account_id, token = register_login(client, "buyer", "security@example.com")
    r = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        json={"account_id": account_id, "amount": 10, "currency": "USD"},
    )
    assert r.status_code == 400

    r = client.post(
        "/api/v1/admin/wallet-requests/not-found/approve",
        headers={"X-Admin-Key": "wrong"},
    )
    assert r.status_code == 403


def test_commission_fee_rounding_minimum_maximum_and_fixed():
    from app import fee_cents
    # 1% of $10 = $0.10, but minimum $2.00 applies; fixed $0.50 is included.
    assert fee_cents(1000, 100, 50, 200, None) == 200
    # 1% of $100 = $1.00 + $0.50 = $1.50; minimum still applies.
    assert fee_cents(10000, 100, 50, 200, None) == 200
    # 10% of $100 = $10 + $1 fixed, capped at $5 maximum.
    assert fee_cents(10000, 1000, 100, 0, 500) == 500
    # Exact integer-cent calculation: 1.005% of $100 rounds to $1.01 before fixed fee.
    assert fee_cents(10000, 100.5, 0, 0, None) == 101
