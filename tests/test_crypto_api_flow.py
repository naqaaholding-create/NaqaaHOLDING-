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


def test_crypto_idempotent_withdraw_and_transfer_do_not_double_apply(client):
    buyer, buyer_token = register_and_login(client, "idem-buyer@test.local", "buyer")
    seller, seller_token = register_and_login(client, "idem-seller@test.local", "seller")

    dep = client.post(
        "/api/v1/crypto/deposit-intent",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "idem-dep-" + uuid.uuid4().hex},
        json={"asset": "USDT", "network": "TRC20"},
    )
    assert dep.status_code == 200
    settle = client.post(
        "/api/v1/crypto/admin/provider/deposit-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={
            "reference": dep.json()["reference"],
            "provider_transaction_id": "idem-provider-deposit-" + uuid.uuid4().hex,
            "amount": "10",
            "tx_hash": "idem-deposit-tx",
        },
    )
    assert settle.status_code == 200

    withdraw_key = "idem-withdraw-" + uuid.uuid4().hex
    payload = {
        "asset": "USDT", "network": "TRC20", "amount": "4",
        "destination": "TIdempotentDestination123",
    }
    first = client.post("/api/v1/crypto/withdraw", params={"token": buyer_token},
                        headers={"Idempotency-Key": withdraw_key}, json=payload)
    second = client.post("/api/v1/crypto/withdraw", params={"token": buyer_token},
                         headers={"Idempotency-Key": withdraw_key}, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["reference"] == second.json()["reference"]
    assert second.json()["duplicate"] is True

    wallet = client.get("/api/v1/crypto/wallet",
                        params={"token": buyer_token, "asset": "USDT", "network": "TRC20"}).json()
    assert wallet["balance"] == "10"
    assert wallet["held"] == "4"
    assert wallet["available"] == "6"

    transfer_key = "idem-transfer-" + uuid.uuid4().hex
    transfer_payload = {
        "to_account_id": seller, "asset": "USDT", "network": "TRC20",
        "amount": "2", "description": "idempotency test",
    }
    t1 = client.post("/api/v1/crypto/transfer", params={"token": buyer_token},
                     headers={"Idempotency-Key": transfer_key}, json=transfer_payload)
    t2 = client.post("/api/v1/crypto/transfer", params={"token": buyer_token},
                     headers={"Idempotency-Key": transfer_key}, json=transfer_payload)
    assert t1.status_code == t2.status_code == 200
    assert t1.json()["reference"] == t2.json()["reference"]
    assert t2.json()["duplicate"] is True

    buyer_wallet = client.get("/api/v1/crypto/wallet",
                              params={"token": buyer_token, "asset": "USDT", "network": "TRC20"}).json()
    seller_wallet = client.get("/api/v1/crypto/wallet",
                               params={"token": seller_token, "asset": "USDT", "network": "TRC20"}).json()
    assert buyer_wallet["balance"] == "8"
    assert buyer_wallet["held"] == "4"
    assert buyer_wallet["available"] == "4"
    assert seller_wallet["balance"] == "2"


def test_crypto_concurrent_withdrawals_cannot_overspend(client):
    import concurrent.futures
    buyer, buyer_token = register_and_login(client, "concurrent-withdraw@test.local", "buyer")

    dep = client.post(
        "/api/v1/crypto/deposit-intent",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "conc-dep-" + uuid.uuid4().hex},
        json={"asset": "USDT", "network": "TRC20"},
    )
    assert dep.status_code == 200
    settled = client.post(
        "/api/v1/crypto/admin/provider/deposit-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={
            "reference": dep.json()["reference"],
            "provider_transaction_id": "conc-provider-deposit-" + uuid.uuid4().hex,
            "amount": "10",
            "tx_hash": "conc-deposit-tx",
        },
    )
    assert settled.status_code == 200

    def attempt(i):
        return client.post(
            "/api/v1/crypto/withdraw",
            params={"token": buyer_token},
            headers={"Idempotency-Key": f"conc-wd-{i}-{uuid.uuid4().hex}"},
            json={
                "asset": "USDT", "network": "TRC20", "amount": "2",
                "destination": f"TConcurrentDestination{i}",
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(attempt, range(8)))

    successful = [r for r in responses if r.status_code == 200]
    rejected = [r for r in responses if r.status_code == 400]
    assert len(successful) == 5
    assert len(rejected) == 3

    wallet = client.get(
        "/api/v1/crypto/wallet",
        params={"token": buyer_token, "asset": "USDT", "network": "TRC20"},
    )
    assert wallet.status_code == 200
    data = wallet.json()
    assert data["balance"] == "10"
    assert data["held"] == "10"
    assert data["available"] == "0"


def test_crypto_withdraw_settlement_is_idempotent_and_reject_after_settle_is_blocked(client):
    buyer, buyer_token = register_and_login(client, "settle-idem@test.local", "buyer")

    dep = client.post(
        "/api/v1/crypto/deposit-intent",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "settle-dep-" + uuid.uuid4().hex},
        json={"asset": "USDT", "network": "TRC20"},
    )
    assert dep.status_code == 200
    assert client.post(
        "/api/v1/crypto/admin/provider/deposit-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={
            "reference": dep.json()["reference"],
            "provider_transaction_id": "settle-provider-dep-" + uuid.uuid4().hex,
            "amount": "5",
            "tx_hash": "settle-deposit-tx",
        },
    ).status_code == 200

    wd = client.post(
        "/api/v1/crypto/withdraw",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "settle-wd-" + uuid.uuid4().hex},
        json={"asset": "USDT", "network": "TRC20", "amount": "2",
              "destination": "TSettlementDestination123"},
    )
    assert wd.status_code == 200
    reference = wd.json()["reference"]
    provider_id = "settle-provider-wd-" + uuid.uuid4().hex

    first = client.post(
        "/api/v1/crypto/admin/provider/withdraw-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={"reference": reference, "provider_transaction_id": provider_id,
              "tx_hash": "settle-withdraw-tx"},
    )
    assert first.status_code == 200
    assert first.json().get("duplicate") is not True

    wallet = client.get(
        "/api/v1/crypto/wallet",
        params={"token": buyer_token, "asset": "USDT", "network": "TRC20"},
    ).json()
    assert wallet["balance"] == "3"
    assert wallet["held"] == "0"
    assert wallet["available"] == "3"

    second = client.post(
        "/api/v1/crypto/admin/provider/withdraw-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={"reference": reference, "provider_transaction_id": provider_id,
              "tx_hash": "settle-withdraw-tx"},
    )
    assert second.status_code == 200
    assert second.json().get("duplicate") is True

    rejected = client.post(
        "/api/v1/crypto/admin/provider/withdraw-reject",
        params={"reference": reference},
        headers={"X-Admin-Key": "ci-admin-key"},
    )
    assert rejected.status_code in (400, 409)


def test_crypto_full_ledger_reconciliation_after_mixed_operations(client):
    buyer, buyer_token = register_and_login(client, "ledger-mixed-buyer@test.local", "buyer")
    seller, seller_token = register_and_login(client, "ledger-mixed-seller@test.local", "seller")

    def deposit(amount, tag):
        dep = client.post(
            "/api/v1/crypto/deposit-intent",
            params={"token": buyer_token},
            headers={"Idempotency-Key": f"{tag}-dep-" + uuid.uuid4().hex},
            json={"asset": "USDT", "network": "TRC20"},
        )
        assert dep.status_code == 200
        settled = client.post(
            "/api/v1/crypto/admin/provider/deposit-settle",
            headers={"X-Admin-Key": "ci-admin-key"},
            json={
                "reference": dep.json()["reference"],
                "provider_transaction_id": f"{tag}-provider-" + uuid.uuid4().hex,
                "amount": amount,
                "tx_hash": f"{tag}-tx",
            },
        )
        assert settled.status_code == 200

    deposit("20", "mixed")

    wd = client.post(
        "/api/v1/crypto/withdraw",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "mixed-wd-" + uuid.uuid4().hex},
        json={"asset": "USDT", "network": "TRC20", "amount": "3",
              "destination": "TMixedLedgerDestination"},
    )
    assert wd.status_code == 200
    settled_wd = client.post(
        "/api/v1/crypto/admin/provider/withdraw-settle",
        headers={"X-Admin-Key": "ci-admin-key"},
        json={"reference": wd.json()["reference"],
              "provider_transaction_id": "mixed-withdraw-provider-" + uuid.uuid4().hex,
              "tx_hash": "mixed-withdraw-tx"},
    )
    assert settled_wd.status_code == 200

    transfer = client.post(
        "/api/v1/crypto/transfer",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "mixed-transfer-" + uuid.uuid4().hex},
        json={"to_account_id": seller, "asset": "USDT", "network": "TRC20",
              "amount": "5", "description": "mixed ledger"},
    )
    assert transfer.status_code == 200

    # A rejected withdrawal must not alter balance and must leave no held amount.
    rejected_wd = client.post(
        "/api/v1/crypto/withdraw",
        params={"token": buyer_token},
        headers={"Idempotency-Key": "mixed-reject-" + uuid.uuid4().hex},
        json={"asset": "USDT", "network": "TRC20", "amount": "2",
              "destination": "TMixedRejectedDestination"},
    )
    assert rejected_wd.status_code == 200
    rejected = client.post(
        "/api/v1/crypto/admin/provider/withdraw-reject",
        params={"reference": rejected_wd.json()["reference"]},
        headers={"X-Admin-Key": "ci-admin-key"},
    )
    assert rejected.status_code == 200

    buyer_wallet = client.get(
        "/api/v1/crypto/wallet",
        params={"token": buyer_token, "asset": "USDT", "network": "TRC20"},
    ).json()
    seller_wallet = client.get(
        "/api/v1/crypto/wallet",
        params={"token": seller_token, "asset": "USDT", "network": "TRC20"},
    ).json()
    assert buyer_wallet["balance"] == "12"
    assert buyer_wallet["held"] == "0"
    assert buyer_wallet["available"] == "12"
    assert seller_wallet["balance"] == "5"

    db = app.db()
    rows = db.execute(
        """SELECT w.account_id, w.balance_units,
                  COALESCE(SUM(l.delta_units),0) AS ledger_units
           FROM crypto_wallets w
           LEFT JOIN crypto_wallet_ledger l ON l.wallet_id=w.id
           WHERE w.asset='USDT' AND w.network='TRC20'
             AND w.account_id IN (?,?)
           GROUP BY w.id""",
        (buyer, seller),
    ).fetchall()
    db.close()
    by_account = {row["account_id"]: row for row in rows}
    assert int(by_account[buyer]["balance_units"]) == int(by_account[buyer]["ledger_units"])
    assert int(by_account[seller]["balance_units"]) == int(by_account[seller]["ledger_units"])
