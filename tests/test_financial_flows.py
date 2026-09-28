import os
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("NAQAA_ADMIN_KEY", "test-admin-key")
    monkeypatch.setenv("REAL_MONEY_ENABLED", "0")

    import app
    importlib.reload(app)
    return TestClient(app.app)


def register(client, email, role):
    r = client.post("/api/v1/auth/register", json={
        "role": role,
        "name": role.title(),
        "email": email,
        "password": "StrongPass123",
        "account_type": "individual",
        "dob": "1990-01-01",
        "nationality": "Test",
        "phone": "+10000000000",
        "identity_type": "passport",
        "identity_number": "P12345678",
        "identity_country": "TEST",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def login(client, email):
    r = client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "StrongPass123",
    })
    assert r.status_code == 200, r.text
    return r.json()["token"]


def approve(client, request_id):
    r = client.post(
        f"/api/v1/admin/wallet-requests/{request_id}/approve",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert r.status_code == 200, r.text


def test_deposit_is_pending_until_admin_approval(client):
    account = register(client, "buyer@example.test", "buyer")
    token = login(client, "buyer@example.test")

    r = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        headers={"Idempotency-Key": "dep-1"},
        json={"account_id": account, "amount": 1000, "currency": "USD"},
    )
    assert r.status_code == 200
    request_id = r.json()["request_id"]

    wallet = client.get(f"/api/v1/wallet/{account}", params={"token": token})
    assert wallet.status_code == 200
    assert wallet.json()["balance_cents"] == 0
    assert wallet.json()["pending_deposit_cents"] == 100000

    approve(client, request_id)

    wallet = client.get(f"/api/v1/wallet/{account}", params={"token": token}).json()
    assert wallet["balance_cents"] == 100000
    assert wallet["available_cents"] == 100000


def test_withdrawal_cannot_exceed_available_balance(client):
    account = register(client, "buyer2@example.test", "buyer")
    token = login(client, "buyer2@example.test")

    dep = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        headers={"Idempotency-Key": "dep-2"},
        json={"account_id": account, "amount": 1000, "currency": "USD"},
    ).json()
    approve(client, dep["request_id"])

    r = client.post(
        "/api/v1/wallet/withdraw-request",
        params={"token": token},
        headers={"Idempotency-Key": "wd-too-large"},
        json={"account_id": account, "amount": 10000, "currency": "USD"},
    )
    assert r.status_code == 400
    assert "insufficient available balance" in r.json()["detail"]

    wallet = client.get(f"/api/v1/wallet/{account}", params={"token": token}).json()
    assert wallet["balance_cents"] == 100000
    assert wallet["held_cents"] == 0


def test_withdrawal_hold_reserves_available_balance(client):
    account = register(client, "buyer3@example.test", "buyer")
    token = login(client, "buyer3@example.test")

    dep = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        headers={"Idempotency-Key": "dep-3"},
        json={"account_id": account, "amount": 1000, "currency": "USD"},
    ).json()
    approve(client, dep["request_id"])

    r = client.post(
        "/api/v1/wallet/withdraw-request",
        params={"token": token},
        headers={"Idempotency-Key": "wd-3"},
        json={"account_id": account, "amount": 600, "currency": "USD"},
    )
    assert r.status_code == 200

    r2 = client.post(
        "/api/v1/wallet/withdraw-request",
        params={"token": token},
        headers={"Idempotency-Key": "wd-4"},
        json={"account_id": account, "amount": 500, "currency": "USD"},
    )
    assert r2.status_code == 400

    wallet = client.get(f"/api/v1/wallet/{account}", params={"token": token}).json()
    assert wallet["balance_cents"] == 100000
    assert wallet["held_cents"] == 60000
    assert wallet["available_cents"] == 40000


def test_idempotency_key_cannot_be_reused_for_different_request(client):
    account = register(client, "buyer4@example.test", "buyer")
    token = login(client, "buyer4@example.test")

    first = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        headers={"Idempotency-Key": "same-key"},
        json={"account_id": account, "amount": 100, "currency": "USD"},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": token},
        headers={"Idempotency-Key": "same-key"},
        json={"account_id": account, "amount": 200, "currency": "USD"},
    )
    assert second.status_code == 409


def test_wallet_payment_is_double_entry_and_reconciles(client):
    buyer = register(client, "buyer5@example.test", "buyer")
    seller = register(client, "seller5@example.test", "seller")
    buyer_token = login(client, "buyer5@example.test")

    dep = client.post(
        "/api/v1/wallet/deposit-request",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "dep-5"},
        json={"account_id": buyer, "amount": 250, "currency": "USD"},
    ).json()
    approve(client, dep["request_id"])

    pay = client.post(
        "/api/v1/wallet/pay",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "pay-5"},
        json={
            "from_account_id": buyer,
            "to_account_id": seller,
            "amount": 75,
            "currency": "USD",
            "description": "test payment",
        },
    )
    assert pay.status_code == 200, pay.text
    reference = pay.json()["reference"]

    buyer_wallet = client.get(f"/api/v1/wallet/{buyer}", params={"token": buyer_token}).json()
    assert buyer_wallet["balance_cents"] == 17500

    recon = client.get("/api/v1/finance/reconciliation")
    assert recon.status_code == 200
    assert recon.json()["ok"] is True

    journal = client.get(f"/api/v1/finance/journal/{reference}")
    assert journal.status_code == 200
    lines = journal.json()["lines"]
    assert sum(x["amount_cents"] for x in lines if x["side"] == "debit") == 7500
    assert sum(x["amount_cents"] for x in lines if x["side"] == "credit") == 7500
