import os
import uuid
import pytest
from fastapi.testclient import TestClient

os.environ["CRYPTO_LIVE_ENABLED"] = "1"
os.environ["NAQAA_ADMIN_KEY"] = "ci-admin-key"

import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "crypto_api.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    app.DB = str(db_path)
    app.init_db()
    return TestClient(app.app)


def register_and_login(client, email, role):
    r = client.post("/api/v1/auth/register", json={
        "role": role, "name": role.title(), "email": email,
        "password": "StrongPass123!",
    })
    assert r.status_code == 200, r.text
    account_id = r.json()["id"]
    c = app.db()
    c.execute("UPDATE accounts SET status='approved' WHERE id=?", (account_id,))
    c.commit()
    c.close()
    r = client.post("/api/v1/auth/login", json={
        "email": email, "password": "StrongPass123!",
    })
    assert r.status_code == 200, r.text
    return account_id, r.json()["token"]


def test_crypto_withdraw_reject_releases_reserve(client):
    buyer, buyer_token = register_and_login(client, "reject-buyer@test.local", "buyer")

    dep = client.post(
        "/api/v1/crypto/deposit-intent",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "reject-deposit-" + uuid.uuid4().hex},
        json={"asset": "USDT", "network": "TRC20"},
    )
    assert dep.status_code == 200, dep.text

    settled = client.post(
        "/api/v1/crypto/admin/provider/deposit-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={
            "reference": dep.json()["reference"],
            "provider_transaction_id": "reject-provider-deposit-" + uuid.uuid4().hex,
            "amount": "10",
            "tx_hash": "reject-deposit-tx-123",
        },
    )
    assert settled.status_code == 200, settled.text

    wd = client.post(
        "/api/v1/crypto/withdraw",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "reject-withdraw-" + uuid.uuid4().hex},
        json={"asset": "USDT", "network": "TRC20", "amount": "4",
              "destination": "TTestWalletDestinationReject123"},
    )
    assert wd.status_code == 200, wd.text
    reference = wd.json()["reference"]

    before = client.get(
        "/api/v1/crypto/wallet",
        params={"token": buyer_token, "asset": "USDT", "network": "TRC20"},
    ).json()
    assert before["balance"] == "10"
    assert before["held"] == "4"
    assert before["available"] == "6"

    rejected = client.post(
        "/api/v1/crypto/admin/provider/withdraw-reject",
        params={"reference": reference},
        headers={"X-Admin-Key": "ci-admin-key"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["reserve_released"] is True

    after = client.get(
        "/api/v1/crypto/wallet",
        params={"token": buyer_token, "asset": "USDT", "network": "TRC20"},
    ).json()
    assert after["balance"] == "10"
    assert after["held"] == "0"
    assert after["available"] == "10"

    tx = next(t for t in after["transactions"] if t["reference"] == reference)
    assert tx["status"] == "failed"


def test_crypto_wallet_api_full_flow(client):
    buyer, buyer_token = register_and_login(client, "api-buyer@test.local", "buyer")
    seller, seller_token = register_and_login(client, "api-seller@test.local", "seller")

    me = client.get("/api/v1/auth/me", params={"token": buyer_token})
    assert me.status_code == 200
    assert me.json()["id"] == buyer

    assets = client.get("/api/v1/crypto/assets")
    assert assets.status_code == 200
    assert {"USDT", "USDC", "BTC", "ETH"} <= {x["asset"] for x in assets.json()["assets"]}

    # Deposit intent is created first; no balance is credited until provider settlement.
    idem = "deposit-" + uuid.uuid4().hex
    dep = client.post(
        "/api/v1/crypto/deposit-intent",
        params={"token": buyer_token},
        headers={"Idempotency-Key": idem},
        json={"asset": "USDT", "network": "TRC20"},
    )
    assert dep.status_code == 200, dep.text
    reference = dep.json()["reference"]
    assert dep.json()["status"] == "pending"

    wallets = client.get("/api/v1/crypto/wallets", params={"token": buyer_token})
    assert wallets.status_code == 200
    assert any(w["asset"] == "USDT" and w["network"] == "TRC20" and w["balance"] == "0" for w in wallets.json()["wallets"])

    settled = client.post(
        "/api/v1/crypto/admin/provider/deposit-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={
            "reference": reference,
            "provider_transaction_id": "provider-deposit-1",
            "amount": "10",
            "tx_hash": "txhash-deposit-123",
        },
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["amount"] == "10"

    wallet = client.get(
        "/api/v1/crypto/wallet",
        params={"token": buyer_token, "asset": "USDT", "network": "TRC20"},
    )
    assert wallet.status_code == 200
    assert wallet.json()["balance"] == "10"
    assert wallet.json()["available"] == "10"

    # Withdrawal reserves first, then provider settlement consumes the reserve.
    widem = "withdraw-" + uuid.uuid4().hex
    wd = client.post(
        "/api/v1/crypto/withdraw",
        params={"token": buyer_token},
        headers={"Idempotency-Key": widem},
        json={
            "asset": "USDT", "network": "TRC20",
            "amount": "2", "destination": "TTestWalletDestination123",
        },
    )
    assert wd.status_code == 200, wd.text
    wref = wd.json()["reference"]
    wallet = client.get(
        "/api/v1/crypto/wallet",
        params={"token": buyer_token, "asset": "USDT", "network": "TRC20"},
    )
    assert wallet.json()["balance"] == "10"
    assert wallet.json()["held"] == "2"
    assert wallet.json()["available"] == "8"

    wsettle = client.post(
        "/api/v1/crypto/admin/provider/withdraw-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={
            "reference": wref,
            "provider_transaction_id": "provider-withdraw-1",
            "tx_hash": "txhash-withdraw-123",
        },
    )
    assert wsettle.status_code == 200, wsettle.text

    # Internal transfer is atomic and immediately visible in both wallets.
    transfer = client.post(
        "/api/v1/crypto/transfer",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "transfer-" + uuid.uuid4().hex},
        json={
            "to_account_id": seller,
            "asset": "USDT", "network": "TRC20",
            "amount": "3",
            "description": "API integration test",
        },
    )
    assert transfer.status_code == 200, transfer.text
    assert transfer.json()["status"] == "completed"

    buyer_wallet = client.get(
        "/api/v1/crypto/wallet",
        params={"token": buyer_token, "asset": "USDT", "network": "TRC20"},
    )
    seller_wallet = client.get(
        "/api/v1/crypto/wallet",
        params={"token": seller_token, "asset": "USDT", "network": "TRC20"},
    )
    assert buyer_wallet.json()["balance"] == "5"
    assert buyer_wallet.json()["held"] == "0"
    assert seller_wallet.json()["balance"] == "3"

    # Wallet ledger must reconcile to the stored balance after deposit,
    # withdrawal settlement, and internal transfer.
    db = app.db()
    buyer_ledger = db.execute(
        """SELECT COALESCE(SUM(delta_units),0) AS total
           FROM crypto_wallet_ledger l
           JOIN crypto_wallets w ON w.id=l.wallet_id
           WHERE w.account_id=? AND w.asset='USDT' AND w.network='TRC20'""",
        (buyer,),
    ).fetchone()["total"]
    seller_ledger = db.execute(
        """SELECT COALESCE(SUM(delta_units),0) AS total
           FROM crypto_wallet_ledger l
           JOIN crypto_wallets w ON w.id=l.wallet_id
           WHERE w.account_id=? AND w.asset='USDT' AND w.network='TRC20'""",
        (seller,),
    ).fetchone()["total"]
    db.close()
    assert int(buyer_ledger) == 5_000_000
    assert int(seller_ledger) == 3_000_000

    history = client.get("/api/v1/crypto/transactions", params={"token": buyer_token})
    assert history.status_code == 200
    types = {t["type"] for t in history.json()["transactions"]}
    assert {"deposit", "withdraw", "transfer_out"} <= types

    # Same provider transaction cannot be settled twice.
    duplicate = client.post(
        "/api/v1/crypto/admin/provider/deposit-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={
            "reference": reference,
            "provider_transaction_id": "provider-deposit-1",
            "amount": "10",
            "tx_hash": "txhash-deposit-123",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
